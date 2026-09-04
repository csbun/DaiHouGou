import asyncio
from collections import deque
from pathlib import Path

import pytest

from guduck.storage import Storage
from guduck.xiaomi import TEST_PHRASE, XiaomiAccountManager, XiaomiStateError


class FakeMiNA:
    def __init__(self, device_results, tts_results=None) -> None:
        self.device_results = deque(device_results)
        self.tts_results = deque(tts_results or [True])
        self.device_list_calls = 0
        self.tts_calls: list[tuple[str, str]] = []

    async def device_list(self):
        self.device_list_calls += 1
        result = self.device_results.popleft()
        if isinstance(result, Exception):
            raise result
        return result

    async def text_to_speech(self, device_id: str, text: str) -> bool:
        self.tts_calls.append((device_id, text))
        result = self.tts_results.popleft()
        if isinstance(result, Exception):
            raise result
        return result


class FakeAccount:
    def __init__(
        self,
        username: str,
        password: str,
        token_store,
        otp_callback,
        mina: FakeMiNA,
        *,
        require_otp: bool,
        login_success: bool,
    ) -> None:
        self.username = username
        self.password = password
        self.token_store = token_store
        self.otp_callback = otp_callback
        self.mina = mina
        self.require_otp = require_otp
        self.login_success = login_success
        self.closed = False
        self.otp_value: str | None = None

    async def login(self, sid: str) -> bool:
        assert sid == "micoapi"
        if self.require_otp:
            self.otp_value = await self.otp_callback("Phone")
        if not self.login_success:
            await self.token_store.save_token()
            return False
        await self.token_store.save_token(
            {"userId": f"private-{self.username}", "micoapi": ["security", "token"]}
        )
        return True

    async def close(self) -> None:
        self.closed = True


class FakeAccountFactory:
    def __init__(
        self,
        minas: list[FakeMiNA],
        *,
        require_otp: bool = False,
        login_success: bool = True,
    ) -> None:
        self.minas = deque(minas)
        self.require_otp = require_otp
        self.login_success = login_success
        self.accounts: list[FakeAccount] = []

    def __call__(self, session, username, password, *, token_store, otp_callback):
        assert session is None
        account = FakeAccount(
            username,
            password,
            token_store,
            otp_callback,
            self.minas.popleft(),
            require_otp=self.require_otp,
            login_success=self.login_success,
        )
        self.accounts.append(account)
        return account


async def wait_for_state(
    manager: XiaomiAccountManager,
    attempt_id: str,
    expected: str,
) -> None:
    for _ in range(100):
        if manager.status(attempt_id).state == expected:
            return
        await asyncio.sleep(0.001)
    raise AssertionError(f"Xiaomi state did not become {expected}")


def device(device_id="raw-device-id", name="客厅音箱", miot_did="raw-miot-did"):
    return {
        "deviceID": device_id,
        "name": name,
        "hardware": "L05C",
        "miotDID": miot_did,
    }


def test_ajax_auth_waits_for_otp_discovers_all_and_commits_selected_binding(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        mina = FakeMiNA([[device(), device("second-secret", "卧室音箱", "second-did")]])
        accounts = FakeAccountFactory([mina], require_otp=True)
        manager = XiaomiAccountManager(
            storage,
            account_factory=accounts,
            mina_factory=lambda account: account.mina,
        )
        await manager.start()

        attempt_id = await manager.start_auth("owner@example.test", "do-not-store")
        await wait_for_state(manager, attempt_id, "otp_required")
        otp_status = manager.status(attempt_id)
        assert otp_status.otp_method == "Phone"
        assert otp_status.devices == ()

        await manager.submit_otp(attempt_id, "123456")
        await wait_for_state(manager, attempt_id, "devices_ready")
        status = manager.status(attempt_id)

        assert mina.device_list_calls == 1
        assert len(status.devices) == 2
        serialized = repr(status)
        assert "raw-device-id" not in serialized
        assert "raw-miot-did" not in serialized
        assert "second-secret" not in serialized
        assert "second-did" not in serialized
        assert "do-not-store" not in serialized
        assert storage.list_speaker_bindings() == []
        assert storage.get_xiaomi_account() is None
        assert manager._attempt is not None
        assert manager._attempt.password == ""
        selected = status.devices[0].id

        result = await manager.save_bindings([selected], display_names={selected: "门口音箱"})

        assert result.saved is True
        assert manager.status().state == "ready"
        account = storage.get_xiaomi_account()
        assert account is not None
        assert account.username == "owner@example.test"
        assert "do-not-store" not in account.token_json
        bindings = storage.list_speaker_bindings()
        assert len(bindings) == 1
        assert bindings[0].bound is True
        assert bindings[0].display_name == "门口音箱"
        assert accounts.accounts[0].otp_value == "123456"
        await manager.stop()

    asyncio.run(scenario())


def test_auth_with_zero_devices_can_be_saved_but_is_not_runtime_ready(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        mina = FakeMiNA([[]])
        accounts = FakeAccountFactory([mina])
        manager = XiaomiAccountManager(
            storage,
            account_factory=accounts,
            mina_factory=lambda account: account.mina,
        )
        attempt_id = await manager.start_auth("owner", "password")
        await wait_for_state(manager, attempt_id, "devices_ready")

        result = await manager.save_bindings([])

        assert result.saved is True
        assert manager.status().state == "devices_ready"
        assert storage.get_xiaomi_account() is not None
        assert manager.has_available_binding(None) is False
        await manager.stop()

    asyncio.run(scenario())


def test_starting_new_attempt_cancels_old_and_cancel_keeps_safe_terminal_status(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        accounts = FakeAccountFactory(
            [FakeMiNA([[]]), FakeMiNA([[]])],
            require_otp=True,
        )
        manager = XiaomiAccountManager(
            storage,
            account_factory=accounts,
            mina_factory=lambda account: account.mina,
        )
        first = await manager.start_auth("first", "first-password")
        await wait_for_state(manager, first, "otp_required")

        second = await manager.start_auth("second", "second-password")
        await wait_for_state(manager, second, "otp_required")

        assert accounts.accounts[0].closed is True
        await manager.cancel_auth(second)
        status = manager.status(second)
        assert status.state == "cancelled"
        assert status.devices == ()
        assert accounts.accounts[1].closed is True
        assert manager._attempt is not None
        assert manager._attempt.password == ""
        assert manager._attempt.token_store.token is None

    asyncio.run(scenario())


def test_attempt_expires_and_releases_in_memory_credentials(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        accounts = FakeAccountFactory([FakeMiNA([[]])], require_otp=True)
        manager = XiaomiAccountManager(
            storage,
            account_factory=accounts,
            mina_factory=lambda account: account.mina,
            attempt_seconds=0.01,
        )
        attempt_id = await manager.start_auth("owner", "temporary-password")
        await wait_for_state(manager, attempt_id, "otp_required")
        await wait_for_state(manager, attempt_id, "expired")

        status = manager.status(attempt_id)
        assert status.state == "expired"
        assert manager._attempt is not None
        assert manager._attempt.password == ""
        assert manager._attempt.token_store.token is None
        assert accounts.accounts[0].closed is True

    asyncio.run(scenario())


def test_refresh_failure_retains_snapshot_and_known_binding_availability(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        mina = FakeMiNA([[device()], RuntimeError("network detail with secret")])
        accounts = FakeAccountFactory([mina])
        manager = XiaomiAccountManager(
            storage,
            account_factory=accounts,
            mina_factory=lambda account: account.mina,
        )
        attempt_id = await manager.start_auth("owner", "password")
        await wait_for_state(manager, attempt_id, "devices_ready")
        speaker_id = manager.status(attempt_id).devices[0].id
        await manager.save_bindings([speaker_id])
        before = manager.status()

        with pytest.raises(XiaomiStateError, match="device_refresh_failed"):
            await manager.refresh_devices()

        after = manager.status()
        assert after.devices == before.devices
        assert after.error_code == "device_refresh_failed"
        assert storage.list_speaker_bindings()[0].available is True
        assert "network detail" not in repr(after)
        await manager.stop()

    asyncio.run(scenario())


def test_successful_refresh_updates_bound_availability_without_persisting_new_candidate(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        mina = FakeMiNA(
            [
                [device("device-a", "客厅", "miot-a"), device("device-b", "卧室", "miot-b")],
                [device("device-a", "客厅新名", "miot-a"), device("device-c", "书房", "miot-c")],
            ]
        )
        accounts = FakeAccountFactory([mina])
        manager = XiaomiAccountManager(
            storage,
            account_factory=accounts,
            mina_factory=lambda account: account.mina,
        )
        attempt_id = await manager.start_auth("owner", "password")
        await wait_for_state(manager, attempt_id, "devices_ready")
        ids = [item.id for item in manager.status(attempt_id).devices]
        await manager.save_bindings(ids)

        refreshed = await manager.refresh_devices()

        assert {item.mina_name for item in refreshed.devices} == {"客厅新名", "书房"}
        persisted = {binding.device_id: binding for binding in storage.list_speaker_bindings()}
        assert set(persisted) == {"device-a", "device-b"}
        assert persisted["device-a"].available is True
        assert persisted["device-b"].available is False
        await manager.stop()

    asyncio.run(scenario())


def test_reauthorization_keeps_old_account_running_until_confirmed_atomic_commit(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        old_mina = FakeMiNA([[device("old-device", "旧音箱", "old-miot")]])
        new_mina = FakeMiNA([[device("new-device", "新音箱", "new-miot")]])
        accounts = FakeAccountFactory([old_mina, new_mina])
        manager = XiaomiAccountManager(
            storage,
            account_factory=accounts,
            mina_factory=lambda account: account.mina,
        )
        first_attempt = await manager.start_auth("old-owner", "old-password")
        await wait_for_state(manager, first_attempt, "devices_ready")
        old_binding_id = manager.status(first_attempt).devices[0].id
        await manager.save_bindings([old_binding_id])
        storage.sync_cameras(["front"])
        storage.set_camera_speaker("front", old_binding_id)

        second_attempt = await manager.start_auth("new-owner", "new-password")
        await wait_for_state(manager, second_attempt, "devices_ready")
        new_binding_id = manager.status(second_attempt).devices[0].id
        preview = await manager.save_bindings([new_binding_id])

        assert preview.saved is False
        assert preview.affected_camera_ids == ("front",)
        unchanged_account = storage.get_xiaomi_account()
        assert unchanged_account is not None
        assert unchanged_account.username == "old-owner"
        assert accounts.accounts[0].closed is False
        assert accounts.accounts[1].closed is False
        assert storage.camera_speaker_id("front") == old_binding_id

        saved = await manager.save_bindings(
            [new_binding_id],
            confirmation_id=preview.confirmation_id,
        )

        assert saved.saved is True
        account = storage.get_xiaomi_account()
        assert account is not None
        assert account.username == "new-owner"
        assert accounts.accounts[0].closed is True
        assert accounts.accounts[1].closed is False
        assert storage.camera_speaker_id("front") is None
        await manager.stop()

    asyncio.run(scenario())


def test_failed_auth_has_stable_error_and_releases_secrets(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        accounts = FakeAccountFactory([FakeMiNA([[]])], login_success=False)
        manager = XiaomiAccountManager(
            storage,
            account_factory=accounts,
            mina_factory=lambda account: account.mina,
        )
        attempt_id = await manager.start_auth("owner", "very-private-password")
        await wait_for_state(manager, attempt_id, "failed")

        status = manager.status(attempt_id)
        assert status.error_code == "auth_failed"
        assert "very-private-password" not in repr(status)
        assert manager._attempt is not None
        assert manager._attempt.password == ""
        assert manager._attempt.token_store.token is None
        assert accounts.accounts[0].closed is True
        assert storage.get_xiaomi_account() is None

    asyncio.run(scenario())


def test_restart_loads_account_without_automatic_device_discovery(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        storage.save_xiaomi_account(
            "owner",
            '{"userId":"private","micoapi":["security","token"]}',
        )
        mina = FakeMiNA([[device()]])
        accounts = FakeAccountFactory([mina])
        manager = XiaomiAccountManager(
            storage,
            account_factory=accounts,
            mina_factory=lambda account: account.mina,
        )

        await manager.start()

        assert manager.status().state == "devices_ready"
        assert mina.device_list_calls == 0
        await manager.stop()

    asyncio.run(scenario())


def test_manual_test_reports_only_boolean_and_never_blocks_save(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        mina = FakeMiNA([[device()]], [False])
        accounts = FakeAccountFactory([mina])
        manager = XiaomiAccountManager(
            storage,
            account_factory=accounts,
            mina_factory=lambda account: account.mina,
        )
        attempt_id = await manager.start_auth("owner", "password")
        await wait_for_state(manager, attempt_id, "devices_ready")
        speaker_id = manager.status(attempt_id).devices[0].id

        result = await manager.test_binding(speaker_id)
        saved = await manager.save_bindings([speaker_id])

        assert result.success is False
        assert saved.saved is True
        assert mina.tts_calls == [("raw-device-id", TEST_PHRASE)]
        assert storage.list_speaker_bindings()[0].test_status == "failure"
        await manager.stop()

    asyncio.run(scenario())


def test_manual_test_auth_failure_marks_attempt_auth_required(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        mina = FakeMiNA([[device()]], [RuntimeError("Error 401 private service token")])
        accounts = FakeAccountFactory([mina])
        manager = XiaomiAccountManager(
            storage,
            account_factory=accounts,
            mina_factory=lambda account: account.mina,
        )
        attempt_id = await manager.start_auth("owner", "password")
        await wait_for_state(manager, attempt_id, "devices_ready")
        speaker_id = manager.status(attempt_id).devices[0].id

        result = await manager.test_binding(speaker_id)

        status = manager.status(attempt_id)
        assert result.success is False
        assert status.state == "auth_required"
        assert status.error_code == "auth_required"
        assert "service token" not in repr(status)

    asyncio.run(scenario())
