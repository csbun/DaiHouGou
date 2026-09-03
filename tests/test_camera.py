import subprocess

from guduck_poc import camera
from guduck_poc.camera import build_ffmpeg_command


def test_decode_uses_tcp_without_audio_or_output_files() -> None:
    command = build_ffmpeg_command("rtsp://127.0.0.1:8554/xiaobai", 1800)
    assert command == [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-t",
        "1800",
        "-i",
        "rtsp://127.0.0.1:8554/xiaobai",
        "-map",
        "0:v:0",
        "-an",
        "-f",
        "null",
        "-",
    ]


def test_decode_timeout_is_returned_as_a_failed_measurement(monkeypatch) -> None:
    def run(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"], stderr="ffmpeg hung")

    monkeypatch.setattr(camera.subprocess, "run", run)

    success, _, error = camera.decode("rtsp://127.0.0.1:8554/xiaobai", 1)

    assert success is False
    assert error == "ffmpeg hung"
