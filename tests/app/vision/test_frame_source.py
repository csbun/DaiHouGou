import io
import time

import numpy as np

from daihougou.vision.frame_source import (
    FRAME_BYTES,
    FfmpegFrameSource,
    build_ffmpeg_command,
    reconnect_delay,
)


def test_ffmpeg_command_forces_tcp_one_fps_and_fixed_bgr_frames() -> None:
    command = build_ffmpeg_command("rtsp://127.0.0.1:8554/xiaobai", 1.0)

    assert command[:4] == ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel"]
    assert command[command.index("-rtsp_transport") + 1] == "tcp"
    assert command[command.index("-vf") + 1] == "fps=1.0,scale=256:256"
    assert command[-5:] == ["-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]


def test_reconnect_delay_caps_at_thirty_seconds() -> None:
    assert [reconnect_delay(attempt) for attempt in range(7)] == [1, 2, 4, 8, 16, 30, 30]


class FakeProcess:
    def __init__(self, payload: bytes) -> None:
        self.stdout = io.BytesIO(payload)
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


def test_source_exposes_only_newest_complete_frame() -> None:
    first = np.zeros((256, 256, 3), dtype=np.uint8)
    second = np.full((256, 256, 3), 7, dtype=np.uint8)
    processes = iter([FakeProcess(first.tobytes() + second.tobytes()), FakeProcess(b"")])
    source = FfmpegFrameSource(
        "rtsp://127.0.0.1:8554/xiaobai",
        process_factory=lambda command: next(processes),
        sleeper=lambda seconds: time.sleep(0.01),
    )

    source.start()
    sample = source.wait_for_frame(after_sequence=0, timeout=1)
    deadline = time.monotonic() + 1
    while sample is not None and sample.sequence < 2 and time.monotonic() < deadline:
        sample = source.wait_for_frame(after_sequence=sample.sequence, timeout=0.1)
    source.stop()

    assert sample is not None
    assert sample.sequence == 2
    assert sample.frame.shape == (256, 256, 3)
    assert int(sample.frame[0, 0, 0]) == 7
    assert FRAME_BYTES == 256 * 256 * 3


def test_truncated_frame_is_never_published() -> None:
    processes = iter([FakeProcess(b"x" * (FRAME_BYTES - 1)), FakeProcess(b"")])
    source = FfmpegFrameSource(
        "rtsp://127.0.0.1:8554/xiaobai",
        process_factory=lambda command: next(processes),
        sleeper=lambda seconds: time.sleep(0.01),
    )

    source.start()
    sample = source.wait_for_frame(after_sequence=0, timeout=0.1)
    source.stop()

    assert sample is None
