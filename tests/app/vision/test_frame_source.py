import io
import shutil
import subprocess
import threading
import time

import numpy as np
import pytest

from daihougou.detection_region import DetectionRegion, FULL_FRAME_REGION
from daihougou.vision.frame_source import (
    FRAME_BYTES,
    FfmpegFrameSource,
    build_ffmpeg_command,
    reconnect_delay,
)
from daihougou.vision.scene_change import SceneChangeGate


def test_full_frame_keeps_existing_filter_without_crop() -> None:
    command = build_ffmpeg_command("rtsp://camera/front", 1.0, 256, FULL_FRAME_REGION)
    video_filter = command[command.index("-vf") + 1]

    assert video_filter.startswith("fps=1.0,scale=256:256")
    assert "crop=" not in video_filter


def test_region_crop_precedes_fps_scale_and_padding() -> None:
    region = DetectionRegion(0.25, 0.1, 0.5, 0.8)
    command = build_ffmpeg_command("rtsp://camera/front", 1.0, 416, region)
    video_filter = command[command.index("-vf") + 1]

    assert video_filter.startswith(
        "crop=w=iw*0.500000:h=ih*0.800000:x=iw*0.250000:y=ih*0.100000,"
        "fps=1.0,scale=416:416"
    )


def test_ffmpeg_command_forces_tcp_one_fps_and_fixed_bgr_frames() -> None:
    command = build_ffmpeg_command("rtsp://127.0.0.1:8554/xiaobai", 1.0)

    assert command[:4] == ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel"]
    assert command[command.index("-rtsp_transport") + 1] == "tcp"
    video_filter = command[command.index("-vf") + 1]
    assert video_filter.startswith("fps=1.0,scale=256:256:force_original_aspect_ratio=decrease")
    assert "pad=256:256" in video_filter
    assert command[-5:] == ["-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]


def test_ffmpeg_command_supports_person_and_object_sizes() -> None:
    person = build_ffmpeg_command("rtsp://127.0.0.1:8554/front", 1.0, 256)
    object_command = build_ffmpeg_command("rtsp://127.0.0.1:8554/front", 1.0, 416)

    assert "scale=256:256" in person[person.index("-vf") + 1]
    assert "pad=256:256" in person[person.index("-vf") + 1]
    assert "scale=416:416" in object_command[object_command.index("-vf") + 1]
    assert "pad=416:416" in object_command[object_command.index("-vf") + 1]


def test_frame_source_rejects_unsupported_size() -> None:
    with pytest.raises(ValueError, match="unsupported frame size"):
        FfmpegFrameSource("rtsp://127.0.0.1:8554/front", size=320)


def test_frame_source_preserves_legacy_positional_injection_arguments() -> None:
    factory = lambda command: FakeProcess(b"")
    sleeper = lambda seconds: None

    source = FfmpegFrameSource(
        "rtsp://127.0.0.1:8554/front", 1.0, 256, factory, sleeper, 30.0, 1.0
    )

    assert "crop=" not in source._command[source._command.index("-vf") + 1]


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


class BlockingStream:
    def __init__(self, released: threading.Event) -> None:
        self._released = released

    def read(self, size: int) -> bytes:
        self._released.wait(timeout=2)
        return b""


class BlockingProcess(FakeProcess):
    def __init__(self) -> None:
        self.released = threading.Event()
        self.stdout = BlockingStream(self.released)
        self.returncode = None

    def terminate(self) -> None:
        self.returncode = 0
        self.released.set()

    def kill(self) -> None:
        self.returncode = -9
        self.released.set()


def test_stalled_process_is_terminated_and_recreated() -> None:
    processes: list[BlockingProcess] = []

    def factory(command: list[str]) -> BlockingProcess:
        process = BlockingProcess()
        processes.append(process)
        return process

    source = FfmpegFrameSource(
        "rtsp://127.0.0.1:8554/xiaobai",
        process_factory=factory,
        stall_timeout=0.05,
        watchdog_interval=0.01,
    )

    source.start()
    deadline = time.monotonic() + 2
    while len(processes) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    source.stop()

    assert len(processes) >= 2
    assert processes[0].returncode == 0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_production_filter_letterboxes_widescreen_frame_without_distortion() -> None:
    command = build_ffmpeg_command("ignored", 1.0)
    video_filter = command[command.index("-vf") + 1]
    completed = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=320x180:r=1:d=1",
            "-vf",
            video_filter,
            "-frames:v",
            "1",
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    frame = np.frombuffer(completed.stdout, dtype=np.uint8).reshape(256, 256, 3)

    assert np.allclose(frame[10, 128], [128, 128, 128], atol=4)
    assert frame[128, 128, 2] > 240
    assert frame[128, 128, :2].max() < 10


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_production_filter_crops_right_half_before_scaling() -> None:
    region = DetectionRegion(0.5, 0.0, 0.5, 1.0)
    command = build_ffmpeg_command("ignored", 1.0, 256, region)
    video_filter = command[command.index("-vf") + 1]
    completed = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=160x180:d=1[left];color=c=blue:s=160x180:d=1[right];[left][right]hstack",
            "-vf",
            video_filter,
            "-frames:v",
            "1",
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    frame = np.frombuffer(completed.stdout, dtype=np.uint8).reshape(256, 256, 3)

    assert np.allclose(frame[128, 128], [255, 0, 0], atol=4)
    assert np.allclose(frame[10, 128], [255, 0, 0], atol=4)
    assert np.allclose(frame[128, 0], [128, 128, 128], atol=4)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_cropped_frames_keep_out_of_region_motion_from_scene_gate() -> None:
    region = DetectionRegion(0.5, 0.0, 0.5, 1.0)
    command = build_ffmpeg_command("ignored", 1.0, 256, region)
    video_filter = command[command.index("-vf") + 1]
    completed = subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            (
                "color=c=black:s=320x180:r=1:d=5,"
                "drawbox=x=0:y=0:w=160:h=180:color=white:t=fill:enable='eq(n,1)',"
                "drawbox=x=160:y=0:w=160:h=180:color=white:t=fill:enable='eq(n,3)'"
            ),
            "-vf",
            video_filter,
            "-frames:v",
            "5",
            "-pix_fmt",
            "bgr24",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        check=True,
        capture_output=True,
    )
    frames = np.frombuffer(completed.stdout, dtype=np.uint8).reshape(5, 256, 256, 3)
    gate = SceneChangeGate()

    assert [gate.changed(frame) for frame in frames] == [True, False, False, True, False]
