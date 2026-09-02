from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Protocol

from daihougou.camera_runtime import CameraRuntime, FrameSource
from daihougou.detection_region import FULL_FRAME_REGION, DetectionRegion
from daihougou.detection_scheduler import DetectionScheduler, DetectorKind, DetectorSnapshot
from daihougou.go2rtc import DiscoveryError, Go2RtcClient, rtsp_stream_url
from daihougou.object_rule import ObjectCategoryAnnouncementRule
from daihougou.presence import PresenceTracker
from daihougou.rules import (
    BUILTIN_RULE_IDS,
    BUILTIN_RULE_NAMES,
    OBJECT_RULE_ID,
    WELCOME_RULE_ID,
    SpeechAction,
    WelcomeRule,
)
from daihougou.settings import SpeakerConfig
from daihougou.speaker_worker import SpeakerManager
from daihougou.storage import CameraConfig, Storage, StoredEvent
from daihougou.vision.frame_source import OBJECT_FRAME_SIZE, PERSON_FRAME_SIZE

FrameSourceFactory = Callable[[str, int, DetectionRegion], FrameSource]


class Discovery(Protocol):
    def stream_names(self) -> tuple[str, ...]: ...


class Snapshotter(Protocol):
    def capture(self, rtsp_url: str) -> bytes: ...


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
class DetectorView:
    kind: str
    status: str
    loaded: bool


@dataclass(frozen=True)
class RuleView:
    id: str
    name: str
    enabled: bool
    status: str
    last_confidence: float | None
    last_detection_latency_ms: int | None
    last_error: str
    latest_trigger: StoredEvent | None


@dataclass(frozen=True)
class CameraView:
    stream_id: str
    speaker_id: str
    speaker: str
    available: bool | None
    rules: tuple[RuleView, ...] = ()
    stream: str = "stopped"
    presence: str = "unknown"
    last_error: str = ""
    rule_enabled: bool | None = None
    detector: str = "stopped"
    last_confidence: float | None = None
    last_detection_latency_ms: int | None = None
    latest_trigger: StoredEvent | None = None
    detection_region: DetectionRegion = FULL_FRAME_REGION

    def __post_init__(self) -> None:
        if self.rule_enabled is None:
            object.__setattr__(self, "rule_enabled", any(rule.enabled for rule in self.rules))
        if self.rules:
            enabled = tuple(rule for rule in self.rules if rule.enabled)
            if any(rule.status == "degraded" for rule in enabled):
                object.__setattr__(self, "detector", "degraded")
            elif enabled and all(rule.status == "ready" for rule in enabled):
                object.__setattr__(self, "detector", "ready")
            object.__setattr__(
                self,
                "last_confidence",
                next((rule.last_confidence for rule in self.rules if rule.last_confidence is not None), None),
            )
            object.__setattr__(
                self,
                "last_detection_latency_ms",
                next((rule.last_detection_latency_ms for rule in self.rules if rule.last_detection_latency_ms is not None), None),
            )
            object.__setattr__(
                self,
                "latest_trigger",
                next((rule.latest_trigger for rule in self.rules if rule.latest_trigger), None),
            )

    def health_dict(self) -> dict[str, object]:
        if not self.rules:
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
        return {
            "stream_id": self.stream_id,
            "available": self.available,
            "rules": [
                {
                    "id": rule.id,
                    "name": rule.name,
                    "enabled": rule.enabled,
                    "status": rule.status,
                    "last_confidence": rule.last_confidence,
                    "last_detection_latency_ms": rule.last_detection_latency_ms,
                    "last_error": rule.last_error,
                    "latest_trigger": (
                        rule.latest_trigger.kind if rule.latest_trigger is not None else None
                    ),
                }
                for rule in self.rules
            ] if self.rules else None,
            "stream": self.stream,
            "presence": self.presence,
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
    detectors: tuple[DetectorView, ...] = ()

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
            and all(rule.status == "ready" for rule in camera.rules if rule.enabled)
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
            camera.stream == "degraded" or camera.detector == "degraded" for camera in enabled
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
        snapshotter: Snapshotter,
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
        self._snapshotter = snapshotter
        self._snapshot_semaphore = asyncio.Semaphore(1)
        self._leave_seconds = leave_seconds
        self._welcome_cooldown_seconds = welcome_cooldown_seconds
        self._available_stream_ids: frozenset[str] | None = None
        self._camera_runtimes: dict[str, CameraRuntime] = {}
        self._camera_descriptors: dict[
            str, tuple[frozenset[str], int, DetectionRegion]
        ] = {}
        self._camera_runtime_errors: dict[str, str] = {}
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
            self._camera_descriptors.clear()
            self._camera_runtime_errors.clear()
            close = getattr(self._scheduler, "close", None)
            if close is not None:
                await close()
            elif self._scheduler.loaded:
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
            except Exception:  # noqa: BLE001
                self._discovery_status = "degraded"
                self._last_error = "go2rtc_unavailable"
            else:
                self._storage.sync_cameras(stream_ids, self._speakers[0].id)
                self._available_stream_ids = frozenset(stream_ids)
                self._discovery_status = "ready"
                self._last_error = ""
            await self._reconcile()

    async def set_rule_enabled(
        self, camera_id: str, rule_id: str | bool, enabled: bool | None = None
    ) -> None:
        if isinstance(rule_id, bool):
            rule_id, enabled = WELCOME_RULE_ID, rule_id
        if rule_id not in BUILTIN_RULE_IDS or enabled is None:
            raise ValueError("unknown_rule")
        async with self._lock:
            self._storage.set_camera_rule_enabled(camera_id, rule_id, enabled)
            await self._reconcile()

    async def set_camera_speaker(self, camera_id: str, speaker_id: str) -> None:
        if speaker_id not in self._speaker_ids:
            raise ValueError("unknown speaker")
        async with self._lock:
            self._storage.set_camera_speaker(camera_id, speaker_id)

    async def set_camera_detection_region(
        self, camera_id: str, region: DetectionRegion
    ) -> None:
        async with self._lock:
            current = next(
                (
                    item
                    for item in self._storage.list_cameras()
                    if item.stream_id == camera_id
                ),
                None,
            )
            if current is None:
                raise KeyError(camera_id)
            if current.detection_region == region:
                return
            self._storage.set_camera_detection_region(camera_id, region)
            await self._reconcile(
                suppress_initial_object_for=frozenset({camera_id})
            )

    async def capture_camera_snapshot(self, camera_id: str) -> bytes:
        if not any(
            camera.stream_id == camera_id for camera in self._storage.list_cameras()
        ):
            raise KeyError(camera_id)
        rtsp_url = rtsp_stream_url(self._rtsp_base_url, camera_id)
        async with self._snapshot_semaphore:
            return await asyncio.to_thread(self._snapshotter.capture, rtsp_url)

    def welcome_phrases(self) -> tuple[str, ...]:
        return self._storage.welcome_phrases()

    def set_welcome_phrases(self, lines: Sequence[str]) -> tuple[str, ...]:
        return self._storage.set_welcome_phrases(lines)

    def snapshot(self) -> RuntimeSnapshot:
        speaker_names = {speaker.id: speaker.name for speaker in self._speakers}
        cameras = tuple(self._camera_view(config, speaker_names) for config in self._storage.list_cameras())
        shared_failed = self._scheduler.fatal_error or self._speaker_manager.fatal_error
        detectors = tuple(
            DetectorView(kind.value, self._detector_snapshot(kind).status, self._detector_snapshot(kind).loaded)
            for kind in DetectorKind
        )
        return RuntimeSnapshot(
            app="unhealthy" if shared_failed else self._app_status,
            database=self._database_status,
            discovery=self._discovery_status,
            detector=self._scheduler.status,
            speaker_auth=self._speaker_manager.auth_status,
            speakers=tuple(SpeakerOption(speaker.id, speaker.name) for speaker in self._speakers),
            cameras=cameras,
            events=tuple(self._storage.recent_events()),
            last_error="background_task_stopped" if shared_failed else self._last_error,
            detectors=detectors,
        )

    async def _reconcile(
        self, suppress_initial_object_for: frozenset[str] = frozenset()
    ) -> None:
        configs = self._storage.list_cameras()
        enabled_by_camera = {
            config.stream_id: frozenset(
                rule_id
                for rule_id in BUILTIN_RULE_IDS
                if self._storage.camera_rule_enabled(config.stream_id, rule_id)
            )
            for config in configs
        }
        desired = {
            stream_id: rule_ids
            for stream_id, rule_ids in enabled_by_camera.items()
            if rule_ids
            and (
                self._available_stream_ids is None
                or stream_id in self._available_stream_ids
            )
        }
        if self._available_stream_ids is None:
            desired = {stream_id: ids for stream_id, ids in enabled_by_camera.items() if ids}

        for stream_id in sorted(self._camera_runtimes.keys() - desired.keys()):
            await self._camera_runtimes.pop(stream_id).stop()
            self._camera_descriptors.pop(stream_id, None)
            self._camera_runtime_errors.pop(stream_id, None)
        for stream_id in self._camera_runtime_errors.keys() - desired.keys():
            self._camera_runtime_errors.pop(stream_id)

        needed = {
            kind
            for rule_ids in desired.values()
            for kind, rule_id in ((DetectorKind.PERSON, WELCOME_RULE_ID), (DetectorKind.OBJECT, OBJECT_RULE_ID))
            if rule_id in rule_ids
        }
        for kind in DetectorKind:
            if kind in needed:
                try:
                    await self._enable_detector(kind)
                except RuntimeError:
                    pass
            else:
                await self._disable_detector(kind)

        for stream_id, rule_ids in sorted(desired.items()):
            size = OBJECT_FRAME_SIZE if OBJECT_RULE_ID in rule_ids else PERSON_FRAME_SIZE
            config = next(config for config in configs if config.stream_id == stream_id)
            descriptor = (rule_ids, size, config.detection_region)
            if self._camera_descriptors.get(stream_id) == descriptor:
                continue
            old = self._camera_runtimes.pop(stream_id, None)
            if old is not None:
                await old.stop()
            camera: CameraRuntime | None = None
            try:
                source = self._make_source(
                    rtsp_stream_url(self._rtsp_base_url, stream_id),
                    size,
                    config.detection_region,
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
                    )
                    if WELCOME_RULE_ID in rule_ids
                    else None,
                    self._speaker_manager,
                    self._storage,
                    object_rule=ObjectCategoryAnnouncementRule(stream_id, self._storage)
                    if OBJECT_RULE_ID in rule_ids
                    else None,
                    suppress_initial_object_detection=(
                        stream_id in suppress_initial_object_for
                    ),
                )
                self._camera_runtimes[stream_id] = camera
                await camera.start()
            except Exception:  # noqa: BLE001
                self._camera_runtimes.pop(stream_id, None)
                self._camera_descriptors.pop(stream_id, None)
                if camera is not None:
                    with suppress(Exception):
                        await camera.stop()
                self._camera_runtime_errors[stream_id] = "camera_start_failed"
                continue
            self._camera_descriptors[stream_id] = descriptor
            self._camera_runtime_errors.pop(stream_id, None)

    def _make_source(
        self, url: str, size: int, region: DetectionRegion
    ) -> FrameSource:
        return self._frame_source_factory(url, size, region)

    async def _enable_detector(self, kind: DetectorKind) -> None:
        enable = getattr(self._scheduler, "enable", None)
        if enable is not None:
            await enable(kind)
        elif not self._scheduler.loaded:
            await self._scheduler.start()

    async def _disable_detector(self, kind: DetectorKind) -> None:
        disable = getattr(self._scheduler, "disable", None)
        if disable is not None:
            await disable(kind)
        elif (
            not any(
                self._storage.camera_rule_enabled(c.stream_id, WELCOME_RULE_ID)
                for c in self._storage.list_cameras()
            )
            and self._scheduler.loaded
        ):
            await self._scheduler.stop()

    def _detector_snapshot(self, kind: DetectorKind) -> DetectorSnapshot:
        snapshot = getattr(self._scheduler, "snapshot", None)
        if snapshot is not None:
            result = snapshot(kind)
            if isinstance(result, DetectorSnapshot):
                return result
        return DetectorSnapshot(self._scheduler.status, self._scheduler.loaded, self._scheduler.fatal_error)

    def _camera_view(self, config: CameraConfig, speaker_names: dict[str, str]) -> CameraView:
        available = None if self._available_stream_ids is None else config.stream_id in self._available_stream_ids
        runtime = self._camera_runtimes.get(config.stream_id)
        enabled_ids = tuple(
            rule_id for rule_id in BUILTIN_RULE_IDS if self._storage.camera_rule_enabled(config.stream_id, rule_id)
        )
        if runtime is not None:
            state = runtime.snapshot()
            pipeline = {WELCOME_RULE_ID: state.person, OBJECT_RULE_ID: state.object}
            stream = state.stream
            presence = state.presence
            last_error = state.last_error
        else:
            unavailable = bool(enabled_ids) and available is False
            runtime_error = self._camera_runtime_errors.get(config.stream_id, "")
            from daihougou.camera_runtime import PipelineSnapshot

            pipeline = {
                WELCOME_RULE_ID: PipelineSnapshot(
                    "degraded" if unavailable or runtime_error else "stopped",
                    None,
                    None,
                    runtime_error or ("stream_unavailable" if unavailable else ""),
                ),
                OBJECT_RULE_ID: PipelineSnapshot(
                    "degraded" if unavailable or runtime_error else "stopped",
                    None,
                    None,
                    runtime_error or ("stream_unavailable" if unavailable else ""),
                ),
            }
            stream = "degraded" if unavailable or runtime_error else "stopped"
            presence = "unknown"
            last_error = runtime_error or ("stream_unavailable" if unavailable else "")
        rules = tuple(
            RuleView(
                rule_id,
                BUILTIN_RULE_NAMES[rule_id],
                rule_id in enabled_ids,
                (
                    "degraded"
                    if rule_id in enabled_ids
                    and self._detector_snapshot(
                        DetectorKind.PERSON if rule_id == WELCOME_RULE_ID else DetectorKind.OBJECT
                    ).status
                    == "degraded"
                    else pipeline[rule_id].status
                ),
                pipeline[rule_id].last_confidence,
                pipeline[rule_id].last_detection_latency_ms,
                (
                    pipeline[rule_id].last_error
                    or (
                        "detector_start_failed"
                        if rule_id in enabled_ids
                        and self._detector_snapshot(
                            DetectorKind.PERSON if rule_id == WELCOME_RULE_ID else DetectorKind.OBJECT
                        ).status
                        == "degraded"
                        else ""
                    )
                ),
                self._storage.latest_rule_trigger(rule_id, config.stream_id),
            )
            for rule_id in BUILTIN_RULE_IDS
        )
        return CameraView(
            config.stream_id,
            config.speaker_id,
            speaker_names.get(config.speaker_id, config.speaker_id),
            available,
            rules,
            stream,
            presence,
            last_error,
            detection_region=config.detection_region,
        )
