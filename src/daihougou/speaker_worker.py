from __future__ import annotations

import asyncio
import time
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
        clock: callable = time.monotonic,
    ) -> None:
        self._speakers = dict(speakers)
        self._storage = storage
        self._queues = {
            speaker_id: asyncio.Queue[SpeechAction | tuple[str, str] | None](maxsize=queue_size)
            for speaker_id in self._speakers
        }
        self._pending_actions: dict[str, dict[tuple[str, str], SpeechAction]] = {
            speaker_id: {} for speaker_id in self._speakers
        }
        self._clock = clock
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
        key = action.coalesce_key
        pending = self._pending_actions[action.speaker_id]
        if key is not None and key in pending:
            previous = pending[key]
            pending[key] = action
            if previous.superseded_event_kind is not None:
                self._record(
                    previous,
                    previous.superseded_event_kind,
                    False,
                    details=previous.details,
                )
            return True
        try:
            queue.put_nowait(key if key is not None else action)
            if key is not None:
                pending[key] = action
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
            entry = await queue.get()
            try:
                if entry is None:
                    return
                if isinstance(entry, tuple):
                    action = self._pending_actions[speaker_id].pop(entry, None)
                    if action is None:
                        continue
                else:
                    action = entry
                if self.auth_status == "reauth_required":
                    self._record(action, "speaker_reauth_required", False)
                    continue
                if (
                    action.max_queue_age_seconds is not None
                    and self._clock() - action.created_monotonic
                    > action.max_queue_age_seconds
                ):
                    self._record(action, "speaker_skipped_expired", False)
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
                    action.completion_event_kind,
                    result.success,
                    latency_ms=result.latency_ms,
                    details={**action.details, "code": result.code},
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
        event_details = dict(details or {})
        if kind in {"speaker_completed", "object_announcement_completed"}:
            event_details["text"] = action.text
        self._storage.record_event(
            EventRecord(
                kind,
                success,
                rule_id=action.rule_id,
                camera_id=action.camera_id,
                speaker_id=action.speaker_id,
                latency_ms=latency_ms,
                details=event_details,
            )
        )
