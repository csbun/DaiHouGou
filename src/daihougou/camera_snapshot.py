import subprocess
from collections.abc import Callable


SNAPSHOT_TIMEOUT_SECONDS = 10.0
SNAPSHOT_ERROR = "camera_snapshot_unavailable"


class SnapshotUnavailable(RuntimeError):
    pass


Runner = Callable[[list[str], float], subprocess.CompletedProcess[bytes]]


def build_snapshot_command(rtsp_url: str) -> list[str]:
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        rtsp_url,
        "-an",
        "-sn",
        "-dn",
        "-vf",
        (
            "scale=w='min(1280,iw)':h='min(1280,ih)':"
            "force_original_aspect_ratio=decrease"
        ),
        "-frames:v",
        "1",
        "-c:v",
        "mjpeg",
        "-q:v",
        "4",
        "-f",
        "image2pipe",
        "pipe:1",
    ]


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        check=False,
        timeout=timeout,
    )


class CameraSnapshotter:
    def __init__(
        self,
        runner: Runner = _run,
        timeout_seconds: float = SNAPSHOT_TIMEOUT_SECONDS,
    ) -> None:
        self._runner = runner
        self._timeout_seconds = timeout_seconds

    def capture(self, rtsp_url: str) -> bytes:
        try:
            completed = self._runner(
                build_snapshot_command(rtsp_url), self._timeout_seconds
            )
        except (OSError, subprocess.TimeoutExpired):
            raise SnapshotUnavailable(SNAPSHOT_ERROR) from None
        image = completed.stdout
        if completed.returncode != 0 or not image or not image.startswith(b"\xff\xd8"):
            raise SnapshotUnavailable(SNAPSHOT_ERROR)
        return image
