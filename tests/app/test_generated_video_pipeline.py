import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from daihougou.presence import PresenceTracker
from daihougou.rules import WelcomeRule
from daihougou.runtime import Runtime
from daihougou.speaker import SpeakResult
from daihougou.speaker_worker import SpeakerWorker
from daihougou.storage import Storage
from daihougou.vision.frame_source import FfmpegFrameSource
from daihougou.vision.person_detector import PersonDetection

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")


def generated_video_process(_command: list[str]) -> subprocess.Popen[bytes]:
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
        "drawbox=enable='gte(t,3)':x=0:y=0:w=iw:h=ih:color=white:t=fill,format=bgr24",
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


def test_generated_video_reaches_one_welcome_action(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        storage.set_rule_enabled("welcome_on_person_entry", True)
        source = FfmpegFrameSource("ignored", process_factory=generated_video_process)
        speaker = FakeSpeaker()
        worker = SpeakerWorker(speaker, storage)
        runtime = Runtime(
            source,
            BrightnessDetector(),
            PresenceTracker(),
            WelcomeRule(storage, chooser=lambda phrases: phrases[0]),
            worker,
            storage,
        )

        await runtime.start()
        deadline = time.monotonic() + 10
        while not speaker.spoken and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await runtime.stop()

        assert speaker.spoken == ["你好呀，欢迎回来。"]
        assert any(event.kind == "presence_changed" for event in storage.recent_events())

    asyncio.run(scenario())
