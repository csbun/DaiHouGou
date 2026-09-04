from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SpeakResult:
    success: bool
    latency_ms: int
    code: int | str | None
    error: str = ""


class Speaker(Protocol):
    def speak(self, text: str) -> SpeakResult: ...


class MiNAService(Protocol):
    async def text_to_speech(self, device_id: str, text: str) -> bool: ...


def is_auth_error(error: BaseException) -> bool:
    text = str(error).lower()
    return any(
        marker in text
        for marker in (
            "auth failed",
            "login failed",
            "unauthorized",
            "verification code",
            "error 401",
            "70016",
        )
    )


class MiNASpeaker:
    def __init__(
        self,
        device_id: str,
        service_factory: Callable[[], MiNAService],
        loop: asyncio.AbstractEventLoop,
        *,
        timeout_seconds: float = 30.0,
        is_authorized: Callable[[], bool] = lambda: True,
        on_auth_required: Callable[[], None] | None = None,
    ) -> None:
        if not device_id:
            raise ValueError("MiNA device id is required")
        self._device_id = device_id
        self._service_factory = service_factory
        self._loop = loop
        self._timeout_seconds = timeout_seconds
        self._is_authorized = is_authorized
        self._on_auth_required = on_auth_required

    def speak(self, text: str) -> SpeakResult:
        started = time.monotonic()
        if not self._is_authorized():
            return SpeakResult(
                False,
                self._latency_ms(started),
                "reauth_required",
                "reauth_required",
            )
        if self._loop.is_closed() or not self._loop.is_running():
            return SpeakResult(
                False,
                self._latency_ms(started),
                "speaker_error",
                "speaker_error",
            )
        coroutine = None
        future: concurrent.futures.Future[bool] | None = None
        try:
            coroutine = self._service_factory().text_to_speech(self._device_id, text)
            future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
            success = bool(future.result(timeout=self._timeout_seconds))
        except (TimeoutError, concurrent.futures.TimeoutError):
            if future is not None:
                future.cancel()
            elif coroutine is not None and inspect.iscoroutine(coroutine):
                coroutine.close()
            return SpeakResult(False, self._latency_ms(started), "timeout", "timeout")
        except Exception as error:  # noqa: BLE001 - third-party service boundary
            if future is None and coroutine is not None and inspect.iscoroutine(coroutine):
                coroutine.close()
            code = "reauth_required" if is_auth_error(error) else "speaker_error"
            if code == "reauth_required":
                self._require_reauthentication()
            return SpeakResult(False, self._latency_ms(started), code, code)
        return SpeakResult(
            success,
            self._latency_ms(started),
            0 if success else "speaker_error",
            "" if success else "speaker_error",
        )

    @staticmethod
    def _latency_ms(started: float) -> int:
        return round((time.monotonic() - started) * 1000)

    def _require_reauthentication(self) -> None:
        if self._on_auth_required is None:
            return
        try:
            self._on_auth_required()
        except Exception:  # noqa: BLE001 - the safe result must still be returned
            return
