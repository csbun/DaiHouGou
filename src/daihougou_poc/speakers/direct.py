import json
import os
import subprocess
import time
from collections.abc import Callable

from daihougou_poc.speakers.base import SpeakResult

Runner = Callable[[list[str], dict[str, str], int], tuple[int, str, str]]


def _run(command: list[str], env: dict[str, str], timeout: int) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        env=env,
        timeout=timeout,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout, completed.stderr


class DirectSpeaker:
    def __init__(self, user: str, password: str, did: str, run: Runner = _run) -> None:
        if not user or not password or not did:
            raise ValueError("MI_USER, MI_PASS, and MI_DID are required")
        self._env = {**os.environ, "MI_USER": user, "MI_PASS": password, "MI_DID": did}
        self._did = did
        self._run = run

    def speak(self, text: str) -> SpeakResult:
        payload = json.dumps({"did": self._did, "siid": 5, "aiid": 3, "in": [text]})
        started = time.monotonic()
        returncode, stdout, stderr = self._run(
            ["python", "-m", "miservice", "action", payload], self._env, 30
        )
        latency_ms = round((time.monotonic() - started) * 1000)
        try:
            body = json.loads(stdout)
        except json.JSONDecodeError:
            body = {}
        code = body.get("code", returncode)
        success = returncode == 0 and code in {0, None}
        return SpeakResult(success=success, latency_ms=latency_ms, code=code, error=stderr[-500:])
