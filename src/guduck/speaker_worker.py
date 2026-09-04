from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from typing import Protocol

from guduck.rules import SpeechAction
from guduck.speaker import Speaker
from guduck.storage import EventRecord


class WorkerStorage(Protocol):
    def camera_rule_enabled(self, camera_id: str, rule_id: str) -> bool: ...

    def camera_speaker_id(self, camera_id: str) -> str | None: ...

    def record_event(self, event: EventRecord) -> None: ...


AuthCallback = Callable[[], Awaitable[None] | None]
QueueEntry = SpeechAction | tuple[str, str] | None


class SpeakerManager:
    def __init__(
        self,
        speakers: Mapping[str, Speaker],
        storage: WorkerStorage,
        queue_size: int = 4,
        clock: Callable[[], float] = time.monotonic,
        on_auth_required: AuthCallback | None = None,
    ) -> None:
        self._storage = storage
        self._queue_size = queue_size
        self._clock = clock
        self._on_auth_required = on_auth_required
        self._speakers: dict[str, Speaker] = {}
        self._queues: dict[str, asyncio.Queue[QueueEntry]] = {}
        self._pending_actions: dict[str, dict[tuple[str, str], SpeechAction]] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._available_ids: frozenset[str] = frozenset()
        self._replace_lock = asyncio.Lock()
        self._started = False
        self._replacing = False
        self.auth_status = "unknown"
        self.fatal_error = False
        self.speaker_statuses: dict[str, str] = {}
        self._install_speakers(speakers, speakers.keys())

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self.fatal_error = False
        self._start_workers()

    async def stop(self) -> None:
        if not self._started:
            return
        self._started = False
        await self._stop_workers(graceful=True)

    async def replace_speakers(
        self,
        speakers: Mapping[str, Speaker],
        available_ids: Collection[str],
    ) -> None:
        async with self._replace_lock:
            self._replacing = True
            try:
                if self._tasks:
                    self._drain_all("speaker_reconfigured")
                    await self._stop_workers(graceful=False)
                self._install_speakers(speakers, available_ids)
                if self._started:
                    self._start_workers()
            finally:
                self._replacing = False

    def set_available_ids(self, available_ids: Collection[str]) -> None:
        available = frozenset(available_ids) & self._speakers.keys()
        newly_unavailable = self._available_ids - available
        self._available_ids = available
        for speaker_id in newly_unavailable:
            self.speaker_statuses[speaker_id] = "unavailable"
            self._drain_queue(speaker_id, "speaker_unavailable")
        for speaker_id in available:
            if self.speaker_statuses.get(speaker_id) == "unavailable":
                self.speaker_statuses[speaker_id] = "unknown"

    def set_auth_status(self, status: str) -> None:
        self.auth_status = status
        if status == "reauth_required":
            for speaker_id in self.speaker_statuses:
                self.speaker_statuses[speaker_id] = "reauth_required"
            self._drain_all("speaker_reauth_required")

    def available(self, speaker_id: str | None) -> bool:
        return (
            self._started
            and speaker_id is not None
            and speaker_id in self._available_ids
            and self.auth_status != "reauth_required"
            and not self.fatal_error
        )

    def submit(self, action: SpeechAction) -> bool:
        if self.auth_status == "reauth_required" or self.fatal_error:
            return False
        if self._replacing:
            self._record(action, "speaker_reconfigured", False)
            return False
        queue = self._queues.get(action.speaker_id)
        if queue is None:
            self._record(action, "speaker_unknown", False)
            return False
        if action.speaker_id not in self._available_ids:
            self._record(action, "speaker_unavailable", False)
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

    def _install_speakers(
        self,
        speakers: Mapping[str, Speaker],
        available_ids: Collection[str],
    ) -> None:
        self._speakers = dict(speakers)
        self._queues = {
            speaker_id: asyncio.Queue(maxsize=self._queue_size) for speaker_id in self._speakers
        }
        self._pending_actions = {speaker_id: {} for speaker_id in self._speakers}
        self._available_ids = frozenset(available_ids) & self._speakers.keys()
        self.speaker_statuses = {
            speaker_id: "unknown" if speaker_id in self._available_ids else "unavailable"
            for speaker_id in self._speakers
        }

    def _start_workers(self) -> None:
        for speaker_id, speaker in self._speakers.items():
            task = asyncio.create_task(
                self._consume(speaker_id, speaker),
                name=f"speaker-worker-{speaker_id}",
            )
            task.add_done_callback(self._record_task_result)
            self._tasks[speaker_id] = task

    async def _stop_workers(self, *, graceful: bool) -> None:
        if not self._tasks:
            return
        if not graceful:
            self._drain_all("speaker_reconfigured")
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
                action = self._resolve_entry(speaker_id, entry)
                if action is None:
                    continue
                if self.auth_status == "reauth_required":
                    self._record(action, "speaker_reauth_required", False)
                    continue
                if speaker_id not in self._available_ids:
                    self._record(action, "speaker_unavailable", False)
                    continue
                if (
                    action.max_queue_age_seconds is not None
                    and self._clock() - action.created_monotonic > action.max_queue_age_seconds
                ):
                    self._record(action, "speaker_skipped_expired", False)
                    continue
                if not self._storage.camera_rule_enabled(action.camera_id, action.rule_id):
                    self._record(action, "speaker_skipped_disabled", False)
                    continue
                if self._storage.camera_speaker_id(action.camera_id) != action.speaker_id:
                    self._record(action, "speaker_skipped_pairing_changed", False)
                    continue

                result = await asyncio.to_thread(speaker.speak, action.text)
                if result.code == "reauth_required":
                    await self._enter_auth_required()
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

    async def _enter_auth_required(self) -> None:
        already_required = self.auth_status == "reauth_required"
        self.set_auth_status("reauth_required")
        if already_required or self._on_auth_required is None:
            return
        result = self._on_auth_required()
        if inspect.isawaitable(result):
            await result

    def _drain_all(self, kind: str) -> None:
        for speaker_id in tuple(self._queues):
            self._drain_queue(speaker_id, kind)

    def _drain_queue(self, speaker_id: str, kind: str) -> None:
        queue = self._queues.get(speaker_id)
        if queue is None:
            return
        while True:
            try:
                entry = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            try:
                if entry is None:
                    continue
                action = self._resolve_entry(speaker_id, entry)
                if action is not None:
                    self._record(action, kind, False)
            finally:
                queue.task_done()

    def _resolve_entry(
        self,
        speaker_id: str,
        entry: SpeechAction | tuple[str, str],
    ) -> SpeechAction | None:
        if isinstance(entry, tuple):
            return self._pending_actions[speaker_id].pop(entry, None)
        return entry

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
