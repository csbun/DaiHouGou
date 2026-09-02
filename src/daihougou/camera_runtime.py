from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from daihougou.object_rule import ObjectCategoryAnnouncementRule
from daihougou.presence import PresenceTracker
from daihougou.rules import SpeechAction, WelcomeRule
from daihougou.storage import EventRecord
from daihougou.vision.frame_source import FrameSample
from daihougou.vision.object_detector import ObjectDetection
from daihougou.vision.person_detector import PersonDetection
from daihougou.vision.scene_change import SceneChangeGate

DETECTION_STOP_TIMEOUT_SECONDS = 5.0
FRAME_WAIT_SECONDS = 2.0
CAMERA_OUTAGE_SECONDS = 30.0


class FrameSource(Protocol):
    def start(self) -> None: ...

    def wait_for_frame(self, after_sequence: int, timeout: float) -> FrameSample | None: ...

    def stop(self) -> None: ...


class Scheduler(Protocol):
    async def detect_person(self, camera_id: str, sample: FrameSample) -> PersonDetection: ...

    async def detect_objects(self, camera_id: str, sample: FrameSample) -> ObjectDetection: ...


class ActionSink(Protocol):
    def submit(self, action: SpeechAction) -> bool: ...


class CameraStorage(Protocol):
    def record_event(self, event: EventRecord) -> None: ...


@dataclass(frozen=True)
class PipelineSnapshot:
    status: str
    last_confidence: float | None
    last_detection_latency_ms: int | None
    last_error: str


@dataclass(frozen=True)
class CameraSnapshot:
    stream_id: str
    stream: str
    presence: str
    last_sequence: int
    person: PipelineSnapshot
    object: PipelineSnapshot
    last_error: str

    @property
    def detector(self) -> str:
        return self.person.status

    @property
    def last_confidence(self) -> float | None:
        return self.person.last_confidence

    @property
    def last_detection_latency_ms(self) -> int | None:
        return self.person.last_detection_latency_ms

class CameraRuntime:
    def __init__(
        self,
        stream_id: str,
        frame_source: FrameSource,
        scheduler: Scheduler,
        presence: PresenceTracker,
        welcome_rule: WelcomeRule | None,
        speaker_manager: ActionSink,
        storage: CameraStorage,
        *,
        object_rule: ObjectCategoryAnnouncementRule | None = None,
        scene_gate: SceneChangeGate | None = None,
        clock: Callable[[], float] = time.monotonic,
        suppress_initial_object_detection: bool = False,
    ) -> None:
        self._stream_id = stream_id
        self._frame_source = frame_source
        self._scheduler = scheduler
        self._presence = presence
        self._welcome_rule = welcome_rule
        self._object_rule = object_rule
        self._scene_gate = scene_gate or SceneChangeGate()
        self._speaker_manager = speaker_manager
        self._storage = storage
        self._clock = clock
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._started_at: float | None = None
        self._last_frame_at: float | None = None
        self._last_sequence = 0
        self._stream_status = "starting"
        self._last_error = ""
        self._camera_error_recorded = False
        self._person = PipelineSnapshot(
            "starting" if welcome_rule else "stopped", None, None, ""
        )
        self._object = PipelineSnapshot(
            "starting" if object_rule else "stopped", None, None, ""
        )
        self._object_failed = False
        self._suppress_initial_object_detection = suppress_initial_object_detection

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
        if background_stopped:
            person = PipelineSnapshot(
                "degraded" if self._welcome_rule else "stopped",
                self._person.last_confidence,
                self._person.last_detection_latency_ms,
                "background_task_stopped" if self._welcome_rule else "",
            )
            object_pipeline = PipelineSnapshot(
                "degraded" if self._object_rule else "stopped",
                self._object.last_confidence,
                self._object.last_detection_latency_ms,
                "background_task_stopped" if self._object_rule else "",
            )
        else:
            person, object_pipeline = self._person, self._object
        return CameraSnapshot(
            stream_id=self._stream_id,
            stream=self._stream_status,
            presence=self._presence.state.value,
            last_sequence=self._last_sequence,
            person=person,
            object=object_pipeline,
            last_error=(
                "background_task_stopped"
                if background_stopped
                else self._last_error or person.last_error or object_pipeline.last_error
            ),
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
        self._scene_gate.invalidate()
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
        self._last_error = ""
        self._camera_error_recorded = False
        jobs: list[tuple[str, asyncio.Future[PersonDetection | ObjectDetection]]] = []
        if self._welcome_rule is not None:
            jobs.append(
                (
                    "person",
                    asyncio.create_task(
                        self._detect_person(sample), name=f"person-{self._stream_id}"
                    ),
                )
            )
        if self._object_rule is not None and not self._object_failed:
            scene_changed = self._scene_gate.changed(sample.frame)
            if self._suppress_initial_object_detection:
                self._suppress_initial_object_detection = False
            elif scene_changed:
                jobs.append(
                    (
                        "object",
                        asyncio.create_task(
                            self._detect_objects(sample), name=f"object-{self._stream_id}"
                        ),
                    )
                )
        if not jobs:
            return
        results = await asyncio.gather(*(future for _, future in jobs), return_exceptions=True)
        for (kind, _), result in zip(jobs, results, strict=True):
            if isinstance(result, Exception):
                self._handle_detection_failure(kind)
            elif kind == "person":
                self._handle_person_result(result, sample)
            else:
                self._handle_object_result(result, sample)

    async def _detect_person(self, sample: FrameSample) -> PersonDetection:
        detect_person = getattr(self._scheduler, "detect_person", None)
        if detect_person is None:
            return await self._scheduler.detect(self._stream_id, sample)  # type: ignore[attr-defined]
        return await detect_person(self._stream_id, sample)

    async def _detect_objects(self, sample: FrameSample) -> ObjectDetection:
        return await self._scheduler.detect_objects(self._stream_id, sample)

    def _handle_detection_failure(self, kind: str) -> None:
        if kind == "person":
            self._presence.invalidate()
            self._person = PipelineSnapshot("degraded", self._person.last_confidence, self._person.last_detection_latency_ms, "detector_failed")
            self._last_error = "detector_failed"
            self._storage.record_event(EventRecord("detector_failed", False, camera_id=self._stream_id))
        else:
            self._object_failed = True
            self._object = PipelineSnapshot("degraded", self._object.last_confidence, self._object.last_detection_latency_ms, "detector_failed")
            self._last_error = "detector_failed"
            self._storage.record_event(EventRecord("object_detector_failed", False, camera_id=self._stream_id))

    def _handle_person_result(self, result: PersonDetection, sample: FrameSample) -> None:
        self._person = PipelineSnapshot("ready", result.confidence, result.latency_ms, "")
        previous = self._presence.state
        event = self._presence.observe(sample.captured_monotonic, result.person_present)
        if self._presence.state is not previous:
            self._storage.record_event(
                EventRecord(
                    "presence_changed", True, camera_id=self._stream_id,
                    details={"from": previous.value, "to": self._presence.state.value, "confidence": round(result.confidence, 4)},
                )
            )
        if event is not None and self._welcome_rule is not None:
            action = self._welcome_rule.handle(event)
            if action is not None:
                self._speaker_manager.submit(action)

    def _handle_object_result(self, result: ObjectDetection, sample: FrameSample) -> None:
        confidence = max((item.confidence for item in result.objects), default=0.0)
        self._object = PipelineSnapshot("ready", confidence, result.latency_ms, "")
        if self._object_rule is None:
            return
        height, width = sample.frame.shape[:2]
        action = self._object_rule.handle(
            result, occurred_monotonic=sample.captured_monotonic, frame_size=(width, height)
        )
        if action is not None:
            self._speaker_manager.submit(action)
