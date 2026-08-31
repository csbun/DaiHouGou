import asyncio
import time
from collections import deque
from pathlib import Path

import numpy as np

from daihougou.camera_runtime import CameraRuntime
from daihougou.presence import PresenceTracker
from daihougou.rules import SpeechAction, WELCOME_RULE_ID, WelcomeRule
from daihougou.storage import Storage
from daihougou.vision.frame_source import FrameSample
from daihougou.vision.person_detector import PersonDetection


class FakeFrameSource:
    def __init__(self, times: list[float]) -> None:
        frame = np.zeros((256, 256, 3), dtype=np.uint8)
        self.samples: deque[FrameSample | None] = deque(
            FrameSample(sequence, captured_at, frame)
            for sequence, captured_at in enumerate(times, start=1)
        )
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def wait_for_frame(self, after_sequence: int, timeout: float) -> FrameSample | None:
        if self.samples:
            return self.samples.popleft()
        time.sleep(0.01)
        return None

    def stop(self) -> None:
        self.stopped = True


class FakeScheduler:
    def __init__(self, observations: list[bool]) -> None:
        self._observations = deque(observations)

    async def detect(self, camera_id: str, sample: FrameSample) -> PersonDetection:
        present = self._observations.popleft()
        return PersonDetection(present, 0.8 if present else 0.1, 4)


class FakeSpeakerManager:
    def __init__(self, *, accepts: bool = True) -> None:
        self.actions: list[SpeechAction] = []
        self.accepts = accepts

    def submit(self, action: SpeechAction) -> bool:
        self.actions.append(action)
        return self.accepts


def configured_storage(
    tmp_path: Path,
    camera_id: str,
    speaker_id: str,
    *,
    enabled: bool,
) -> Storage:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.sync_cameras([camera_id], speaker_id)
    storage.set_camera_rule_enabled(camera_id, WELCOME_RULE_ID, enabled)
    return storage


async def wait_for_sequence(camera: CameraRuntime, sequence: int) -> None:
    deadline = time.monotonic() + 2
    while camera.snapshot().last_sequence < sequence and time.monotonic() < deadline:
        await asyncio.sleep(0.01)


def test_camera_runtime_processes_entry_and_tags_events(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = configured_storage(tmp_path, "front", "living_room", enabled=True)
        source = FakeFrameSource([0, 1, 2, 3, 4])
        scheduler = FakeScheduler([False, False, False, True, True])
        speaker = FakeSpeakerManager()
        camera = CameraRuntime(
            "front",
            source,
            scheduler,
            PresenceTracker(),
            WelcomeRule("front", storage, chooser=lambda phrases: phrases[0]),
            speaker,
            storage,
        )

        await camera.start()
        await wait_for_sequence(camera, 5)
        snapshot = camera.snapshot()
        await camera.stop()

        assert source.started is True
        assert source.stopped is True
        assert snapshot.stream == "ready"
        assert snapshot.detector == "ready"
        assert snapshot.presence == "present"
        assert speaker.actions[0].camera_id == "front"
        assert all(
            event.camera_id == "front"
            for event in storage.recent_events()
            if event.kind == "presence_changed"
        )

    asyncio.run(scenario())


def test_inference_failure_invalidates_presence_and_records_safe_event(
    tmp_path: Path,
) -> None:
    class BrokenScheduler:
        async def detect(self, camera_id: str, sample: FrameSample) -> PersonDetection:
            raise RuntimeError("private upstream details")

    async def scenario() -> None:
        storage = configured_storage(tmp_path, "front", "living_room", enabled=True)
        camera = CameraRuntime(
            "front",
            FakeFrameSource([0]),
            BrokenScheduler(),
            PresenceTracker(),
            WelcomeRule("front", storage),
            FakeSpeakerManager(),
            storage,
        )

        await camera.start()
        await wait_for_sequence(camera, 1)
        await camera.stop()
        snapshot = camera.snapshot()
        events = storage.recent_events()

        assert snapshot.presence == "unknown"
        assert snapshot.detector == "degraded"
        assert [event.kind for event in events] == ["detector_failed"]
        assert events[0].camera_id == "front"
        assert "private upstream details" not in events[0].details_json

    asyncio.run(scenario())


def test_camera_timeout_is_recorded_once_during_an_outage(tmp_path: Path) -> None:
    class TimeoutClock:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> float:
            self.calls += 1
            return 0 if self.calls == 1 else 31

    async def scenario() -> None:
        storage = configured_storage(tmp_path, "front", "living_room", enabled=True)
        camera = CameraRuntime(
            "front",
            FakeFrameSource([]),
            FakeScheduler([]),
            PresenceTracker(),
            WelcomeRule("front", storage),
            FakeSpeakerManager(),
            storage,
            clock=TimeoutClock(),
        )

        await camera.start()
        deadline = time.monotonic() + 1
        while camera.snapshot().stream != "degraded" and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.04)
        await camera.stop()

        events = [
            event for event in storage.recent_events() if event.kind == "camera_no_frames"
        ]
        assert camera.snapshot().last_error == "camera_no_frames"
        assert len(events) == 1
        assert events[0].camera_id == "front"

    asyncio.run(scenario())


def test_frame_gap_cannot_create_false_leave_and_reentry(tmp_path: Path) -> None:
    class GappedFrameSource(FakeFrameSource):
        def __init__(self) -> None:
            frame = np.zeros((256, 256, 3), dtype=np.uint8)
            self.samples = deque(
                [
                    FrameSample(1, 0, frame),
                    FrameSample(2, 1, frame),
                    FrameSample(3, 2, frame),
                    None,
                    FrameSample(4, 100, frame),
                    FrameSample(5, 101, frame),
                    FrameSample(6, 102, frame),
                ]
            )
            self.started = False
            self.stopped = False

    async def scenario() -> None:
        storage = configured_storage(tmp_path, "front", "living_room", enabled=True)
        speaker = FakeSpeakerManager()
        camera = CameraRuntime(
            "front",
            GappedFrameSource(),
            FakeScheduler([True, True, True, False, True, True]),
            PresenceTracker(),
            WelcomeRule("front", storage),
            speaker,
            storage,
        )

        await camera.start()
        await wait_for_sequence(camera, 6)
        snapshot = camera.snapshot()
        await camera.stop()

        assert snapshot.presence == "present"
        assert speaker.actions == []

    asyncio.run(scenario())


def test_stop_always_stops_owned_frame_source_after_loop_failure(tmp_path: Path) -> None:
    class BrokenStorage(Storage):
        def record_event(self, event) -> None:
            raise RuntimeError("database unavailable")

    async def scenario() -> None:
        storage = BrokenStorage(tmp_path / "app.db")
        storage.initialize()
        storage.sync_cameras(["front"], "living_room")
        source = FakeFrameSource([0, 1, 2])
        camera = CameraRuntime(
            "front",
            source,
            FakeScheduler([False, False, False]),
            PresenceTracker(),
            WelcomeRule("front", storage),
            FakeSpeakerManager(),
            storage,
        )

        await camera.start()
        await wait_for_sequence(camera, 3)
        await camera.stop()

        assert source.stopped is True

    asyncio.run(scenario())


def test_action_submission_failure_does_not_stop_camera_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = configured_storage(tmp_path, "front", "living_room", enabled=True)
        speaker = FakeSpeakerManager(accepts=False)
        camera = CameraRuntime(
            "front",
            FakeFrameSource([0, 1, 2, 3, 4, 5]),
            FakeScheduler([False, False, False, True, True, True]),
            PresenceTracker(),
            WelcomeRule("front", storage),
            speaker,
            storage,
        )

        await camera.start()
        await wait_for_sequence(camera, 6)
        snapshot = camera.snapshot()
        await camera.stop()

        assert snapshot.last_sequence == 6
        assert len(speaker.actions) == 1

    asyncio.run(scenario())


def test_two_cameras_keep_presence_and_cooldown_state_isolated(tmp_path: Path) -> None:
    class MultiScheduler:
        def __init__(self) -> None:
            self.observations = {
                "front": deque([False, False, False, True, True]),
                "back": deque([False, False, False, True, True]),
            }

        async def detect(self, camera_id: str, sample: FrameSample) -> PersonDetection:
            present = self.observations[camera_id].popleft()
            return PersonDetection(present, 0.8 if present else 0.1, 4)

    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        storage.sync_cameras(["front", "back"], "living_room")
        storage.set_camera_rule_enabled("front", WELCOME_RULE_ID, True)
        storage.set_camera_rule_enabled("back", WELCOME_RULE_ID, True)
        speaker = FakeSpeakerManager()
        scheduler = MultiScheduler()
        front = CameraRuntime(
            "front",
            FakeFrameSource([0, 1, 2, 3, 4]),
            scheduler,
            PresenceTracker(),
            WelcomeRule("front", storage),
            speaker,
            storage,
        )
        back = CameraRuntime(
            "back",
            FakeFrameSource([0, 1, 2, 3, 4]),
            scheduler,
            PresenceTracker(),
            WelcomeRule("back", storage),
            speaker,
            storage,
        )

        await asyncio.gather(front.start(), back.start())
        await asyncio.gather(wait_for_sequence(front, 5), wait_for_sequence(back, 5))
        snapshots = (front.snapshot(), back.snapshot())
        await asyncio.gather(front.stop(), back.stop())

        assert [snapshot.presence for snapshot in snapshots] == ["present", "present"]
        assert sorted(action.camera_id for action in speaker.actions) == ["back", "front"]

    asyncio.run(scenario())
