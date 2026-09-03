from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

import numpy as np

from daihougou.vision.frame_source import FrameSample
from daihougou.vision.object_detector import ObjectDetection
from daihougou.vision.person_detector import PersonDetection

STOP_TIMEOUT_SECONDS = 2.0


class DetectorKind(StrEnum):
    PERSON = "person"
    OBJECT = "object"


@dataclass(frozen=True)
class DetectorSnapshot:
    status: str
    loaded: bool
    fatal_error: bool


class PersonDetectorProtocol(Protocol):
    def detect(self, frame: np.ndarray) -> PersonDetection: ...


class ObjectDetectorProtocol(Protocol):
    def detect(self, frame: np.ndarray) -> ObjectDetection: ...


@dataclass(frozen=True)
class _DetectionJob:
    kind: DetectorKind
    camera_id: str
    sample: FrameSample
    future: asyncio.Future[PersonDetection | ObjectDetection]


class DetectionScheduler:
    stop_timeout_seconds = STOP_TIMEOUT_SECONDS

    def __init__(
        self,
        person_factory: Callable[[], PersonDetectorProtocol],
        object_factory: Callable[[], ObjectDetectorProtocol] | None = None,
    ) -> None:
        self._factories: dict[DetectorKind, Callable[[], object]] = {
            DetectorKind.PERSON: person_factory,
        }
        if object_factory is not None:
            self._factories[DetectorKind.OBJECT] = object_factory
        self._detectors: dict[DetectorKind, object] = {}
        self._statuses = {kind: "stopped" for kind in DetectorKind}
        self._fatal_errors = {kind: False for kind in DetectorKind}
        self._queue: asyncio.Queue[_DetectionJob | None] = asyncio.Queue()
        self._pending: dict[
            tuple[DetectorKind, str], asyncio.Future[PersonDetection | ObjectDetection]
        ] = {}
        self._task: asyncio.Task[None] | None = None
        self._replacement_lock = asyncio.Lock()
        self._inference_lock = asyncio.Lock()
        self.fatal_error = False

    @property
    def loaded(self) -> bool:
        return bool(self._detectors)

    @property
    def status(self) -> str:
        active_statuses = tuple(status for status in self._statuses.values() if status != "stopped")
        if any(status == "degraded" for status in active_statuses):
            return "degraded"
        if self.loaded:
            return "ready"
        return "stopped"

    async def start(self) -> None:
        await self.enable(DetectorKind.PERSON)

    async def enable(self, kind: DetectorKind) -> None:
        if kind not in self._factories:
            raise RuntimeError(f"detector_factory_missing:{kind.value}")
        if kind in self._detectors:
            return
        self._statuses[kind] = "starting"
        self._fatal_errors[kind] = False
        try:
            detector = await asyncio.to_thread(self._factories[kind])
        except Exception as error:
            self._detectors.pop(kind, None)
            self._statuses[kind] = "degraded"
            self._fatal_errors[kind] = True
            raise RuntimeError(f"detector_start_failed:{kind.value}") from error
        self._detectors[kind] = detector
        self._statuses[kind] = "ready"
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._consume(), name="detection-scheduler")
            self._task.add_done_callback(self._record_task_result)

    async def disable(self, kind: DetectorKind) -> None:
        self._fail_pending("detector_stopped", kind=kind)
        self._detectors.pop(kind, None)
        self._statuses[kind] = "stopped"
        self._fatal_errors[kind] = False
        if not self._detectors:
            await self._stop_consumer()

    async def replace_factory(
        self,
        kind: DetectorKind,
        factory: Callable[[], PersonDetectorProtocol | ObjectDetectorProtocol],
    ) -> None:
        async with self._replacement_lock:
            try:
                replacement = await asyncio.to_thread(factory)
            except Exception as error:
                raise RuntimeError(f"detector_replace_failed:{kind.value}") from error

            async with self._inference_lock:
                should_be_loaded = self._statuses[kind] != "stopped"
                self._factories[kind] = factory
                if not should_be_loaded:
                    return
                self._detectors[kind] = replacement
                self._statuses[kind] = "ready"
                self._fatal_errors[kind] = False
                if self._task is None or self._task.done():
                    self._task = asyncio.create_task(self._consume(), name="detection-scheduler")
                    self._task.add_done_callback(self._record_task_result)

    async def detect_person(self, camera_id: str, sample: FrameSample) -> PersonDetection:
        result = await self._detect(DetectorKind.PERSON, camera_id, sample)
        assert isinstance(result, PersonDetection)
        return result

    async def detect_objects(self, camera_id: str, sample: FrameSample) -> ObjectDetection:
        result = await self._detect(DetectorKind.OBJECT, camera_id, sample)
        assert isinstance(result, ObjectDetection)
        return result

    async def detect(self, camera_id: str, sample: FrameSample) -> PersonDetection:
        return await self.detect_person(camera_id, sample)

    def snapshot(self, kind: DetectorKind) -> DetectorSnapshot:
        return DetectorSnapshot(
            self._statuses[kind], kind in self._detectors, self._fatal_errors[kind]
        )

    async def stop(self) -> None:
        await self.close()

    async def close(self) -> None:
        self._fail_pending("detector_stopped")
        self._detectors.clear()
        await self._stop_consumer()
        for kind in DetectorKind:
            self._statuses[kind] = "stopped"
            self._fatal_errors[kind] = False
        self.fatal_error = False

    async def _detect(
        self, kind: DetectorKind, camera_id: str, sample: FrameSample
    ) -> PersonDetection | ObjectDetection:
        if kind not in self._detectors or self._task is None or self._task.done():
            raise RuntimeError("detector_stopped")
        key = (kind, camera_id)
        if key in self._pending:
            raise RuntimeError("detector_request_pending")
        future: asyncio.Future[PersonDetection | ObjectDetection] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[key] = future
        self._queue.put_nowait(_DetectionJob(kind, camera_id, sample, future))
        return await future

    async def _stop_consumer(self) -> None:
        task = self._task
        if task is not None and not task.done():
            self._queue.put_nowait(None)
            try:
                await asyncio.wait_for(task, timeout=self.stop_timeout_seconds)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        elif task is not None:
            await asyncio.gather(task, return_exceptions=True)
        self._task = None
        self._drain_queue()

    def _record_task_result(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            self.fatal_error = True

    async def _consume(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                if job is None:
                    return
                async with self._inference_lock:
                    detector = self._detectors.get(job.kind)
                    if detector is None:
                        raise RuntimeError("detector_stopped")
                    result = await asyncio.to_thread(detector.detect, job.sample.frame)
                if not job.future.done():
                    job.future.set_result(result)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - detector backends expose varied exceptions.
                if job is not None:
                    if not job.future.done():
                        job.future.set_exception(RuntimeError("detector_failed"))
                    self._statuses[job.kind] = "degraded"
                    self._fatal_errors[job.kind] = True
                    self._detectors.pop(job.kind, None)
                    self._fail_pending("detector_failed", kind=job.kind)
                if not self._detectors:
                    return
            finally:
                if job is not None:
                    self._pending.pop((job.kind, job.camera_id), None)
                self._queue.task_done()

    def _fail_pending(self, message: str, *, kind: DetectorKind | None = None) -> None:
        for key, future in tuple(self._pending.items()):
            if kind is not None and key[0] is not kind:
                continue
            if not future.done():
                future.set_exception(RuntimeError(message))
            self._pending.pop(key, None)

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._queue.task_done()
