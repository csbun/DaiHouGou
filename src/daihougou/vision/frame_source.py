import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO, Protocol

import numpy as np
import numpy.typing as npt

FRAME_WIDTH = 256
FRAME_HEIGHT = 256
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 3


class Process(Protocol):
    stdout: BinaryIO | None
    returncode: int | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def kill(self) -> None: ...


ProcessFactory = Callable[[list[str]], Process]


@dataclass(frozen=True)
class FrameSample:
    sequence: int
    captured_monotonic: float
    frame: npt.NDArray[np.uint8]


def build_ffmpeg_command(stream_url: str, fps: float) -> list[str]:
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        stream_url,
        "-an",
        "-sn",
        "-dn",
        "-vf",
        f"fps={fps},scale={FRAME_WIDTH}:{FRAME_HEIGHT}",
        "-pix_fmt",
        "bgr24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def reconnect_delay(attempt: int) -> int:
    return min(2**attempt, 30)


def _spawn(command: list[str]) -> Process:
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class FfmpegFrameSource:
    def __init__(
        self,
        stream_url: str,
        fps: float = 1.0,
        process_factory: ProcessFactory = _spawn,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._command = build_ffmpeg_command(stream_url, fps)
        self._process_factory = process_factory
        self._sleeper = sleeper
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: Process | None = None
        self._latest: FrameSample | None = None
        self._sequence = 0
        self.status = "stopped"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self.status = "starting"
        self._thread = threading.Thread(target=self._run, name="ffmpeg-frame-source", daemon=True)
        self._thread.start()

    def wait_for_frame(self, after_sequence: int, timeout: float) -> FrameSample | None:
        with self._condition:
            self._condition.wait_for(
                lambda: self._latest is not None and self._latest.sequence > after_sequence,
                timeout=timeout,
            )
            if self._latest is None or self._latest.sequence <= after_sequence:
                return None
            return self._latest

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        if thread is not None and thread.is_alive() and process is not None:
            process.kill()
            thread.join(timeout=2)
        self._thread = None
        self.status = "stopped"
        with self._condition:
            self._condition.notify_all()

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                process = self._process_factory(self._command)
            except (OSError, StopIteration):
                self.status = "degraded"
                if not self._wait_backoff(attempt):
                    break
                attempt += 1
                continue
            self._process = process
            stream = process.stdout
            complete_frames = 0
            if stream is not None:
                while not self._stop.is_set():
                    payload = _read_exact(stream, FRAME_BYTES)
                    if len(payload) != FRAME_BYTES:
                        break
                    frame = np.frombuffer(payload, dtype=np.uint8).reshape(
                        FRAME_HEIGHT, FRAME_WIDTH, 3
                    ).copy()
                    with self._condition:
                        self._sequence += 1
                        self._latest = FrameSample(self._sequence, time.monotonic(), frame)
                        self.status = "ready"
                        self._condition.notify_all()
                    complete_frames += 1
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
            self._process = None
            if self._stop.is_set():
                break
            self.status = "degraded"
            attempt = 0 if complete_frames else attempt + 1
            if not self._wait_backoff(max(0, attempt - 1)):
                break

    def _wait_backoff(self, attempt: int) -> bool:
        deadline = time.monotonic() + reconnect_delay(attempt)
        while not self._stop.is_set() and time.monotonic() < deadline:
            self._sleeper(min(0.1, max(0, deadline - time.monotonic())))
        return not self._stop.is_set()
