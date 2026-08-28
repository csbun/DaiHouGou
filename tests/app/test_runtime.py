import asyncio
import time
from collections import deque
from pathlib import Path

import numpy as np

import daihougou.runtime as runtime_module
from daihougou.presence import PresenceTracker
from daihougou.rules import WelcomeRule
from daihougou.runtime import Runtime
from daihougou.speaker import SpeakResult
from daihougou.speaker_worker import SpeakerWorker
from daihougou.storage import Storage
from daihougou.vision.frame_source import FrameSample
from daihougou.vision.person_detector import PersonDetection


class FakeFrameSource:
    def __init__(self, times: list[float]) -> None:
        frame = np.zeros((256, 256, 3), dtype=np.uint8)
        self.samples = deque(
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


class FakeDetector:
    def __init__(self, observations: list[bool]) -> None:
        self.observations = deque(observations)

    def detect(self, frame: np.ndarray) -> PersonDetection:
        present = self.observations.popleft()
        return PersonDetection(present, 0.8 if present else 0.1, 4)


class FakeSpeaker:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> SpeakResult:
        self.spoken.append(text)
        return SpeakResult(True, 12, 0)


class HangingSpeakerWorker:
    auth_status = "unknown"

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        await asyncio.Event().wait()

    def submit(self, action: object) -> bool:
        return True


async def wait_for_sequence(runtime: Runtime, sequence: int) -> None:
    deadline = time.monotonic() + 2
    while runtime.snapshot().last_sequence < sequence and time.monotonic() < deadline:
        await asyncio.sleep(0.01)


def test_runtime_processes_person_entry_and_stops_owned_components(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        storage.set_rule_enabled("welcome_on_person_entry", True)
        source = FakeFrameSource([0, 1, 2, 3, 4])
        speaker = FakeSpeaker()
        worker = SpeakerWorker(speaker, storage)
        runtime = Runtime(
            source,
            FakeDetector([False, False, False, True, True]),
            PresenceTracker(),
            WelcomeRule(storage, chooser=lambda phrases: phrases[0]),
            worker,
            storage,
        )

        await runtime.start()
        await wait_for_sequence(runtime, 5)
        await worker.join()
        snapshot = runtime.snapshot()
        await runtime.stop()

        assert source.started is True
        assert source.stopped is True
        assert snapshot.camera == "ready"
        assert snapshot.detector == "ready"
        assert snapshot.presence == "present"
        assert snapshot.last_sequence == 5
        assert speaker.spoken == ["你好呀，欢迎回来。"]
        assert "presence_changed" in [event.kind for event in storage.recent_events()]

    asyncio.run(scenario())


def test_runtime_skips_failed_inference_instead_of_marking_absent(tmp_path: Path) -> None:
    class BrokenDetector:
        def detect(self, frame: np.ndarray) -> PersonDetection:
            raise RuntimeError("private upstream details")

    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        source = FakeFrameSource([0])
        worker = SpeakerWorker(FakeSpeaker(), storage)
        runtime = Runtime(
            source,
            BrokenDetector(),
            PresenceTracker(),
            WelcomeRule(storage),
            worker,
            storage,
        )

        await runtime.start()
        await wait_for_sequence(runtime, 1)
        await runtime.stop()
        snapshot = runtime.snapshot()

        assert snapshot.presence == "unknown"
        assert snapshot.detector == "degraded"
        event = storage.recent_events()[0]
        assert event.kind == "detector_failed"
        assert "private upstream details" not in event.details_json

    asyncio.run(scenario())


def test_runtime_records_camera_timeout_once_during_an_outage(tmp_path: Path) -> None:
    class TimeoutClock:
        def __init__(self) -> None:
            self.calls = 0

        def __call__(self) -> float:
            self.calls += 1
            return 0 if self.calls == 1 else 31

    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        source = FakeFrameSource([])
        runtime = Runtime(
            source,
            FakeDetector([]),
            PresenceTracker(),
            WelcomeRule(storage),
            SpeakerWorker(FakeSpeaker(), storage),
            storage,
            clock=TimeoutClock(),
        )

        await runtime.start()
        deadline = time.monotonic() + 1
        while runtime.snapshot().camera != "degraded" and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.04)
        await runtime.stop()

        snapshot = runtime.snapshot()
        timeout_events = [
            event for event in storage.recent_events() if event.kind == "camera_no_frames"
        ]
        assert snapshot.camera == "degraded"
        assert snapshot.last_error == "camera_no_frames"
        assert len(timeout_events) == 1

    asyncio.run(scenario())


def test_runtime_bounds_speaker_shutdown_and_reports_timeout(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(
        runtime_module, "SPEAKER_STOP_TIMEOUT_SECONDS", 0.01, raising=False
    )

    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        runtime = Runtime(
            FakeFrameSource([]),
            FakeDetector([]),
            PresenceTracker(),
            WelcomeRule(storage),
            HangingSpeakerWorker(),
            storage,
        )

        await runtime.start()
        await asyncio.wait_for(runtime.stop(), timeout=0.2)

        assert runtime.snapshot().last_error == "speaker_stop_timeout"

    asyncio.run(scenario())
