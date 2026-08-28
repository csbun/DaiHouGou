import asyncio
from typing import Protocol

from daihougou.rules import SpeechAction
from daihougou.speaker import Speaker
from daihougou.storage import EventRecord


class WorkerStorage(Protocol):
    def rule_enabled(self, rule_id: str) -> bool: ...

    def record_event(self, event: EventRecord) -> None: ...


class SpeakerWorker:
    def __init__(self, speaker: Speaker, storage: WorkerStorage, queue_size: int = 4) -> None:
        self._speaker = speaker
        self._storage = storage
        self._queue: asyncio.Queue[SpeechAction | None] = asyncio.Queue(maxsize=queue_size)
        self._task: asyncio.Task[None] | None = None
        self.auth_status = "unknown"
        self.fatal_error = False

    async def start(self) -> None:
        if self._task is None:
            self.fatal_error = False
            self._task = asyncio.create_task(self._consume(), name="speaker-worker")
            self._task.add_done_callback(self._record_task_result)

    def submit(self, action: SpeechAction) -> bool:
        if self.auth_status == "reauth_required" or self.fatal_error:
            return False
        try:
            self._queue.put_nowait(action)
            return True
        except asyncio.QueueFull:
            self._storage.record_event(
                EventRecord("speaker_queue_full", False, rule_id=action.rule_id)
            )
            return False

    async def join(self) -> None:
        await self._queue.join()

    async def stop(self) -> None:
        if self._task is None:
            return
        task = self._task
        if not task.done():
            await self._queue.put(None)
        await asyncio.gather(task, return_exceptions=True)
        self._task = None

    def _record_task_result(self, task: asyncio.Task[None]) -> None:
        if task.cancelled():
            return
        self.fatal_error = task.exception() is not None

    async def _consume(self) -> None:
        while True:
            action = await self._queue.get()
            try:
                if action is None:
                    return
                if not self._storage.rule_enabled(action.rule_id):
                    self._storage.record_event(
                        EventRecord("speaker_skipped_disabled", False, rule_id=action.rule_id)
                    )
                    continue
                if self.auth_status == "reauth_required":
                    self._storage.record_event(
                        EventRecord("speaker_reauth_required", False, rule_id=action.rule_id)
                    )
                    continue
                result = await asyncio.to_thread(self._speaker.speak, action.text)
                self.auth_status = (
                    "reauth_required" if result.code == "reauth_required" else "ready"
                )
                self._storage.record_event(
                    EventRecord(
                        "speaker_completed",
                        result.success,
                        rule_id=action.rule_id,
                        latency_ms=result.latency_ms,
                        details={"code": result.code},
                    )
                )
            finally:
                self._queue.task_done()
