import subprocess
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen


def build_ffmpeg_command(rtsp_url: str, duration_seconds: int) -> list[str]:
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-t",
        str(duration_seconds),
        "-i",
        rtsp_url,
        "-map",
        "0:v:0",
        "-an",
        "-f",
        "null",
        "-",
    ]


def decode(rtsp_url: str, duration_seconds: int) -> tuple[bool, int, str]:
    started = time.monotonic()
    completed = subprocess.run(
        build_ffmpeg_command(rtsp_url, duration_seconds),
        timeout=duration_seconds + 60,
        check=False,
        capture_output=True,
        text=True,
    )
    elapsed = round(time.monotonic() - started)
    return completed.returncode == 0, elapsed, completed.stderr[-2000:]


def wait_for_snapshot(api_url: str, stream: str, max_seconds: int) -> int | None:
    started = time.monotonic()
    query = urlencode({"src": stream, "width": 320})
    while time.monotonic() - started <= max_seconds:
        try:
            with urlopen(f"{api_url}/api/frame.jpeg?{query}", timeout=5) as response:
                if response.status == 200 and response.headers.get_content_type() == "image/jpeg":
                    return round(time.monotonic() - started)
        except (URLError, TimeoutError):
            time.sleep(2)
    return None
