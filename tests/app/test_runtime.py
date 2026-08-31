import asyncio
import time
from collections import deque
from pathlib import Path
from urllib.parse import unquote

from daihougou.go2rtc import DiscoveryError
from daihougou.rules import SpeechAction, WELCOME_RULE_ID
from daihougou.runtime import Runtime
from daihougou.settings import SpeakerConfig
from daihougou.storage import EventRecord, Storage
from daihougou.vision.frame_source import FrameSample
from daihougou.vision.person_detector import PersonDetection


class FakeDiscovery:
    def __init__(self, results: list[tuple[str, ...] | Exception]) -> None:
        self._results = deque(results)
        self.calls = 0

    def stream_names(self) -> tuple[str, ...]:
        self.calls += 1
        result = self._results.popleft()
        if isinstance(result, Exception):
            raise result
        return result


class FakeSource:
    def __init__(self, *, broken: bool = False) -> None:
        self.broken = broken
        self.started = False
        self.stopped = False
        self.start_count = 0
        self.stop_count = 0

    def start(self) -> None:
        self.started = True
        self.stopped = False
        self.start_count += 1

    def wait_for_frame(self, after_sequence: int, timeout: float) -> None:
        if self.broken:
            raise RuntimeError("private stream failure")
        time.sleep(0.005)
        return None

    def stop(self) -> None:
        self.stopped = True
        self.stop_count += 1


class FakeDetectionScheduler:
    def __init__(self) -> None:
        self.loaded = False
        self.status = "stopped"
        self.fatal_error = False
        self.starts = 0
        self.stops = 0

    async def start(self) -> None:
        self.loaded = True
        self.status = "ready"
        self.starts += 1

    async def stop(self) -> None:
        self.loaded = False
        self.status = "stopped"
        self.stops += 1

    async def detect(self, camera_id: str, sample: FrameSample) -> PersonDetection:
        return PersonDetection(False, 0.1, 1)


class FakeSpeakerManager:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self.auth_status = "unknown"
        self.fatal_error = False
        self.speaker_statuses = {
            "living_room": "unknown",
            "bedroom": "unknown",
        }

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def submit(self, action: SpeechAction) -> bool:
        return True


def make_runtime(
    tmp_path: Path,
    discoveries: list[tuple[str, ...] | Exception],
    *,
    broken_streams: frozenset[str] = frozenset(),
    speaker_manager: FakeSpeakerManager | None = None,
) -> tuple[Runtime, FakeDiscovery, dict[str, FakeSource], FakeDetectionScheduler]:
    storage = Storage(tmp_path / "app.db")
    discovery = FakeDiscovery(discoveries)
    sources: dict[str, FakeSource] = {}
    scheduler = FakeDetectionScheduler()
    speakers = (
        SpeakerConfig("living_room", "客厅音箱", "secret-living-did"),
        SpeakerConfig("bedroom", "卧室音箱", "secret-bedroom-did"),
    )

    def source_factory(url: str) -> FakeSource:
        stream_id = unquote(url.rsplit("/", 1)[-1])
        return sources.setdefault(
            stream_id, FakeSource(broken=stream_id in broken_streams)
        )

    runtime = Runtime(
        storage=storage,
        discovery=discovery,
        rtsp_base_url="rtsp://127.0.0.1:8554",
        speakers=speakers,
        speaker_manager=speaker_manager or FakeSpeakerManager(storage),
        scheduler=scheduler,
        frame_source_factory=source_factory,
        leave_seconds=10,
        welcome_cooldown_seconds=60,
    )
    return runtime, discovery, sources, scheduler


def test_start_discovers_once_and_new_cameras_stay_idle(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime, discovery, sources, scheduler = make_runtime(
            tmp_path, discoveries=[("front", "back")]
        )
        await runtime.start()
        await asyncio.sleep(0.05)
        snapshot = runtime.snapshot()

        assert discovery.calls == 1
        assert snapshot.saved_camera_count == 2
        assert snapshot.enabled_camera_count == 0
        assert snapshot.ready_camera_count == 0
        assert sources == {}
        assert scheduler.loaded is False
        assert snapshot.overall == "ready"
        assert all("secret" not in repr(option) for option in snapshot.speakers)
        await runtime.stop()

    asyncio.run(scenario())


def test_enable_starts_only_target_and_last_disable_releases_detector(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime, _discovery, sources, scheduler = make_runtime(
            tmp_path, discoveries=[("front", "back")]
        )
        await runtime.start()
        await runtime.set_rule_enabled("front", True)

        assert set(sources) == {"front"}
        assert sources["front"].started is True
        assert scheduler.loaded is True
        assert scheduler.starts == 1

        await runtime.set_rule_enabled("front", False)
        assert sources["front"].stopped is True
        assert scheduler.loaded is False
        await runtime.stop()

    asyncio.run(scenario())


def test_refresh_is_manual_and_missing_stream_keeps_configuration(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime, discovery, sources, _scheduler = make_runtime(
            tmp_path, discoveries=[("front", "back"), ("back",)]
        )
        await runtime.start()
        await runtime.set_rule_enabled("front", True)
        await asyncio.sleep(0.05)
        assert discovery.calls == 1

        await runtime.refresh_cameras()
        snapshot = runtime.snapshot()
        front = next(camera for camera in snapshot.cameras if camera.stream_id == "front")
        assert discovery.calls == 2
        assert front.available is False
        assert front.rule_enabled is True
        assert sources["front"].stopped is True
        await runtime.stop()

    asyncio.run(scenario())


def test_initial_discovery_failure_starts_saved_enabled_camera(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.sync_cameras(["front"], "living_room")
    storage.set_camera_rule_enabled("front", WELCOME_RULE_ID, True)

    async def scenario() -> None:
        runtime, discovery, sources, scheduler = make_runtime(
            tmp_path, discoveries=[DiscoveryError("go2rtc_unavailable")]
        )
        await runtime.start()
        snapshot = runtime.snapshot()
        front = snapshot.cameras[0]

        assert discovery.calls == 1
        assert front.available is None
        assert front.rule_enabled is True
        assert sources["front"].started is True
        assert scheduler.loaded is True
        assert snapshot.discovery == "degraded"
        await runtime.stop()

    asyncio.run(scenario())


def test_later_refresh_failure_preserves_availability_and_running_set(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime, _discovery, sources, scheduler = make_runtime(
            tmp_path,
            discoveries=[("front",), DiscoveryError("go2rtc_unavailable")],
        )
        await runtime.start()
        await runtime.set_rule_enabled("front", True)
        await runtime.refresh_cameras()
        snapshot = runtime.snapshot()

        assert snapshot.cameras[0].available is True
        assert sources["front"].stopped is False
        assert scheduler.loaded is True
        assert snapshot.discovery == "degraded"
        await runtime.stop()

    asyncio.run(scenario())


def test_removed_stream_reappears_with_rule_and_pairing_preserved(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime, _discovery, sources, _scheduler = make_runtime(
            tmp_path, discoveries=[("front",), (), ("front",)]
        )
        await runtime.start()
        await runtime.set_camera_speaker("front", "bedroom")
        await runtime.set_rule_enabled("front", True)
        await runtime.refresh_cameras()
        assert sources["front"].stop_count == 1
        await runtime.refresh_cameras()
        snapshot = runtime.snapshot()
        front = snapshot.cameras[0]

        assert front.available is True
        assert front.rule_enabled is True
        assert front.speaker_id == "bedroom"
        assert sources["front"].start_count == 2
        await runtime.stop()

    asyncio.run(scenario())


def test_unknown_speaker_update_does_not_change_storage(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime, _discovery, _sources, _scheduler = make_runtime(
            tmp_path, discoveries=[("front",)]
        )
        await runtime.start()
        try:
            await runtime.set_camera_speaker("front", "missing")
        except ValueError as error:
            assert str(error) == "unknown speaker"
        else:
            raise AssertionError("unknown speaker was accepted")

        assert runtime.snapshot().cameras[0].speaker_id == "living_room"
        await runtime.stop()

    asyncio.run(scenario())


def test_four_enabled_cameras_warn_but_are_not_blocked(tmp_path: Path) -> None:
    async def scenario() -> None:
        camera_ids = ("one", "two", "three", "four")
        runtime, _discovery, sources, _scheduler = make_runtime(
            tmp_path, discoveries=[camera_ids]
        )
        await runtime.start()
        for camera_id in camera_ids[:3]:
            await runtime.set_rule_enabled(camera_id, True)
        assert runtime.snapshot().resource_warning is False

        await runtime.set_rule_enabled(camera_ids[3], True)
        snapshot = runtime.snapshot()
        assert snapshot.resource_warning is True
        assert snapshot.enabled_camera_count == 4
        assert set(sources) == set(camera_ids)
        await runtime.stop()

    asyncio.run(scenario())


def test_one_camera_background_failure_does_not_mark_other_camera_degraded(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        runtime, _discovery, _sources, _scheduler = make_runtime(
            tmp_path,
            discoveries=[("front", "back")],
            broken_streams=frozenset({"front"}),
        )
        await runtime.start()
        await runtime.set_rule_enabled("front", True)
        await runtime.set_rule_enabled("back", True)
        await asyncio.sleep(0.05)
        cameras = {camera.stream_id: camera for camera in runtime.snapshot().cameras}

        assert cameras["front"].detector == "degraded"
        assert cameras["back"].detector != "degraded"
        await runtime.stop()

    asyncio.run(scenario())


def test_shared_scheduler_or_speaker_failure_is_unhealthy(tmp_path: Path) -> None:
    async def scenario() -> None:
        manager = FakeSpeakerManager(Storage(tmp_path / "app.db"))
        runtime, _discovery, _sources, scheduler = make_runtime(
            tmp_path, discoveries=[("front",)], speaker_manager=manager
        )
        await runtime.start()
        scheduler.fatal_error = True
        assert runtime.snapshot().overall == "unhealthy"
        scheduler.fatal_error = False
        manager.fatal_error = True
        assert runtime.snapshot().overall == "unhealthy"
        await runtime.stop()

    asyncio.run(scenario())


def test_latest_trigger_is_scoped_to_each_camera(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime, _discovery, _sources, _scheduler = make_runtime(
            tmp_path, discoveries=[("front", "back")]
        )
        await runtime.start()
        storage = Storage(tmp_path / "app.db")
        storage.record_event(
            EventRecord(
                "speaker_completed",
                True,
                rule_id=WELCOME_RULE_ID,
                camera_id="front",
                speaker_id="living_room",
            )
        )
        storage.record_event(
            EventRecord(
                "speaker_queue_full",
                False,
                rule_id=WELCOME_RULE_ID,
                camera_id="back",
                speaker_id="bedroom",
            )
        )
        cameras = {camera.stream_id: camera for camera in runtime.snapshot().cameras}

        assert cameras["front"].latest_trigger.kind == "speaker_completed"
        assert cameras["back"].latest_trigger.kind == "speaker_queue_full"
        await runtime.stop()

    asyncio.run(scenario())
