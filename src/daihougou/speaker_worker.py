import asyncio
from typing import Protocol

from daihougou.rules import SpeechAction
from daihougou.speaker import SpeakResult
from daihougou.storage import EventRecord


class _Storage(Protocol):
    def rule_enabled(self, rule_id: str) -> bool: ...

    def record_event(self, event: EventRecord) -> None: ...


class _Speaker(Protocol):
    def speak(self, text: str) -> SpeakResult: ...


class SpeakerWorker:
    def __init__(self, speaker: _Speaker, storage: _Storage, maxsize: int = 4) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be greater than 0")
        self._speaker = speaker
        self._storage = storage
        self._queue: asyncio.Queue[SpeechAction | None] = asyncio.Queue(maxsize=maxsize)
        self._task: asyncio.Task[None] | None = None
        self._auth_status = "unknown"

    @property
    def auth_status(self) -> str:
        return self._auth_status

    @property
    def queue(self) -> asyncio.Queue[SpeechAction | None]:
        return self._queue

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._consume())

    def submit(self, action: SpeechAction) -> bool:
        if self._auth_status == "reauth_required":
            return False
        try:
            self._queue.put_nowait(action)
        except asyncio.QueueFull:
            self._record(
                EventRecord(
                    kind="speaker_queue_full",
                    success=False,
                    rule_id=action.rule_id,
                )
            )
            return False
        return True

    async def join(self) -> None:
        if self._task is not None:
            await self._queue.join()

    async def stop(self) -> None:
        if self._task is None:
            return
        await self._queue.join()
        await self._queue.put(None)
        await self._task
        self._task = None

    async def _consume(self) -> None:
        while True:
            action = await self._queue.get()
            try:
                if action is None:
                    return
                if self._auth_status == "reauth_required":
                    self._record(
                        EventRecord(
                            kind="speaker_skipped_reauth",
                            success=False,
                            rule_id=action.rule_id,
                        )
                    )
                    continue
                if not self._storage.rule_enabled(action.rule_id):
                    self._record(
                        EventRecord(
                            kind="speaker_skipped_disabled",
                            success=False,
                            rule_id=action.rule_id,
                        )
                    )
                    continue
                result = await asyncio.to_thread(self._speaker.speak, action.text)
                self._record_result(action, result)
            finally:
                self._queue.task_done()

    def _record_result(self, action: SpeechAction, result: SpeakResult) -> None:
        if result.code == "reauth_required" or result.error == "reauth_required":
            self._auth_status = "reauth_required"
            kind = "speaker_reauth_required"
        elif result.success:
            self._auth_status = "authenticated"
            kind = "speaker_success"
        else:
            kind = "speaker_failed"
        self._record(
            EventRecord(
                kind=kind,
                success=result.success,
                rule_id=action.rule_id,
                latency_ms=result.latency_ms,
                details={"code": result.code} if kind != "speaker_success" else {},
            )
        )

    def _record(self, event: EventRecord) -> None:
        self._storage.record_event(event)


AsyncSpeakerWorker = SpeakerWorker
