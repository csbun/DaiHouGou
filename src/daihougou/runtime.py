import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from daihougou.presence import PresenceTracker
from daihougou.rules import SpeechAction, WelcomeRule
from daihougou.storage import EventRecord, Storage
from daihougou.vision.frame_source import FrameSample
from daihougou.vision.person_detector import PersonDetection

DETECTION_STOP_TIMEOUT_SECONDS = 5
SPEAKER_STOP_TIMEOUT_SECONDS = 35


class FrameSource(Protocol):
    def start(self) -> None: ...

    def wait_for_frame(self, after_sequence: int, timeout: float) -> FrameSample | None: ...

    def stop(self) -> None: ...


class Detector(Protocol):
    def detect(self, frame: npt.NDArray[np.uint8]) -> PersonDetection: ...


class ManagedSpeakerWorker(Protocol):
    auth_status: str
    fatal_error: bool

    async def start(self) -> None: ...

    def submit(self, action: SpeechAction) -> bool: ...

    async def stop(self) -> None: ...


@dataclass(frozen=True)
class RuntimeSnapshot:
    app: str
    database: str
    camera: str
    detector: str
    speaker_auth: str
    presence: str
    last_sequence: int
    last_confidence: float | None
    last_detection_latency_ms: int | None
    last_error: str

    @property
    def overall(self) -> str:
        if "unhealthy" in (self.app, self.database, self.camera, self.detector):
            return "unhealthy"
        required = (self.app, self.database, self.camera, self.detector)
        return "ready" if required == ("running", "ready", "ready", "ready") else "degraded"


class Runtime:
    def __init__(
        self,
        frame_source: FrameSource,
        detector: Detector,
        presence: PresenceTracker,
        welcome_rule: WelcomeRule,
        speaker_worker: ManagedSpeakerWorker,
        storage: Storage,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._frame_source = frame_source
        self._detector = detector
        self._presence = presence
        self._welcome_rule = welcome_rule
        self._speaker_worker = speaker_worker
        self._storage = storage
        self._clock = clock
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._started_at: float | None = None
        self._last_frame_at: float | None = None
        self._last_sequence = 0
        self._last_confidence: float | None = None
        self._last_detection_latency_ms: int | None = None
        self._camera_status = "starting"
        self._camera_error_recorded = False
        self._detector_status = "starting"
        self._database_status = "starting"
        self._app_status = "stopped"
        self._last_error = ""

    async def start(self) -> None:
        if self._task is not None:
            return
        self._storage.initialize()
        self._database_status = "ready"
        await self._speaker_worker.start()
        await asyncio.to_thread(self._frame_source.start)
        self._stop.clear()
        self._started_at = self._clock()
        self._app_status = "running"
        self._task = asyncio.create_task(self._detect_loop(), name="person-detection-loop")

    async def stop(self) -> None:
        self._stop.set()
        await asyncio.to_thread(self._frame_source.stop)
        if self._task is not None:
            done, pending = await asyncio.wait(
                {self._task}, timeout=DETECTION_STOP_TIMEOUT_SECONDS
            )
            if pending:
                self._task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
            self._task = None
        try:
            await asyncio.wait_for(
                self._speaker_worker.stop(), timeout=SPEAKER_STOP_TIMEOUT_SECONDS
            )
        except TimeoutError:
            self._last_error = "speaker_stop_timeout"
        self._app_status = "stopped"

    def snapshot(self) -> RuntimeSnapshot:
        detection_stopped = (
            self._task is not None and self._task.done() and not self._stop.is_set()
        )
        speaker_failed = bool(getattr(self._speaker_worker, "fatal_error", False))
        unhealthy = detection_stopped or speaker_failed
        return RuntimeSnapshot(
            app="unhealthy" if unhealthy else self._app_status,
            database=self._database_status,
            camera=self._camera_status,
            detector=self._detector_status,
            speaker_auth=self._speaker_worker.auth_status,
            presence=self._presence.state.value,
            last_sequence=self._last_sequence,
            last_confidence=self._last_confidence,
            last_detection_latency_ms=self._last_detection_latency_ms,
            last_error="background_task_stopped" if unhealthy else self._last_error,
        )

    async def _detect_loop(self) -> None:
        while not self._stop.is_set():
            sample = await asyncio.to_thread(
                self._frame_source.wait_for_frame, self._last_sequence, 2.0
            )
            if sample is None:
                self._presence.invalidate()
                reference = (
                    self._last_frame_at
                    if self._last_frame_at is not None
                    else self._started_at
                )
                if reference is not None and self._clock() - reference >= 30:
                    self._camera_status = "degraded"
                    self._last_error = "camera_no_frames"
                    if not self._camera_error_recorded:
                        self._storage.record_event(EventRecord("camera_no_frames", False))
                        self._camera_error_recorded = True
                continue
            await self._process_sample(sample)

    async def _process_sample(self, sample: FrameSample) -> None:
        self._last_sequence = sample.sequence
        self._last_frame_at = self._clock()
        self._camera_status = "ready"
        self._camera_error_recorded = False
        try:
            result = await asyncio.to_thread(self._detector.detect, sample.frame)
        except Exception:  # noqa: BLE001 - detector backends expose varied exceptions.
            self._presence.invalidate()
            self._detector_status = "degraded"
            self._last_error = "detector_failed"
            self._storage.record_event(EventRecord("detector_failed", False))
            return
        self._detector_status = "ready"
        self._last_error = ""
        self._last_confidence = result.confidence
        self._last_detection_latency_ms = result.latency_ms
        previous = self._presence.state
        event = self._presence.observe(sample.captured_monotonic, result.person_present)
        if self._presence.state is not previous:
            self._storage.record_event(
                EventRecord(
                    "presence_changed",
                    True,
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
                self._speaker_worker.submit(action)
