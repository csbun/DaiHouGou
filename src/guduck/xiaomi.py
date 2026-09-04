from __future__ import annotations

import asyncio
import inspect
import secrets
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol
from uuid import uuid4

from miservice import MiAccount, MiNAService

from guduck.speaker import MiNASpeaker, Speaker, is_auth_error
from guduck.storage import (
    BindingSaveResult,
    DatabaseTokenStore,
    DiscoveredSpeaker,
    SpeakerBinding,
    Storage,
    TestResult,
)

AUTH_ATTEMPT_SECONDS = 600.0
TEST_PHRASE = "音箱配置测试"


class AccountLike(Protocol):
    async def login(self, sid: str) -> bool: ...


class MiNAServiceLike(Protocol):
    async def device_list(self) -> Sequence[Mapping[str, Any]] | None: ...

    async def text_to_speech(self, device_id: str, text: str) -> bool: ...


class SpeakerRuntime(Protocol):
    async def replace_speakers(
        self,
        speakers: Mapping[str, Speaker],
        available_ids: Sequence[str],
    ) -> None: ...

    def set_available_ids(self, available_ids: Sequence[str]) -> None: ...

    def set_auth_status(self, status: str) -> None: ...


AccountFactory = Callable[..., AccountLike]
MiNAFactory = Callable[[AccountLike], MiNAServiceLike]
ChangeCallback = Callable[[], Awaitable[None] | None]


class XiaomiStateError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PublicSpeaker:
    id: str
    name: str
    mina_name: str
    hardware: str
    checked: bool
    available: bool
    test_status: str


@dataclass(frozen=True)
class XiaomiStatus:
    state: str
    attempt_id: str | None
    otp_method: str | None
    expires_at: float | None
    error_code: str | None
    devices: tuple[PublicSpeaker, ...]
    bindings: tuple[PublicSpeaker, ...]


@dataclass
class _MemoryTokenStore:
    token: dict[str, object] | None = None

    async def load_token(self) -> dict[str, object] | None:
        return None if self.token is None else dict(self.token)

    async def save_token(self, token: Mapping[str, object] | None = None) -> None:
        self.token = None if token is None else dict(token)


@dataclass
class _Candidate:
    binding_id: str
    discovered: DiscoveredSpeaker
    display_name: str
    checked: bool
    test_status: str = "unknown"


@dataclass
class _AuthAttempt:
    id: str
    username: str
    password: str
    expires_at: float
    state: str = "authenticating"
    otp_method: str | None = None
    otp_future: asyncio.Future[str] | None = None
    error_code: str | None = None
    token_store: _MemoryTokenStore = field(default_factory=_MemoryTokenStore)
    account: AccountLike | None = None
    mina: MiNAServiceLike | None = None
    candidates: dict[str, _Candidate] = field(default_factory=dict)
    task: asyncio.Task[None] | None = None
    expiry_task: asyncio.Task[None] | None = None


class XiaomiAccountManager:
    def __init__(
        self,
        storage: Storage,
        *,
        account_factory: AccountFactory = MiAccount,
        mina_factory: MiNAFactory = MiNAService,
        clock: Callable[[], float] = time.monotonic,
        attempt_seconds: float = AUTH_ATTEMPT_SECONDS,
        on_change: ChangeCallback | None = None,
        speaker_runtime: SpeakerRuntime | None = None,
    ) -> None:
        self._storage = storage
        self._account_factory = account_factory
        self._mina_factory = mina_factory
        self._clock = clock
        self._attempt_seconds = attempt_seconds
        self._on_change = on_change
        self._speaker_runtime = speaker_runtime
        self._attempt: _AuthAttempt | None = None
        self._active_account: AccountLike | None = None
        self._active_mina: MiNAServiceLike | None = None
        self._active_snapshot: dict[str, _Candidate] = {}
        self._state = "unconfigured"
        self._error_code: str | None = None
        self._lock = asyncio.Lock()
        self._playback_auth_required = threading.Event()

    async def start(self) -> None:
        account = self._storage.get_xiaomi_account()
        bindings = tuple(binding for binding in self._storage.list_speaker_bindings() if binding.bound)
        if account is None:
            self._state = "unconfigured"
            await self._publish_runtime(replace=True)
            return
        token_store = DatabaseTokenStore(self._storage, account.username)
        self._active_account = self._account_factory(
            None,
            account.username,
            "",
            token_store=token_store,
            otp_callback=self._reject_runtime_otp,
        )
        self._active_mina = self._mina_factory(self._active_account)
        self._state = "ready" if bindings else "devices_ready"
        await self._publish_runtime(replace=True)

    async def stop(self) -> None:
        async with self._lock:
            attempt = self._attempt
            self._attempt = None
        if attempt is not None:
            await self._finish_attempt(attempt, "cancelled")
        await self._close_account(self._active_account)
        self._active_account = None
        self._active_mina = None

    async def start_auth(self, username: str, password: str) -> str:
        username = username.strip()
        if not username or len(username) > 200:
            raise XiaomiStateError("invalid_username")
        if not password or len(password) > 200:
            raise XiaomiStateError("invalid_password")
        previous: _AuthAttempt | None
        async with self._lock:
            previous = self._attempt
            attempt = _AuthAttempt(
                id=secrets.token_urlsafe(24),
                username=username,
                password=password,
                expires_at=self._clock() + self._attempt_seconds,
            )
            self._attempt = attempt
            attempt.task = asyncio.create_task(
                self._authenticate(attempt),
                name="xiaomi-account-auth",
            )
            attempt.expiry_task = asyncio.create_task(
                self._expire_attempt(attempt),
                name="xiaomi-account-auth-expiry",
            )
        if previous is not None:
            await self._finish_attempt(previous, "cancelled")
        return attempt.id

    def status(self, attempt_id: str | None = None) -> XiaomiStatus:
        attempt = self._attempt
        if attempt_id is not None and (attempt is None or attempt.id != attempt_id):
            raise XiaomiStateError("unknown_auth_attempt")
        if attempt is not None:
            return XiaomiStatus(
                state=attempt.state,
                attempt_id=attempt.id,
                otp_method=attempt.otp_method,
                expires_at=attempt.expires_at,
                error_code=attempt.error_code,
                devices=self._public_candidates(attempt.candidates),
                bindings=self.display_bindings(),
            )
        return XiaomiStatus(
            state=self._state,
            attempt_id=None,
            otp_method=None,
            expires_at=None,
            error_code=self._error_code,
            devices=self._public_candidates(self._active_snapshot),
            bindings=self.display_bindings(),
        )

    async def submit_otp(self, attempt_id: str, code: str) -> None:
        attempt = self._require_attempt(attempt_id, "otp_required")
        code = code.strip()
        if not 4 <= len(code) <= 12 or not code.isdigit():
            raise XiaomiStateError("invalid_otp")
        future = attempt.otp_future
        if future is None or future.done():
            raise XiaomiStateError("otp_not_required")
        future.set_result(code)
        attempt.otp_method = None
        attempt.state = "authenticating"

    async def cancel_auth(self, attempt_id: str) -> None:
        attempt = self._require_attempt(attempt_id)
        await self._finish_attempt(attempt, "cancelled")

    async def refresh_devices(self) -> XiaomiStatus:
        mina = self._active_mina
        if mina is None:
            raise XiaomiStateError("account_unconfigured")
        previous = self._active_snapshot
        try:
            devices = self._normalize_devices(await mina.device_list())
        except Exception as error:  # noqa: BLE001 - external library boundary
            if self._is_auth_error(error):
                self._state = "auth_required"
                self._error_code = "auth_required"
                self._playback_auth_required.set()
                await self._publish_runtime(replace=False)
                raise XiaomiStateError("auth_required") from None
            self._active_snapshot = previous
            self._error_code = "device_refresh_failed"
            raise XiaomiStateError("device_refresh_failed") from None
        self._storage.refresh_known_speakers(devices)
        self._active_snapshot = self._merge_candidates(devices)
        self._error_code = None
        await self._publish_runtime(replace=False)
        return self.status()

    async def save_bindings(
        self,
        selected_ids: Sequence[str],
        confirmation_id: str | None = None,
        display_names: Mapping[str, str] | None = None,
    ) -> BindingSaveResult:
        old_account_to_close: AccountLike | None = None
        attempt = self._attempt
        candidates = attempt.candidates if attempt and attempt.state == "devices_ready" else self._active_snapshot
        public_ids = frozenset(candidates)
        existing = {binding.binding_id: binding for binding in self._storage.list_speaker_bindings()}
        allowed = public_ids | frozenset(
            binding_id for binding_id, binding in existing.items() if binding.bound
        )
        selected = tuple(dict.fromkeys(selected_ids))
        if not frozenset(selected) <= allowed:
            raise XiaomiStateError("unknown_speaker_binding")
        names = display_names or {}
        for binding_id, name in names.items():
            if binding_id not in allowed or not 1 <= len(name.strip()) <= 50:
                raise XiaomiStateError("invalid_speaker_name")

        selected_candidates = [
            DiscoveredSpeaker(
                candidate.discovered.device_id,
                candidate.discovered.mina_name,
                candidate.discovered.hardware,
                candidate.discovered.miot_did,
                binding_id,
            )
            for binding_id, candidate in candidates.items()
            if binding_id in selected
        ]
        selected_test_statuses = {
            binding_id: candidate.test_status
            for binding_id, candidate in candidates.items()
            if binding_id in selected and candidate.test_status != "unknown"
        }
        if attempt is not None and attempt.state == "devices_ready":
            token = attempt.token_store.token
            if token is None:
                raise XiaomiStateError("auth_failed")
            result = self._storage.replace_xiaomi_configuration(
                attempt.username,
                self._serialize_token(token),
                selected_candidates,
                selected,
                display_names=names,
                test_statuses=selected_test_statuses,
                confirmation_id=confirmation_id,
            )
            if not result.saved:
                return result
            old_account_to_close = self._active_account
            self._active_account = attempt.account
            self._active_mina = attempt.mina
            self._active_snapshot = dict(attempt.candidates)
            attempt.account = None
            attempt.mina = None
            async with self._lock:
                if self._attempt is attempt:
                    self._attempt = None
            await self._finish_attempt(attempt, "ready" if selected else "devices_ready")
        else:
            account = self._storage.get_xiaomi_account()
            if account is None:
                raise XiaomiStateError("account_unconfigured")
            result = self._storage.replace_xiaomi_configuration(
                account.username,
                account.token_json,
                selected_candidates,
                selected,
                display_names=names,
                test_statuses=selected_test_statuses,
                confirmation_id=confirmation_id,
            )
            if not result.saved:
                return result
        self._state = "ready" if selected else "devices_ready"
        self._error_code = None
        try:
            await self._publish_runtime(replace=True)
        finally:
            await self._close_account(old_account_to_close)
        return result

    async def test_binding(self, binding_id: str) -> TestResult:
        attempt = self._attempt
        candidates = attempt.candidates if attempt and attempt.state == "devices_ready" else self._active_snapshot
        candidate = candidates.get(binding_id)
        mina = attempt.mina if attempt and candidate is not None else self._active_mina
        if candidate is None:
            binding = next(
                (
                    item
                    for item in self._storage.list_speaker_bindings()
                    if item.binding_id == binding_id
                ),
                None,
            )
            if binding is None:
                raise XiaomiStateError("unknown_speaker_binding")
            device_id = binding.device_id
        else:
            device_id = candidate.discovered.device_id
        if mina is None:
            raise XiaomiStateError("account_unconfigured")
        try:
            success = bool(await mina.text_to_speech(device_id, TEST_PHRASE))
        except Exception as error:  # noqa: BLE001 - external library boundary
            success = False
            if self._is_auth_error(error):
                if attempt is not None and candidate is not None:
                    attempt.state = "auth_required"
                    attempt.error_code = "auth_required"
                else:
                    self._state = "auth_required"
                    self._error_code = "auth_required"
        if candidate is not None:
            candidate.test_status = "success" if success else "failure"
        if any(
            binding.binding_id == binding_id
            for binding in self._storage.list_speaker_bindings()
        ):
            self._storage.set_speaker_test_status(
                binding_id,
                "success" if success else "failure",
            )
        if attempt is None and self._state == "auth_required":
            self._playback_auth_required.set()
            await self._publish_runtime(replace=False)
        else:
            await self._notify_change()
        return TestResult(success)

    def has_available_binding(self, binding_id: str | None) -> bool:
        return any(
            binding.binding_id == binding_id and binding.bound and binding.available
            for binding in self._storage.list_speaker_bindings()
        ) and self._state == "ready"

    def display_bindings(self) -> tuple[PublicSpeaker, ...]:
        return tuple(
            self._public_binding(binding)
            for binding in self._storage.list_speaker_bindings()
            if binding.bound
        )

    def runtime_bindings(self) -> tuple[SpeakerBinding, ...]:
        return tuple(binding for binding in self._storage.list_speaker_bindings() if binding.bound)

    def runtime_speakers(
        self,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> Mapping[str, Speaker]:
        if self._active_mina is None:
            return {}
        resolved_loop = loop or asyncio.get_running_loop()
        return {
            binding.binding_id: MiNASpeaker(
                binding.device_id,
                lambda service=self._active_mina: service,
                resolved_loop,
                is_authorized=lambda: not self._playback_auth_required.is_set(),
                on_auth_required=self._playback_auth_required.set,
            )
            for binding in self.runtime_bindings()
        }

    async def mark_auth_required(self) -> None:
        self._state = "auth_required"
        self._error_code = "auth_required"
        self._playback_auth_required.set()
        await self._publish_runtime(replace=False)

    async def _authenticate(self, attempt: _AuthAttempt) -> None:
        try:
            attempt.account = self._account_factory(
                None,
                attempt.username,
                attempt.password,
                token_store=attempt.token_store,
                otp_callback=lambda method: self._request_otp(attempt, method),
            )
            attempt.mina = self._mina_factory(attempt.account)
            if not await attempt.account.login("micoapi"):
                raise XiaomiStateError("auth_failed")
            if self._attempt is not attempt:
                return
            attempt.password = ""
            attempt.state = "fetching_devices"
            devices = self._normalize_devices(await attempt.mina.device_list())
            if self._attempt is not attempt:
                return
            attempt.candidates = self._merge_candidates(devices)
            attempt.state = "devices_ready"
            attempt.error_code = None
        except asyncio.CancelledError:
            raise
        except XiaomiStateError as error:
            attempt.password = ""
            attempt.error_code = error.code
            await self._finish_attempt(attempt, "failed")
        except Exception as error:  # noqa: BLE001 - external library boundary
            attempt.password = ""
            attempt.error_code = (
                "auth_required" if self._is_auth_error(error) else "auth_failed"
            )
            await self._finish_attempt(attempt, "failed")
        finally:
            attempt.password = ""

    async def _request_otp(self, attempt: _AuthAttempt, method: str) -> str:
        if self._attempt is not attempt:
            raise XiaomiStateError("unknown_auth_attempt")
        loop = asyncio.get_running_loop()
        attempt.otp_method = method
        attempt.otp_future = loop.create_future()
        attempt.state = "otp_required"
        try:
            return await attempt.otp_future
        finally:
            attempt.otp_future = None

    async def _expire_attempt(self, attempt: _AuthAttempt) -> None:
        delay = max(0.0, attempt.expires_at - self._clock())
        await asyncio.sleep(delay)
        async with self._lock:
            if self._attempt is not attempt:
                return
        await self._finish_attempt(attempt, "expired")

    async def _finish_attempt(self, attempt: _AuthAttempt, state: str) -> None:
        attempt.password = ""
        if attempt.otp_future is not None and not attempt.otp_future.done():
            attempt.otp_future.cancel()
        current = asyncio.current_task()
        for task in (attempt.task, attempt.expiry_task):
            if task is not None and task is not current and not task.done():
                task.cancel()
        pending = tuple(
            task
            for task in (attempt.task, attempt.expiry_task)
            if task is not None and task is not current and not task.done()
        )
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        await self._close_account(attempt.account)
        attempt.account = None
        attempt.mina = None
        attempt.token_store.token = None
        attempt.candidates.clear()
        attempt.state = state

    def _require_attempt(
        self,
        attempt_id: str,
        state: str | None = None,
    ) -> _AuthAttempt:
        attempt = self._attempt
        if attempt is None or not secrets.compare_digest(attempt.id, attempt_id):
            raise XiaomiStateError("unknown_auth_attempt")
        if state is not None and attempt.state != state:
            raise XiaomiStateError("otp_not_required")
        return attempt

    def _merge_candidates(
        self,
        devices: Sequence[DiscoveredSpeaker],
    ) -> dict[str, _Candidate]:
        persisted_by_device = {
            binding.device_id: binding for binding in self._storage.list_speaker_bindings()
        }
        previous_by_device = {
            candidate.discovered.device_id: candidate
            for candidate in self._active_snapshot.values()
        }
        candidates: dict[str, _Candidate] = {}
        for device in devices:
            persisted = persisted_by_device.get(device.device_id)
            previous = previous_by_device.get(device.device_id)
            binding_id = (
                persisted.binding_id
                if persisted is not None
                else previous.binding_id
                if previous is not None
                else str(uuid4())
            )
            candidates[binding_id] = _Candidate(
                binding_id=binding_id,
                discovered=device,
                display_name=(
                    persisted.display_name
                    if persisted is not None
                    else previous.display_name
                    if previous is not None
                    else device.mina_name
                ),
                checked=bool(persisted and persisted.bound),
                test_status=(
                    persisted.test_status
                    if persisted is not None
                    else previous.test_status
                    if previous is not None
                    else "unknown"
                ),
            )
        return candidates

    @staticmethod
    def _normalize_devices(
        raw_devices: Sequence[Mapping[str, Any]] | None,
    ) -> tuple[DiscoveredSpeaker, ...]:
        devices: list[DiscoveredSpeaker] = []
        seen: set[str] = set()
        for raw in raw_devices or ():
            device_id = raw.get("deviceID")
            if not isinstance(device_id, str) or not device_id or device_id in seen:
                continue
            seen.add(device_id)
            name = raw.get("name")
            hardware = raw.get("hardware")
            miot_did = raw.get("miotDID")
            devices.append(
                DiscoveredSpeaker(
                    device_id=device_id,
                    mina_name=name.strip() if isinstance(name, str) and name.strip() else "未命名音箱",
                    hardware=(
                        hardware.strip()
                        if isinstance(hardware, str) and hardware.strip()
                        else "未知型号"
                    ),
                    miot_did=miot_did if isinstance(miot_did, str) and miot_did else None,
                )
            )
        return tuple(devices)

    @staticmethod
    def _public_candidates(candidates: Mapping[str, _Candidate]) -> tuple[PublicSpeaker, ...]:
        return tuple(
            PublicSpeaker(
                id=candidate.binding_id,
                name=candidate.display_name,
                mina_name=candidate.discovered.mina_name,
                hardware=candidate.discovered.hardware,
                checked=candidate.checked,
                available=True,
                test_status=candidate.test_status,
            )
            for candidate in sorted(candidates.values(), key=lambda item: item.display_name)
        )

    @staticmethod
    def _public_binding(binding: SpeakerBinding) -> PublicSpeaker:
        return PublicSpeaker(
            id=binding.binding_id,
            name=binding.display_name,
            mina_name=binding.mina_name,
            hardware=binding.hardware,
            checked=binding.bound,
            available=binding.available,
            test_status=binding.test_status,
        )

    @staticmethod
    def _serialize_token(token: Mapping[str, object]) -> str:
        import json

        return json.dumps(dict(token), ensure_ascii=False)

    @staticmethod
    def _is_auth_error(error: Exception) -> bool:
        return is_auth_error(error)

    async def _notify_change(self) -> None:
        if self._on_change is None:
            return
        result = self._on_change()
        if inspect.isawaitable(result):
            await result

    async def _publish_runtime(self, *, replace: bool) -> None:
        runtime = self._speaker_runtime
        ready = self._state == "ready"
        if ready:
            self._playback_auth_required.clear()
        elif self._state == "auth_required":
            self._playback_auth_required.set()
        if runtime is not None:
            available_ids = tuple(
                binding.binding_id
                for binding in self.runtime_bindings()
                if ready and binding.available
            )
            if replace:
                await runtime.replace_speakers(
                    self.runtime_speakers(),
                    available_ids,
                )
            else:
                runtime.set_available_ids(available_ids)
            runtime.set_auth_status(
                "reauth_required" if self._state == "auth_required" else self._state
            )
        await self._notify_change()

    async def _reject_runtime_otp(self, method: str) -> str:
        del method
        raise XiaomiStateError("auth_required")

    @staticmethod
    async def _close_account(account: AccountLike | None) -> None:
        if account is None:
            return
        close = getattr(account, "close", None)
        if close is None:
            session = getattr(account, "_session", None)
            close = getattr(session, "close", None)
        if close is None:
            return
        result = close()
        if inspect.isawaitable(result):
            await result
