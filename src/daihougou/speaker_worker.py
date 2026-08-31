from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Protocol

from daihougou.rules import SpeechAction
from daihougou.speaker import Speaker
from daihougou.storage import EventRecord


class WorkerStorage(Protocol):
    def camera_rule_enabled(self, camera_id: str, rule_id: str) -> bool: ...

    def camera_speaker_id(self, camera_id: str) -> str: ...

    def record_event(self, event: EventRecord) -> None: ...


class SpeakerManager:
    def __init__(
        self,
        speakers: Mapping[str, Speaker],
        storage: WorkerStorage,
        queue_size: int = 4,
    ) -> None:
        self._speakers = dict(speakers)
        self._storage = storage
        self._queues = {
            speaker_id: asyncio.Queue[SpeechAction | None](maxsize=queue_size)
            for speaker_id in self._speakers
        }
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self.auth_status = "unknown"
        self.fatal_error = False
        self.speaker_statuses = {speaker_id: "unknown" for speaker_id in self._speakers}

    async def start(self) -> None:
        if self._tasks:
            return
        self.fatal_error = False
        for speaker_id, speaker in self._speakers.items():
            task = asyncio.create_task(
                self._consume(speaker_id, speaker),
                name=f"speaker-worker-{speaker_id}",
            )
            task.add_done_callback(self._record_task_result)
            self._tasks[speaker_id] = task

    def submit(self, action: SpeechAction) -> bool:
        if self.auth_status == "reauth_required" or self.fatal_error:
            return False
        queue = self._queues.get(action.speaker_id)
        if queue is None:
            self._record(action, "speaker_unknown", False)
            return False
        try:
            queue.put_nowait(action)
            return True
        except asyncio.QueueFull:
            self._record(action, "speaker_queue_full", False)
            return False

    async def join(self) -> None:
        await asyncio.gather(*(queue.join() for queue in self._queues.values()))

    async def stop(self) -> None:
        if not self._tasks:
            return
        tasks = tuple(self._tasks.values())
        for speaker_id, task in self._tasks.items():
            if not task.done():
                await self._queues[speaker_id].put(None)
        await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    def _record_task_result(self, task: asyncio.Task[None]) -> None:
        if not task.cancelled() and task.exception() is not None:
            self.fatal_error = True

    async def _consume(self, speaker_id: str, speaker: Speaker) -> None:
        queue = self._queues[speaker_id]
        while True:
            action = await queue.get()
            try:
                if action is None:
                    return
                if self.auth_status == "reauth_required":
                    self._record(action, "speaker_reauth_required", False)
                    continue
                if not self._storage.camera_rule_enabled(
                    action.camera_id, action.rule_id
                ):
                    self._record(action, "speaker_skipped_disabled", False)
                    continue
                if self._storage.camera_speaker_id(action.camera_id) != action.speaker_id:
                    self._record(action, "speaker_skipped_pairing_changed", False)
                    continue

                result = await asyncio.to_thread(speaker.speak, action.text)
                if result.code == "reauth_required":
                    self.auth_status = "reauth_required"
                    self.speaker_statuses[speaker_id] = "reauth_required"
                    self._record(
                        action,
                        "speaker_reauth_required",
                        False,
                        latency_ms=result.latency_ms,
                        details={"code": result.code},
                    )
                    continue

                if self.auth_status != "reauth_required":
                    self.auth_status = "ready"
                self.speaker_statuses[speaker_id] = (
                    "ready" if result.success else "degraded"
                )
                self._record(
                    action,
                    "speaker_completed",
                    result.success,
                    latency_ms=result.latency_ms,
                    details={"code": result.code},
                )
            finally:
                queue.task_done()

    def _record(
        self,
        action: SpeechAction,
        kind: str,
        success: bool,
        *,
        latency_ms: int | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        self._storage.record_event(
            EventRecord(
                kind,
                success,
                rule_id=action.rule_id,
                camera_id=action.camera_id,
                speaker_id=action.speaker_id,
                latency_ms=latency_ms,
                details=details or {},
            )
        )
