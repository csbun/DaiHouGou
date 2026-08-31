import re
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from daihougou.runtime import CameraView, RuntimeSnapshot, SpeakerOption
from daihougou.storage import normalize_welcome_phrases
from daihougou.web import create_app


def snapshot_fixture(
    camera_count: int,
    camera_ids: tuple[str, ...] | None = None,
    *,
    rules_enabled: bool = True,
) -> RuntimeSnapshot:
    names = list(camera_ids) if camera_ids is not None else ["front", "back"] + [
        f"camera_{number}" for number in range(3, camera_count + 1)
    ]
    cameras = tuple(
        CameraView(
            stream_id=name,
            speaker_id="living_room",
            speaker="客厅音箱",
            available=True,
            rule_enabled=rules_enabled,
            stream="ready" if rules_enabled else "stopped",
            detector="ready" if rules_enabled else "stopped",
            presence="absent" if rules_enabled else "unknown",
            last_confidence=0.1 if rules_enabled else None,
            last_detection_latency_ms=4 if rules_enabled else None,
            last_error="",
            latest_trigger=None,
        )
        for name in names[:camera_count]
    )
    return RuntimeSnapshot(
        app="running",
        database="ready",
        discovery="ready",
        detector="ready" if cameras else "stopped",
        speaker_auth="ready",
        speakers=(SpeakerOption("living_room", "客厅音箱"),),
        cameras=cameras,
        events=(),
        last_error="",
    )


class FakeRuntime:
    def __init__(
        self,
        camera_count: int = 1,
        camera_ids: tuple[str, ...] | None = None,
        *,
        rules_enabled: bool = True,
    ) -> None:
        self.refresh_calls = 0
        self.rule_updates: list[tuple[str, bool]] = []
        self.speaker_updates: list[tuple[str, str]] = []
        self.phrase_updates: list[list[str]] = []
        self._phrases = ("你好呀，欢迎回来。",)
        self._snapshot = snapshot_fixture(
            camera_count, camera_ids, rules_enabled=rules_enabled
        )

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def refresh_cameras(self) -> None:
        self.refresh_calls += 1

    async def set_rule_enabled(self, camera_id: str, enabled: bool) -> None:
        if camera_id not in {camera.stream_id for camera in self._snapshot.cameras}:
            raise KeyError(camera_id)
        self.rule_updates.append((camera_id, enabled))
        self._snapshot = replace(
            self._snapshot,
            cameras=tuple(
                replace(
                    camera,
                    rule_enabled=enabled,
                    stream="starting" if enabled else "stopped",
                    detector="starting" if enabled else "stopped",
                    presence="unknown",
                )
                if camera.stream_id == camera_id
                else camera
                for camera in self._snapshot.cameras
            ),
        )

    async def set_camera_speaker(self, camera_id: str, speaker_id: str) -> None:
        if camera_id not in {camera.stream_id for camera in self._snapshot.cameras}:
            raise KeyError(camera_id)
        if speaker_id not in {speaker.id for speaker in self._snapshot.speakers}:
            raise ValueError("unknown speaker")
        self.speaker_updates.append((camera_id, speaker_id))

    def welcome_phrases(self) -> tuple[str, ...]:
        return self._phrases

    def set_welcome_phrases(self, lines: list[str]) -> tuple[str, ...]:
        normalized = normalize_welcome_phrases(lines)
        self.phrase_updates.append(lines)
        self._phrases = normalized
        return normalized

    def snapshot(self) -> RuntimeSnapshot:
        return self._snapshot


def test_home_lists_cameras_and_links_to_settings_without_phrase_editor() -> None:
    runtime = FakeRuntime(camera_count=2)
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "大口九" in response.text
    assert "front" in response.text
    assert "back" in response.text
    assert 'href="/settings"' in response.text
    assert ">设置<" in response.text
    assert "欢迎词（每行一条）" not in response.text
    assert "你好呀，欢迎回来。" not in response.text
    assert "客厅音箱" in response.text
    assert "123456789" not in response.text
    assert response.cookies["daihougou_csrf"] == "fixed-token"


def test_settings_page_edits_welcome_phrases_and_links_home() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/settings")

    assert response.status_code == 200
    assert "设置" in response.text
    assert "欢迎词（每行一条）" in response.text
    assert "你好呀，欢迎回来。" in response.text
    assert 'href="/"' in response.text
    assert "返回管理页" in response.text


def test_disabled_rule_explains_why_video_stream_is_not_running() -> None:
    runtime = FakeRuntime(rules_enabled=False)
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "未启动（规则关闭）" in response.text


def test_rule_checkbox_does_not_keep_full_page_inline_submit_handler() -> None:
    runtime = FakeRuntime(rules_enabled=False)
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/")

    checkbox = re.search(r"<input[^>]+data-rule-checkbox[^>]*>", response.text)
    assert checkbox is not None
    assert "onchange" not in checkbox.group(0)


def test_manual_refresh_command_calls_runtime_once() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            "/commands/refresh-cameras",
            data={"csrf_token": "fixed-token"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert runtime.refresh_calls == 1


def test_rule_command_uses_hidden_camera_id_not_url_path() -> None:
    runtime = FakeRuntime(camera_count=1, camera_ids=("room/a b",))
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            "/commands/camera-rule",
            data={
                "csrf_token": "fixed-token",
                "camera_id": "room/a b",
                "enabled": "true",
            },
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert runtime.rule_updates == [("room/a b", True)]


def test_camera_rule_and_speaker_commands_redirect_after_success() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        rule_response = client.post(
            "/commands/camera-rule",
            data={
                "csrf_token": "fixed-token",
                "camera_id": "front",
                "enabled": "false",
            },
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
        speaker_response = client.post(
            "/commands/camera-speaker",
            data={
                "csrf_token": "fixed-token",
                "camera_id": "front",
                "speaker_id": "living_room",
            },
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert rule_response.status_code == 303
    assert speaker_response.status_code == 303
    assert runtime.rule_updates == [("front", False)]
    assert runtime.speaker_updates == [("front", "living_room")]


def test_camera_rule_command_returns_current_state_for_async_switch() -> None:
    runtime = FakeRuntime(rules_enabled=False)
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            "/commands/camera-rule",
            data={
                "csrf_token": "fixed-token",
                "camera_id": "front",
                "enabled": "true",
            },
            headers={
                "Origin": "http://testserver",
                "Accept": "application/json",
            },
            follow_redirects=False,
        )

    assert response.status_code == 200
    assert response.json() == {
        "camera": {
            "stream_id": "front",
            "available": True,
            "rule_enabled": True,
            "stream": "starting",
            "detector": "starting",
            "presence": "unknown",
            "last_confidence": None,
            "last_detection_latency_ms": None,
            "last_error": "",
        },
        "enabled_camera_count": 1,
        "ready_camera_count": 0,
        "resource_warning": False,
    }


def test_invalid_welcome_phrases_render_error_without_writing() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            "/commands/welcome-phrases",
            data={"csrf_token": "fixed-token", "phrases": "\n\n"},
            headers={"Origin": "http://testserver"},
        )

    assert response.status_code == 422
    assert "至少填写一条欢迎词" in response.text
    assert "返回管理页" in response.text
    assert runtime.phrase_updates == []


def test_valid_welcome_phrases_redirect_back_to_settings() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/settings")
        response = client.post(
            "/commands/welcome-phrases",
            data={"csrf_token": "fixed-token", "phrases": "Welcome home!"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    assert runtime.phrase_updates == [["Welcome home!"]]


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/commands/refresh-cameras", {}),
        ("/commands/camera-rule", {"camera_id": "front", "enabled": "true"}),
        (
            "/commands/camera-speaker",
            {"camera_id": "front", "speaker_id": "living_room"},
        ),
        ("/commands/welcome-phrases", {"phrases": "你好"}),
    ],
)
def test_all_commands_reject_missing_csrf(path: str, data: dict[str, str]) -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            path,
            data=data,
            headers={"Origin": "http://testserver"},
        )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/commands/refresh-cameras", {}),
        ("/commands/camera-rule", {"camera_id": "front", "enabled": "true"}),
        (
            "/commands/camera-speaker",
            {"camera_id": "front", "speaker_id": "living_room"},
        ),
        ("/commands/welcome-phrases", {"phrases": "你好"}),
    ],
)
def test_all_commands_reject_cross_origin(path: str, data: dict[str, str]) -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            path,
            data={**data, "csrf_token": "fixed-token"},
            headers={"Origin": "http://attacker.test"},
        )

    assert response.status_code == 403


def test_unknown_camera_and_speaker_return_not_found() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        headers = {"Origin": "http://testserver"}
        unknown_camera = client.post(
            "/commands/camera-rule",
            data={
                "csrf_token": "fixed-token",
                "camera_id": "missing",
                "enabled": "true",
            },
            headers=headers,
        )
        unknown_speaker = client.post(
            "/commands/camera-speaker",
            data={
                "csrf_token": "fixed-token",
                "camera_id": "front",
                "speaker_id": "missing",
            },
            headers=headers,
        )

    assert unknown_camera.status_code == 404
    assert unknown_speaker.status_code == 404


def test_four_enabled_cameras_show_resource_notice() -> None:
    runtime = FakeRuntime(camera_count=4)
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/")

    assert "已开启 4 路摄像头" in response.text
    assert "仍可继续开启" in response.text


def test_healthz_contains_counts_and_camera_states_without_private_data() -> None:
    runtime = FakeRuntime(camera_count=2)
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["saved_camera_count"] == 2
    assert body["discovered_camera_count"] == 2
    assert body["enabled_camera_count"] == 2
    assert body["cameras"][0]["stream_id"] == "front"
    serialized = response.text
    assert "你好呀，欢迎回来。" not in serialized
    assert "secret" not in serialized
    assert "did" not in serialized.lower()
