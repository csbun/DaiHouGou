from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from daihougou.camera_runtime import CameraRuntime, CameraSnapshot, FrameSource
from daihougou.detection_scheduler import DetectionScheduler
from daihougou.go2rtc import DiscoveryError, Go2RtcClient, rtsp_stream_url
from daihougou.presence import PresenceTracker
from daihougou.rules import WELCOME_RULE_ID, SpeechAction, WelcomeRule
from daihougou.settings import SpeakerConfig
from daihougou.speaker_worker import SpeakerManager
from daihougou.storage import CameraConfig, Storage, StoredEvent

FrameSourceFactory = Callable[[str], FrameSource]


class Discovery(Protocol):
    def stream_names(self) -> tuple[str, ...]: ...


class ManagedScheduler(Protocol):
    loaded: bool
    status: str
    fatal_error: bool

    async def start(self) -> None: ...

    async def stop(self) -> None: ...


class ManagedSpeakerManager(Protocol):
    auth_status: str
    fatal_error: bool
    speaker_statuses: dict[str, str]

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def submit(self, action: SpeechAction) -> bool: ...


@dataclass(frozen=True)
class SpeakerOption:
    id: str
    name: str


@dataclass(frozen=True)
class CameraView:
    stream_id: str
    speaker_id: str
    speaker: str
    available: bool | None
    rule_enabled: bool
    stream: str
    detector: str
    presence: str
    last_confidence: float | None
    last_detection_latency_ms: int | None
    last_error: str
    latest_trigger: StoredEvent | None

    def health_dict(self) -> dict[str, str | bool | float | int | None]:
        return {
            "stream_id": self.stream_id,
            "available": self.available,
            "rule_enabled": self.rule_enabled,
            "stream": self.stream,
            "detector": self.detector,
            "presence": self.presence,
            "last_confidence": self.last_confidence,
            "last_detection_latency_ms": self.last_detection_latency_ms,
            "last_error": self.last_error,
        }


@dataclass(frozen=True)
class RuntimeSnapshot:
    app: str
    database: str
    discovery: str
    detector: str
    speaker_auth: str
    speakers: tuple[SpeakerOption, ...]
    cameras: tuple[CameraView, ...]
    events: tuple[StoredEvent, ...]
    last_error: str

    @property
    def saved_camera_count(self) -> int:
        return len(self.cameras)

    @property
    def discovered_camera_count(self) -> int:
        return sum(camera.available is True for camera in self.cameras)

    @property
    def enabled_camera_count(self) -> int:
        return sum(camera.rule_enabled for camera in self.cameras)

    @property
    def ready_camera_count(self) -> int:
        return sum(
            camera.rule_enabled
            and camera.stream == "ready"
            and camera.detector == "ready"
            for camera in self.cameras
        )

    @property
    def resource_warning(self) -> bool:
        return self.enabled_camera_count >= 4

    @property
    def overall(self) -> str:
        if self.app == "unhealthy" or self.database == "unhealthy":
            return "unhealthy"
        enabled = tuple(camera for camera in self.cameras if camera.rule_enabled)
        if self.discovery == "degraded" or any(
            camera.stream == "degraded" or camera.detector == "degraded"
            for camera in enabled
        ):
            return "degraded"
        return "ready"


class Runtime:
    def __init__(
        self,
        storage: Storage,
        discovery: Go2RtcClient | Discovery,
        rtsp_base_url: str,
        speakers: tuple[SpeakerConfig, ...],
        speaker_manager: SpeakerManager | ManagedSpeakerManager,
        scheduler: DetectionScheduler | ManagedScheduler,
        frame_source_factory: FrameSourceFactory,
        leave_seconds: float,
        welcome_cooldown_seconds: float,
    ) -> None:
        if not speakers:
            raise ValueError("at least one speaker is required")
        self._storage = storage
        self._discovery = discovery
        self._rtsp_base_url = rtsp_base_url
        self._speakers = speakers
        self._speaker_ids = frozenset(speaker.id for speaker in speakers)
        self._speaker_manager = speaker_manager
        self._scheduler = scheduler
        self._frame_source_factory = frame_source_factory
        self._leave_seconds = leave_seconds
        self._welcome_cooldown_seconds = welcome_cooldown_seconds
        self._available_stream_ids: frozenset[str] | None = None
        self._camera_runtimes: dict[str, CameraRuntime] = {}
        self._app_status = "stopped"
        self._database_status = "starting"
        self._discovery_status = "unknown"
        self._last_error = ""
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        if self._app_status == "running":
            return
        try:
            self._storage.initialize()
        except Exception:
            self._database_status = "unhealthy"
            self._app_status = "unhealthy"
            self._last_error = "database_unavailable"
            raise
        self._database_status = "ready"
        await self._speaker_manager.start()
        self._app_status = "running"
        await self.refresh_cameras()

    async def stop(self) -> None:
        async with self._lock:
            for stream_id in sorted(self._camera_runtimes):
                camera = self._camera_runtimes.pop(stream_id)
                await camera.stop()
            if self._scheduler.loaded:
                await self._scheduler.stop()
            await self._speaker_manager.stop()
            self._app_status = "stopped"

    async def refresh_cameras(self) -> None:
        async with self._lock:
            try:
                stream_ids = await asyncio.to_thread(self._discovery.stream_names)
            except DiscoveryError as error:
                self._discovery_status = "degraded"
                self._last_error = str(error)
            except Exception:  # noqa: BLE001 - adapters may expose varied transport errors.
                self._discovery_status = "degraded"
                self._last_error = "go2rtc_unavailable"
            else:
                self._storage.sync_cameras(stream_ids, self._speakers[0].id)
                self._available_stream_ids = frozenset(stream_ids)
                self._discovery_status = "ready"
                self._last_error = ""
            await self._reconcile()

    async def set_rule_enabled(self, camera_id: str, enabled: bool) -> None:
        async with self._lock:
            self._storage.set_camera_rule_enabled(
                camera_id, WELCOME_RULE_ID, enabled
            )
            await self._reconcile()

    async def set_camera_speaker(self, camera_id: str, speaker_id: str) -> None:
        if speaker_id not in self._speaker_ids:
            raise ValueError("unknown speaker")
        async with self._lock:
            self._storage.set_camera_speaker(camera_id, speaker_id)

    def welcome_phrases(self) -> tuple[str, ...]:
        return self._storage.welcome_phrases()

    def set_welcome_phrases(self, lines: Sequence[str]) -> tuple[str, ...]:
        return self._storage.set_welcome_phrases(lines)

    def snapshot(self) -> RuntimeSnapshot:
        speaker_names = {speaker.id: speaker.name for speaker in self._speakers}
        cameras = tuple(
            self._camera_view(config, speaker_names)
            for config in self._storage.list_cameras()
        )
        shared_failed = self._scheduler.fatal_error or self._speaker_manager.fatal_error
        return RuntimeSnapshot(
            app="unhealthy" if shared_failed else self._app_status,
            database=self._database_status,
            discovery=self._discovery_status,
            detector=self._scheduler.status,
            speaker_auth=self._speaker_manager.auth_status,
            speakers=tuple(
                SpeakerOption(speaker.id, speaker.name) for speaker in self._speakers
            ),
            cameras=cameras,
            events=tuple(self._storage.recent_events()),
            last_error="background_task_stopped" if shared_failed else self._last_error,
        )

    async def _reconcile(self) -> None:
        configs = self._storage.list_cameras()
        enabled = {
            config.stream_id
            for config in configs
            if self._storage.camera_rule_enabled(config.stream_id, WELCOME_RULE_ID)
        }
        desired = (
            enabled
            if self._available_stream_ids is None
            else enabled.intersection(self._available_stream_ids)
        )

        for stream_id in sorted(self._camera_runtimes.keys() - desired):
            camera = self._camera_runtimes.pop(stream_id)
            await camera.stop()

        if not desired:
            if self._scheduler.loaded:
                await self._scheduler.stop()
            return

        if not self._scheduler.loaded:
            await self._scheduler.start()

        for stream_id in sorted(desired - self._camera_runtimes.keys()):
            source = self._frame_source_factory(
                rtsp_stream_url(self._rtsp_base_url, stream_id)
            )
            camera = CameraRuntime(
                stream_id,
                source,
                self._scheduler,
                PresenceTracker(leave_seconds=self._leave_seconds),
                WelcomeRule(
                    stream_id,
                    self._storage,
                    cooldown_seconds=self._welcome_cooldown_seconds,
                ),
                self._speaker_manager,
                self._storage,
            )
            self._camera_runtimes[stream_id] = camera
            await camera.start()

    def _camera_view(
        self, config: CameraConfig, speaker_names: dict[str, str]
    ) -> CameraView:
        available = (
            None
            if self._available_stream_ids is None
            else config.stream_id in self._available_stream_ids
        )
        enabled = self._storage.camera_rule_enabled(
            config.stream_id, WELCOME_RULE_ID
        )
        runtime = self._camera_runtimes.get(config.stream_id)
        if runtime is not None:
            state = runtime.snapshot()
        else:
            unavailable = enabled and available is False
            state = CameraSnapshot(
                stream_id=config.stream_id,
                stream="degraded" if unavailable else "stopped",
                detector="stopped",
                presence="unknown",
                last_sequence=0,
                last_confidence=None,
                last_detection_latency_ms=None,
                last_error="stream_unavailable" if unavailable else "",
            )
        return CameraView(
            stream_id=config.stream_id,
            speaker_id=config.speaker_id,
            speaker=speaker_names.get(config.speaker_id, config.speaker_id),
            available=available,
            rule_enabled=enabled,
            stream=state.stream,
            detector=state.detector,
            presence=state.presence,
            last_confidence=state.last_confidence,
            last_detection_latency_ms=state.last_detection_latency_ms,
            last_error=state.last_error,
            latest_trigger=self._storage.latest_rule_trigger(
                WELCOME_RULE_ID, config.stream_id
            ),
        )
