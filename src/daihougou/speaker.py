import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

Runner = Callable[[list[str], dict[str, str], int], tuple[int, str, str]]
REAUTH_MARKERS = (
    "login auth failed",
    "exception on login",
    "verification code",
    "eof when reading a line",
    "验证码",
)


@dataclass(frozen=True)
class SpeakResult:
    success: bool
    latency_ms: int
    code: int | str | None
    error: str = ""


class Speaker(Protocol):
    def speak(self, text: str) -> SpeakResult: ...


def _run(command: list[str], env: dict[str, str], timeout: int) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        env=env,
        timeout=timeout,
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return completed.returncode, completed.stdout, completed.stderr


class DirectSpeaker:
    def __init__(self, user: str, password: str, did: str, run: Runner = _run) -> None:
        if not user or not password or not did:
            raise ValueError("MI_USER, MI_PASS, and MI_DID are required")
        safe_environment = {
            key: os.environ[key]
            for key in ("HOME", "LANG", "LC_ALL", "PATH", "PYTHONPATH")
            if key in os.environ
        }
        self._env = {
            **safe_environment,
            "MI_USER": user,
            "MI_PASS": password,
            "MI_DID": did,
        }
        self._did = did
        self._run = run

    def speak(self, text: str) -> SpeakResult:
        payload = json.dumps({"did": self._did, "siid": 5, "aiid": 3, "in": [text]})
        started = time.monotonic()
        try:
            returncode, stdout, stderr = self._run(
                ["python", "-m", "miservice", "action", payload], self._env, 30
            )
        except subprocess.TimeoutExpired:
            latency_ms = round((time.monotonic() - started) * 1000)
            return SpeakResult(False, latency_ms, "timeout", "timeout")

        latency_ms = round((time.monotonic() - started) * 1000)
        lowered_stderr = stderr.lower()
        if returncode != 0 and any(marker in lowered_stderr for marker in REAUTH_MARKERS):
            return SpeakResult(False, latency_ms, "reauth_required", "reauth_required")
        try:
            body = json.loads(stdout)
        except json.JSONDecodeError:
            body = None
        code = body["code"] if isinstance(body, dict) and "code" in body else "invalid_response"
        if code == 70016:
            return SpeakResult(False, latency_ms, "reauth_required", "reauth_required")
        success = returncode == 0 and code == 0
        return SpeakResult(success, latency_ms, code, "subprocess_error" if stderr else "")
