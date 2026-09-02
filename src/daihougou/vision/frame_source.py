import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO, Protocol

import numpy as np
import numpy.typing as npt

from daihougou.detection_region import DetectionRegion, FULL_FRAME_REGION

PERSON_FRAME_SIZE = 256
OBJECT_FRAME_SIZE = 416
FRAME_WIDTH = PERSON_FRAME_SIZE
FRAME_HEIGHT = PERSON_FRAME_SIZE
FRAME_BYTES = PERSON_FRAME_SIZE * PERSON_FRAME_SIZE * 3
DEFAULT_STALL_TIMEOUT_SECONDS = 30.0
DEFAULT_WATCHDOG_INTERVAL_SECONDS = 1.0


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


def build_ffmpeg_command(
    stream_url: str,
    fps: float,
    size: int = PERSON_FRAME_SIZE,
    region: DetectionRegion = FULL_FRAME_REGION,
) -> list[str]:
    if size not in {PERSON_FRAME_SIZE, OBJECT_FRAME_SIZE}:
        raise ValueError("unsupported frame size")
    filters: list[str] = []
    if not region.is_full_frame:
        filters.append(
            f"crop=w=iw*{region.width:.6f}:h=ih*{region.height:.6f}:"
            f"x=iw*{region.x:.6f}:y=ih*{region.y:.6f}"
        )
    filters.extend(
        [
            f"fps={fps}",
            (
                f"scale={size}:{size}:"
                "force_original_aspect_ratio=decrease"
            ),
            f"pad={size}:{size}:(ow-iw)/2:(oh-ih)/2:color=0x808080",
        ]
    )
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
        ",".join(filters),
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


def _terminate(process: Process) -> None:
    try:
        process.terminate()
    except OSError:
        pass


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
        size: int = PERSON_FRAME_SIZE,
        process_factory: ProcessFactory = _spawn,
        sleeper: Callable[[float], None] = time.sleep,
        stall_timeout: float = DEFAULT_STALL_TIMEOUT_SECONDS,
        watchdog_interval: float = DEFAULT_WATCHDOG_INTERVAL_SECONDS,
        region: DetectionRegion = FULL_FRAME_REGION,
    ) -> None:
        if size not in {PERSON_FRAME_SIZE, OBJECT_FRAME_SIZE}:
            raise ValueError("unsupported frame size")
        self._size = size
        self._frame_bytes = size * size * 3
        self._command = build_ffmpeg_command(stream_url, fps, size, region)
        self._process_factory = process_factory
        self._sleeper = sleeper
        self._stall_timeout = stall_timeout
        self._watchdog_interval = watchdog_interval
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._watchdog_thread: threading.Thread | None = None
        self._process: Process | None = None
        self._process_started_at: float | None = None
        self._last_frame_at: float | None = None
        self._latest: FrameSample | None = None
        self._sequence = 0
        self.status = "stopped"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self.status = "starting"
        self._thread = threading.Thread(target=self._run, name="ffmpeg-frame-source", daemon=True)
        self._watchdog_thread = threading.Thread(
            target=self._watch_stalls, name="ffmpeg-frame-watchdog", daemon=True
        )
        self._thread.start()
        self._watchdog_thread.start()

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
            _terminate(process)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        if thread is not None and thread.is_alive() and process is not None:
            process.kill()
            thread.join(timeout=2)
        watchdog_thread = self._watchdog_thread
        if watchdog_thread is not None:
            watchdog_thread.join(timeout=2)
        self._thread = None
        self._watchdog_thread = None
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
            with self._condition:
                self._process = process
                self._process_started_at = time.monotonic()
                self._last_frame_at = None
            stream = process.stdout
            complete_frames = 0
            if stream is not None:
                while not self._stop.is_set():
                    payload = _read_exact(stream, self._frame_bytes)
                    if len(payload) != self._frame_bytes:
                        break
                    frame = np.frombuffer(payload, dtype=np.uint8).reshape(
                        self._size, self._size, 3
                    ).copy()
                    with self._condition:
                        self._sequence += 1
                        captured_at = time.monotonic()
                        self._latest = FrameSample(self._sequence, captured_at, frame)
                        self._last_frame_at = captured_at
                        self.status = "ready"
                        self._condition.notify_all()
                    complete_frames += 1
            if process.poll() is None:
                _terminate(process)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
            with self._condition:
                if self._process is process:
                    self._process = None
                    self._process_started_at = None
                    self._last_frame_at = None
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

    def _watch_stalls(self) -> None:
        while not self._stop.wait(self._watchdog_interval):
            with self._condition:
                process = self._process
                reference = self._last_frame_at or self._process_started_at
            if (
                process is not None
                and reference is not None
                and process.poll() is None
                and time.monotonic() - reference >= self._stall_timeout
            ):
                self.status = "degraded"
                _terminate(process)
