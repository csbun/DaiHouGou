from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from daihougou.vision.frame_source import FrameSample
from daihougou.vision.person_detector import PersonDetection

STOP_TIMEOUT_SECONDS = 2.0


class Detector(Protocol):
    def detect(self, frame: np.ndarray) -> PersonDetection: ...


@dataclass(frozen=True)
class _DetectionJob:
    camera_id: str
    sample: FrameSample
    future: asyncio.Future[PersonDetection]


class DetectionScheduler:
    def __init__(self, detector_factory: Callable[[], Detector]) -> None:
        self._detector_factory = detector_factory
        self._detector: Detector | None = None
        self._queue: asyncio.Queue[_DetectionJob | None] = asyncio.Queue()
        self._pending: dict[str, asyncio.Future[PersonDetection]] = {}
        self._task: asyncio.Task[None] | None = None
        self.status = "stopped"
        self.fatal_error = False

    @property
    def loaded(self) -> bool:
        return self._detector is not None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self.status = "starting"
        self.fatal_error = False
        try:
            self._detector = await asyncio.to_thread(self._detector_factory)
        except Exception as error:
            self._detector = None
            self.status = "degraded"
            self.fatal_error = True
            raise RuntimeError("detector_start_failed") from error
        self.status = "ready"
        self._task = asyncio.create_task(self._consume(), name="detection-scheduler")
        self._task.add_done_callback(self._record_task_result)

    async def detect(self, camera_id: str, sample: FrameSample) -> PersonDetection:
        if self._detector is None or self._task is None or self._task.done():
            raise RuntimeError("detector_stopped")
        if camera_id in self._pending:
            raise RuntimeError("detector_request_pending")
        future: asyncio.Future[PersonDetection] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[camera_id] = future
        self._queue.put_nowait(_DetectionJob(camera_id, sample, future))
        return await future

    async def stop(self) -> None:
        task = self._task
        if task is not None and not task.done():
            self._queue.put_nowait(None)
            try:
                await asyncio.wait_for(task, timeout=STOP_TIMEOUT_SECONDS)
            except TimeoutError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        elif task is not None:
            await asyncio.gather(task, return_exceptions=True)

        self._fail_pending("detector_stopped")
        self._drain_queue()
        self._task = None
        self._detector = None
        self.status = "stopped"

    def _record_task_result(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            self.status = "degraded"
            self.fatal_error = True

    async def _consume(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                if job is None:
                    return
                detector = self._detector
                if detector is None:
                    raise RuntimeError("detector_stopped")
                detection = await asyncio.to_thread(detector.detect, job.sample.frame)
                if not job.future.done():
                    job.future.set_result(detection)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self.status = "degraded"
                self.fatal_error = True
                self._fail_pending("detector_failed")
                self._drain_queue()
                raise RuntimeError("detector_failed") from error
            finally:
                if job is not None:
                    pending = self._pending.get(job.camera_id)
                    if pending is job.future:
                        self._pending.pop(job.camera_id, None)
                self._queue.task_done()

    def _fail_pending(self, message: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError(message))
        self._pending.clear()

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            self._queue.task_done()
