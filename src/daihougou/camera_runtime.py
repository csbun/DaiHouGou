from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from daihougou.presence import PresenceTracker
from daihougou.rules import SpeechAction, WelcomeRule
from daihougou.storage import EventRecord
from daihougou.vision.frame_source import FrameSample
from daihougou.vision.person_detector import PersonDetection

DETECTION_STOP_TIMEOUT_SECONDS = 5.0
FRAME_WAIT_SECONDS = 2.0
CAMERA_OUTAGE_SECONDS = 30.0


class FrameSource(Protocol):
    def start(self) -> None: ...

    def wait_for_frame(self, after_sequence: int, timeout: float) -> FrameSample | None: ...

    def stop(self) -> None: ...


class Scheduler(Protocol):
    async def detect(self, camera_id: str, sample: FrameSample) -> PersonDetection: ...


class ActionSink(Protocol):
    def submit(self, action: SpeechAction) -> bool: ...


class CameraStorage(Protocol):
    def record_event(self, event: EventRecord) -> None: ...


@dataclass(frozen=True)
class CameraSnapshot:
    stream_id: str
    stream: str
    detector: str
    presence: str
    last_sequence: int
    last_confidence: float | None
    last_detection_latency_ms: int | None
    last_error: str


class CameraRuntime:
    def __init__(
        self,
        stream_id: str,
        frame_source: FrameSource,
        scheduler: Scheduler,
        presence: PresenceTracker,
        welcome_rule: WelcomeRule,
        speaker_manager: ActionSink,
        storage: CameraStorage,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._stream_id = stream_id
        self._frame_source = frame_source
        self._scheduler = scheduler
        self._presence = presence
        self._welcome_rule = welcome_rule
        self._speaker_manager = speaker_manager
        self._storage = storage
        self._clock = clock
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._started_at: float | None = None
        self._last_frame_at: float | None = None
        self._last_sequence = 0
        self._last_confidence: float | None = None
        self._last_detection_latency_ms: int | None = None
        self._stream_status = "starting"
        self._detector_status = "starting"
        self._camera_error_recorded = False
        self._last_error = ""

    async def start(self) -> None:
        if self._task is not None:
            return
        await asyncio.to_thread(self._frame_source.start)
        self._stop.clear()
        self._started_at = self._clock()
        self._task = asyncio.create_task(
            self._detect_loop(), name=f"camera-runtime-{self._stream_id}"
        )

    async def stop(self) -> None:
        self._stop.set()
        await asyncio.to_thread(self._frame_source.stop)
        task = self._task
        if task is not None:
            done, pending = await asyncio.wait(
                {task}, timeout=DETECTION_STOP_TIMEOUT_SECONDS
            )
            if pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
            self._task = None

    def snapshot(self) -> CameraSnapshot:
        background_stopped = (
            self._task is not None and self._task.done() and not self._stop.is_set()
        )
        return CameraSnapshot(
            stream_id=self._stream_id,
            stream=self._stream_status,
            detector="degraded" if background_stopped else self._detector_status,
            presence=self._presence.state.value,
            last_sequence=self._last_sequence,
            last_confidence=self._last_confidence,
            last_detection_latency_ms=self._last_detection_latency_ms,
            last_error="background_task_stopped" if background_stopped else self._last_error,
        )

    async def _detect_loop(self) -> None:
        while not self._stop.is_set():
            sample = await asyncio.to_thread(
                self._frame_source.wait_for_frame,
                self._last_sequence,
                FRAME_WAIT_SECONDS,
            )
            if sample is None:
                self._handle_missing_frame()
                continue
            await self._process_sample(sample)

    def _handle_missing_frame(self) -> None:
        self._presence.invalidate()
        reference = (
            self._last_frame_at if self._last_frame_at is not None else self._started_at
        )
        if reference is None or self._clock() - reference < CAMERA_OUTAGE_SECONDS:
            return
        self._stream_status = "degraded"
        self._last_error = "camera_no_frames"
        if not self._camera_error_recorded:
            self._storage.record_event(
                EventRecord("camera_no_frames", False, camera_id=self._stream_id)
            )
            self._camera_error_recorded = True

    async def _process_sample(self, sample: FrameSample) -> None:
        self._last_sequence = sample.sequence
        self._last_frame_at = self._clock()
        self._stream_status = "ready"
        self._camera_error_recorded = False
        try:
            result = await self._scheduler.detect(self._stream_id, sample)
        except Exception:  # noqa: BLE001 - detector backends expose varied exceptions.
            self._presence.invalidate()
            self._detector_status = "degraded"
            self._last_error = "detector_failed"
            self._storage.record_event(
                EventRecord("detector_failed", False, camera_id=self._stream_id)
            )
            return

        self._detector_status = "ready"
        self._last_error = ""
        self._last_confidence = result.confidence
        self._last_detection_latency_ms = result.latency_ms
        previous = self._presence.state
        event = self._presence.observe(
            sample.captured_monotonic, result.person_present
        )
        if self._presence.state is not previous:
            self._storage.record_event(
                EventRecord(
                    "presence_changed",
                    True,
                    camera_id=self._stream_id,
                    details={
                        "from": previous.value,
                        "to": self._presence.state.value,
                        "confidence": round(result.confidence, 4),
                    },
                )
            )
        if event is not None:
            action = self._welcome_rule.handle(event)
            if action is not None:
                self._speaker_manager.submit(action)
