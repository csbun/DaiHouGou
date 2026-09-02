import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from daihougou.detection_region import DetectionRegion
from daihougou.detection_scheduler import DetectionScheduler
from daihougou.runtime import Runtime
from daihougou.settings import SpeakerConfig
from daihougou.speaker import SpeakResult
from daihougou.speaker_worker import SpeakerManager
from daihougou.storage import Storage
from daihougou.vision.frame_source import FfmpegFrameSource
from daihougou.vision.person_detector import PersonDetection

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")


def generated_video_process(transition_second: int):
    def factory(_command: list[str]) -> subprocess.Popen[bytes]:
        generated_command = [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-re",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=256x256:r=1:d=6",
            "-vf",
            (
                f"drawbox=enable='gte(t,{transition_second})':"
                "x=0:y=0:w=iw:h=ih:color=white:t=fill,format=bgr24"
            ),
            "-frames:v",
            "6",
            "-threads",
            "1",
            "-f",
            "rawvideo",
            "pipe:1",
        ]
        return subprocess.Popen(
            generated_command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )

    return factory


class BrightnessDetector:
    def detect(self, frame: np.ndarray) -> PersonDetection:
        present = float(frame.mean()) > 127
        return PersonDetection(present, 0.9 if present else 0.1, 1)


class FakeSpeaker:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> SpeakResult:
        self.spoken.append(text)
        return SpeakResult(True, 1, 0)


class FakeDiscovery:
    def stream_names(self) -> tuple[str, ...]:
        return ("front", "back")


class FakeSnapshotter:
    def capture(self, rtsp_url: str) -> bytes:
        raise AssertionError("snapshot capture is not expected in this test")


def test_generated_video_routes_two_cameras_through_one_detector(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        living_room = FakeSpeaker()
        bedroom = FakeSpeaker()
        manager = SpeakerManager(
            {"living_room": living_room, "bedroom": bedroom}, storage
        )
        detector_factory_calls = 0

        def detector_factory() -> BrightnessDetector:
            nonlocal detector_factory_calls
            detector_factory_calls += 1
            return BrightnessDetector()

        scheduler = DetectionScheduler(detector_factory)
        sources: dict[str, FfmpegFrameSource] = {}

        def source_factory(
            url: str, size: int, region: DetectionRegion
        ) -> FfmpegFrameSource:
            stream_id = url.rsplit("/", 1)[-1]
            transition = 3 if stream_id == "front" else 4
            source = FfmpegFrameSource(
                url,
                size=size,
                process_factory=generated_video_process(transition),
                region=region,
            )
            sources[stream_id] = source
            return source

        runtime = Runtime(
            storage=storage,
            discovery=FakeDiscovery(),
            rtsp_base_url="rtsp://127.0.0.1:8554",
            speakers=(
                SpeakerConfig("living_room", "客厅音箱", "safe-one"),
                SpeakerConfig("bedroom", "卧室音箱", "safe-two"),
            ),
            speaker_manager=manager,
            scheduler=scheduler,
            frame_source_factory=source_factory,
            snapshotter=FakeSnapshotter(),
            leave_seconds=10,
            welcome_cooldown_seconds=60,
        )

        await runtime.start()
        runtime.set_welcome_phrases(["你好呀，欢迎回来。"])
        await runtime.set_camera_speaker("back", "bedroom")
        await runtime.set_rule_enabled("front", True)
        await runtime.set_rule_enabled("back", True)
        deadline = time.monotonic() + 12
        while (
            not living_room.spoken or not bedroom.spoken
        ) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await manager.join()

        assert living_room.spoken == ["你好呀，欢迎回来。"]
        assert bedroom.spoken == ["你好呀，欢迎回来。"]
        assert detector_factory_calls == 1
        assert {
            (event.camera_id, event.speaker_id)
            for event in storage.recent_events()
            if event.kind == "speaker_completed"
        } == {("front", "living_room"), ("back", "bedroom")}

        await runtime.set_rule_enabled("front", False)
        await runtime.set_rule_enabled("back", False)
        assert sources["front"].status == "stopped"
        assert sources["back"].status == "stopped"
        assert scheduler.loaded is False
        await runtime.stop()

    asyncio.run(scenario())
