import shutil
import subprocess

import cv2
import numpy as np
import pytest

from daihougou.camera_snapshot import (
    CameraSnapshotter,
    SnapshotUnavailable,
    build_snapshot_command,
)


def test_snapshot_command_captures_one_uncropped_bounded_jpeg() -> None:
    url = "rtsp://camera/room"
    command = build_snapshot_command(url)

    assert command[:4] == ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel"]
    assert command[command.index("-rtsp_transport") + 1] == "tcp"
    assert command[command.index("-i") + 1] == url
    assert command[command.index("-frames:v") + 1] == "1"
    assert command[command.index("-c:v") + 1] == "mjpeg"
    assert command[command.index("-q:v") + 1] == "4"
    assert command[command.index("-f") + 1] == "image2pipe"
    video_filter = command[command.index("-vf") + 1]
    assert "min(1280,iw)" in video_filter
    assert "min(1280,ih)" in video_filter
    assert "force_original_aspect_ratio=decrease" in video_filter
    assert "crop=" not in video_filter
    assert all(option in command for option in ("-an", "-sn", "-dn"))


def test_snapshotter_returns_jpeg_bytes() -> None:
    calls: list[tuple[list[str], float]] = []

    def runner(command: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
        calls.append((command, timeout))
        return subprocess.CompletedProcess(command, 0, b"\xff\xd8jpeg", b"")

    result = CameraSnapshotter(runner=runner).capture("rtsp://camera/room")

    assert result == b"\xff\xd8jpeg"
    assert calls[0][0][calls[0][0].index("-i") + 1] == "rtsp://camera/room"
    assert calls[0][1] == 10.0


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.CompletedProcess(
            ["ffmpeg"],
            1,
            b"",
            b"rtsp://owner:password@camera/private failure",
        ),
        subprocess.CompletedProcess(["ffmpeg"], 0, b"", b""),
        subprocess.CompletedProcess(["ffmpeg"], 0, b"not-a-jpeg", b""),
        subprocess.TimeoutExpired(
            ["ffmpeg", "rtsp://owner:password@camera/private"], 10
        ),
        OSError("rtsp://owner:password@camera/private"),
    ],
)
def test_snapshotter_translates_process_failures_without_sensitive_details(
    failure: subprocess.CompletedProcess[bytes] | Exception,
) -> None:
    def runner(command: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
        if isinstance(failure, Exception):
            raise failure
        return failure

    with pytest.raises(SnapshotUnavailable) as raised:
        CameraSnapshotter(runner=runner).capture("rtsp://camera/room")

    assert str(raised.value) == "camera_snapshot_unavailable"
    assert raised.value.__cause__ is None
    assert "password" not in repr(raised.value)
    assert "rtsp://" not in repr(raised.value)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
@pytest.mark.parametrize(
    ("source_size", "expected_size"),
    [("1920x1080", (1280, 720)), ("640x360", (640, 360))],
)
def test_production_snapshot_filter_caps_without_upscaling(
    source_size: str, expected_size: tuple[int, int]
) -> None:
    production = build_snapshot_command("ignored")
    suffix = production[production.index("-i") + 2 :]
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
            f"color=c=red:s={source_size}:d=1",
            *suffix,
        ],
        check=True,
        capture_output=True,
    )
    image = cv2.imdecode(np.frombuffer(completed.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)

    assert image is not None
    expected_width, expected_height = expected_size
    assert image.shape[:2] == (expected_height, expected_width)
