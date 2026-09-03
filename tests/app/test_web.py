import re
from dataclasses import replace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient

from guduck.camera_snapshot import SnapshotUnavailable
from guduck.detection_region import FULL_FRAME_REGION, DetectionRegion
from guduck.runtime import (
    CameraView,
    ObjectDetectorOption,
    RuntimeSnapshot,
    SpeakerOption,
)
from guduck.settings import ObjectDetectorAdapter
from guduck.storage import StoredEvent, normalize_welcome_phrases
from guduck.web import create_app


def snapshot_fixture(
    camera_count: int,
    camera_ids: tuple[str, ...] | None = None,
    *,
    rules_enabled: bool = True,
    detection_region: DetectionRegion = FULL_FRAME_REGION,
) -> RuntimeSnapshot:
    names = (
        list(camera_ids)
        if camera_ids is not None
        else ["front", "back"] + [f"camera_{number}" for number in range(3, camera_count + 1)]
    )
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
            detection_region=detection_region,
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
        detection_region: DetectionRegion = FULL_FRAME_REGION,
        snapshot_bytes: bytes = b"\xff\xd8snapshot",
        snapshot_error: Exception | None = None,
        object_detector_adapter: ObjectDetectorAdapter = ObjectDetectorAdapter.NANODET,
        unavailable_object_adapters: frozenset[ObjectDetectorAdapter] = frozenset(),
        object_detector_error: RuntimeError | None = None,
    ) -> None:
        self.refresh_calls = 0
        self.rule_updates: list[tuple[str, bool]] = []
        self.speaker_updates: list[tuple[str, str]] = []
        self.phrase_updates: list[list[str]] = []
        self.snapshot_calls: list[str] = []
        self.region_updates: list[tuple[str, DetectionRegion]] = []
        self.object_detector_updates: list[ObjectDetectorAdapter] = []
        self._object_detector_adapter = object_detector_adapter
        self._unavailable_object_adapters = unavailable_object_adapters
        self._object_detector_error = object_detector_error
        self._snapshot_bytes = snapshot_bytes
        self._snapshot_error = snapshot_error
        self._phrases = ("你好呀，欢迎回来。",)
        self._snapshot = snapshot_fixture(
            camera_count,
            camera_ids,
            rules_enabled=rules_enabled,
            detection_region=detection_region,
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

    async def capture_camera_snapshot(self, camera_id: str) -> bytes:
        if camera_id not in {camera.stream_id for camera in self._snapshot.cameras}:
            raise KeyError(camera_id)
        self.snapshot_calls.append(camera_id)
        if self._snapshot_error is not None:
            raise self._snapshot_error
        return self._snapshot_bytes

    async def set_camera_detection_region(self, camera_id: str, region: DetectionRegion) -> None:
        if camera_id not in {camera.stream_id for camera in self._snapshot.cameras}:
            raise KeyError(camera_id)
        self.region_updates.append((camera_id, region))
        self._snapshot = replace(
            self._snapshot,
            cameras=tuple(
                replace(camera, detection_region=region)
                if camera.stream_id == camera_id
                else camera
                for camera in self._snapshot.cameras
            ),
        )

    def welcome_phrases(self) -> tuple[str, ...]:
        return self._phrases

    def set_welcome_phrases(self, lines: list[str]) -> tuple[str, ...]:
        normalized = normalize_welcome_phrases(lines)
        self.phrase_updates.append(lines)
        self._phrases = normalized
        return normalized

    def object_detector_options(self) -> tuple[ObjectDetectorOption, ...]:
        return tuple(
            ObjectDetectorOption(
                adapter=adapter,
                available=adapter not in self._unavailable_object_adapters,
                selected=adapter is self._object_detector_adapter,
            )
            for adapter in ObjectDetectorAdapter
        )

    async def set_object_detector_adapter(self, adapter: ObjectDetectorAdapter) -> None:
        if self._object_detector_error is not None:
            raise self._object_detector_error
        self.object_detector_updates.append(adapter)
        self._object_detector_adapter = adapter

    def snapshot(self) -> RuntimeSnapshot:
        return self._snapshot


def test_home_lists_cameras_and_links_to_settings_without_phrase_editor() -> None:
    runtime = FakeRuntime(camera_count=2)
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "GuDuck" in response.text
    assert "front" in response.text
    assert "back" in response.text
    assert 'href="/settings"' in response.text
    assert ">设置<" in response.text
    assert "欢迎词（每行一条）" not in response.text
    assert "你好呀，欢迎回来。" not in response.text
    assert "客厅音箱" in response.text
    assert "123456789" not in response.text
    assert response.cookies["guduck_csrf"] == "fixed-token"


def test_home_links_each_camera_to_independent_detection_region_page() -> None:
    runtime = FakeRuntime(
        camera_count=1,
        camera_ids=("room/a b",),
        detection_region=DetectionRegion(0.1, 0.2, 0.6, 0.5),
    )
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/")

    expected = f"/camera-region?{urlencode({'camera_id': 'room/a b'})}"
    assert expected.replace("&", "&amp;") in response.text
    assert "检测区域" in response.text
    assert "已划定" in response.text
    assert "0.600000" not in response.text


def test_home_omits_region_status_for_full_frame() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/")

    assert "检测区域" in response.text
    assert "已划定" not in response.text


def test_camera_region_page_renders_editor_for_encoded_camera_id() -> None:
    runtime = FakeRuntime(camera_count=1, camera_ids=("room/a b",))
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get(f"/camera-region?{urlencode({'camera_id': 'room/a b'})}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "room/a b" in response.text
    assert "data-region-preview" in response.text
    assert response.text.count("data-region-input=") == 4
    assert "刷新画面" in response.text
    assert "恢复全画面" in response.text
    assert "取消" in response.text
    assert "保存" in response.text


def test_camera_region_page_exposes_accessible_editor_hooks_without_stream_url() -> None:
    region = DetectionRegion(0.123456, 0.2, 0.6, 0.5)
    runtime = FakeRuntime(detection_region=region)
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/camera-region?camera_id=front")

    assert response.text.count("data-region-handle=") == 8
    for direction in ("n", "ne", "e", "se", "s", "sw", "w", "nw"):
        assert f'data-region-handle="{direction}"' in response.text
    assert 'aria-label="检测区域画布"' in response.text
    assert response.text.count('inputmode="decimal"') == 4
    assert response.text.count('aria-describedby="region-error"') == 4
    assert 'data-region-x="0.123456"' in response.text
    assert 'data-region-width="0.6"' in response.text
    assert "beforeunload" not in response.text
    assert "rtsp://" not in response.text


def test_camera_region_page_rejects_unknown_camera() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/camera-region?camera_id=missing")

    assert response.status_code == 404


def test_home_shows_status_tab_without_recent_records() -> None:
    runtime = FakeRuntime()
    runtime._snapshot = replace(
        runtime._snapshot,
        events=(
            StoredEvent(
                id=1,
                occurred_at="2026-09-01T12:34:56+00:00",
                kind="speaker_completed",
                rule_id="welcome_on_person_entry",
                camera_id="front",
                speaker_id="living_room",
                success=True,
                latency_ms=12,
                details_json='{"text": "欢迎回家", "code": 0}',
            ),
        ),
    )
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert 'aria-current="page"' in response.text
    assert 'href="/logs"' in response.text
    assert 'href="/settings"' in response.text
    assert 'id="events-title"' not in response.text
    assert "欢迎回家" not in response.text
    assert "家庭儿童陪护" not in response.text


def test_logs_page_contains_recent_records_and_marks_logs_tab_active() -> None:
    runtime = FakeRuntime()
    runtime._snapshot = replace(
        runtime._snapshot,
        events=(
            StoredEvent(
                id=1,
                occurred_at="2026-09-01T12:34:56+00:00",
                kind="speaker_completed",
                rule_id="welcome_on_person_entry",
                camera_id="front",
                speaker_id="living_room",
                success=True,
                latency_ms=12,
                details_json='{"text": "欢迎回家", "code": 0}',
            ),
        ),
    )
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/logs")

    assert response.status_code == 200
    assert 'id="events-title"' in response.text
    assert "欢迎回家" in response.text
    assert "家庭儿童陪护" not in response.text
    assert response.text.count('aria-current="page"') == 1
    assert ">日志<" in response.text


def test_settings_page_uses_settings_tab_without_back_link() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/settings")

    assert response.status_code == 200
    assert 'href="/"' in response.text
    assert 'href="/logs"' in response.text
    assert ">返回管理页<" not in response.text
    assert "家庭儿童陪护" not in response.text
    assert response.text.count('aria-current="page"') == 1
    assert ">设置<" in response.text


def test_settings_page_edits_welcome_phrases_and_links_home() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/settings")

    assert response.status_code == 200
    assert "设置" in response.text
    assert "欢迎词（每行一条）" in response.text
    assert "你好呀，欢迎回来。" in response.text
    assert 'href="/"' in response.text
    assert 'href="/logs"' in response.text


def test_settings_page_shows_the_active_objects365_adapter_and_its_vocabulary() -> None:
    runtime = FakeRuntime(
        object_detector_adapter=ObjectDetectorAdapter.OBJECTS365,
        unavailable_object_adapters=frozenset({ObjectDetectorAdapter.NANODET}),
    )
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/settings")

    assert response.status_code == 200
    assert "Objects365 YOLO26n" in response.text
    assert "364 个可播报类别" in response.text
    assert "rabbit" in response.text
    assert "兔子" in response.text
    assert re.search(r'name="adapter" value="objects365"[^>]*checked', response.text)
    assert re.search(r'name="adapter" value="nanodet"[^>]*disabled', response.text)


def test_object_detector_command_switches_the_global_adapter() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/settings")
        response = client.post(
            "/commands/object-detector-adapter",
            data={"csrf_token": "fixed-token", "adapter": "objects365"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/settings"
    assert runtime.object_detector_updates == [ObjectDetectorAdapter.OBJECTS365]


def test_failed_object_detector_switch_keeps_the_previous_selection() -> None:
    runtime = FakeRuntime(object_detector_error=RuntimeError("object_detector_switch_failed"))
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/settings")
        response = client.post(
            "/commands/object-detector-adapter",
            data={"csrf_token": "fixed-token", "adapter": "objects365"},
            headers={"Origin": "http://testserver"},
        )

    assert response.status_code == 422
    assert "模型加载失败，已继续使用原检测器。" in response.text
    assert re.search(r'name="adapter" value="nanodet"[^>]*checked', response.text)
    assert runtime.object_detector_updates == []


def test_unavailable_object_detector_switch_reports_deployment_error() -> None:
    runtime = FakeRuntime(object_detector_error=RuntimeError("object_detector_unavailable"))
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/settings")
        response = client.post(
            "/commands/object-detector-adapter",
            data={"csrf_token": "fixed-token", "adapter": "objects365"},
            headers={"Origin": "http://testserver"},
        )

    assert response.status_code == 422
    assert "模型文件不可用，请先完成部署。" in response.text
    assert runtime.object_detector_updates == []


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
    assert 'href="/logs"' in response.text
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


def test_camera_snapshot_returns_uncached_jpeg_bytes() -> None:
    runtime = FakeRuntime(snapshot_bytes=b"\xff\xd8exact-jpeg")
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            "/commands/camera-snapshot",
            data={"csrf_token": "fixed-token", "camera_id": "front"},
            headers={"Origin": "http://testserver"},
        )

    assert response.status_code == 200
    assert response.content == b"\xff\xd8exact-jpeg"
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["cache-control"] == "no-store"
    assert runtime.snapshot_calls == ["front"]


def test_camera_snapshot_failure_is_stable_and_uncached() -> None:
    error = SnapshotUnavailable("camera_snapshot_unavailable")
    error.__cause__ = RuntimeError("rtsp://owner:password@camera/private")
    runtime = FakeRuntime(snapshot_error=error)
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            "/commands/camera-snapshot",
            data={"csrf_token": "fixed-token", "camera_id": "front"},
            headers={"Origin": "http://testserver"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "camera_snapshot_unavailable"}
    assert response.headers["cache-control"] == "no-store"
    assert "password" not in response.text
    assert "rtsp://" not in response.text


def test_camera_snapshot_rejects_unknown_camera_without_runtime_call() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            "/commands/camera-snapshot",
            data={"csrf_token": "fixed-token", "camera_id": "missing"},
            headers={"Origin": "http://testserver"},
        )

    assert response.status_code == 404
    assert runtime.snapshot_calls == []


def test_camera_region_json_save_uses_six_decimal_value_and_reports_state() -> None:
    runtime = FakeRuntime(rules_enabled=False)
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        client.post(
            "/commands/camera-snapshot",
            data={"csrf_token": "fixed-token", "camera_id": "front"},
            headers={"Origin": "http://testserver"},
        )
        response = client.post(
            "/commands/camera-region",
            data={
                "csrf_token": "fixed-token",
                "camera_id": "front",
                "x": "0.12345649",
                "y": "0.2",
                "width": "0.6",
                "height": "0.5",
            },
            headers={"Origin": "http://testserver", "Accept": "application/json"},
        )

    expected = DetectionRegion(0.123456, 0.2, 0.6, 0.5)
    assert response.status_code == 200
    assert runtime.region_updates == [("front", expected)]
    assert response.json() == {
        "saved": True,
        "region": {"x": 0.123456, "y": 0.2, "width": 0.6, "height": 0.5},
        "camera": {"stream_id": "front", "stream": "stopped", "last_error": ""},
    }


def test_camera_region_form_save_redirects_to_encoded_editor() -> None:
    runtime = FakeRuntime(camera_count=1, camera_ids=("room/a b",))
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            "/commands/camera-region",
            data={
                "csrf_token": "fixed-token",
                "camera_id": "room/a b",
                "x": "0",
                "y": "0",
                "width": "1",
                "height": "1",
            },
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == (
        f"/camera-region?{urlencode({'camera_id': 'room/a b', 'saved': '1'})}"
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("x", "text"),
        ("x", "nan"),
        ("x", "inf"),
        ("width", "0.019"),
        ("width", "1.1"),
    ],
)
def test_camera_region_rejects_invalid_values_without_writing(field: str, value: str) -> None:
    runtime = FakeRuntime()
    data = {
        "csrf_token": "fixed-token",
        "camera_id": "front",
        "x": "0",
        "y": "0",
        "width": "1",
        "height": "1",
    }
    data[field] = value
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            "/commands/camera-region",
            data=data,
            headers={"Origin": "http://testserver", "Accept": "application/json"},
        )

    assert response.status_code == 422
    assert runtime.region_updates == []


def test_invalid_camera_region_form_rerenders_editor() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            "/commands/camera-region",
            data={
                "csrf_token": "fixed-token",
                "camera_id": "front",
                "x": "invalid",
                "y": "0",
                "width": "1",
                "height": "1",
            },
            headers={"Origin": "http://testserver"},
        )

    assert response.status_code == 422
    assert "检测区域格式无效" in response.text
    assert "front" in response.text
    assert runtime.region_updates == []


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/commands/camera-snapshot", {"camera_id": "front"}),
        (
            "/commands/camera-region",
            {
                "camera_id": "front",
                "x": "0",
                "y": "0",
                "width": "1",
                "height": "1",
            },
        ),
    ],
)
def test_region_commands_reject_wrong_csrf_without_runtime_calls(
    path: str, data: dict[str, str]
) -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            path,
            data={**data, "csrf_token": "wrong-token"},
            headers={"Origin": "http://testserver"},
        )

    assert response.status_code == 403
    assert runtime.snapshot_calls == []
    assert runtime.region_updates == []


def test_camera_region_requires_snapshot_for_non_full_region_but_allows_reset() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        blocked = client.post(
            "/commands/camera-region",
            data={
                "csrf_token": "fixed-token",
                "camera_id": "front",
                "x": "0.1",
                "y": "0.1",
                "width": "0.8",
                "height": "0.8",
            },
            headers={"Origin": "http://testserver", "Accept": "application/json"},
        )
        reset = client.post(
            "/commands/camera-region",
            data={
                "csrf_token": "fixed-token",
                "camera_id": "front",
                "x": "0",
                "y": "0",
                "width": "1",
                "height": "1",
            },
            headers={"Origin": "http://testserver", "Accept": "application/json"},
        )

    assert blocked.status_code == 409
    assert reset.status_code == 200
    assert runtime.region_updates == [("front", FULL_FRAME_REGION)]


def test_camera_region_last_valid_save_wins() -> None:
    runtime = FakeRuntime()
    first = DetectionRegion(0.1, 0.1, 0.8, 0.8)
    second = DetectionRegion(0.2, 0.2, 0.6, 0.6)
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        client.post(
            "/commands/camera-snapshot",
            data={"csrf_token": "fixed-token", "camera_id": "front"},
            headers={"Origin": "http://testserver"},
        )
        for region in (first, second):
            response = client.post(
                "/commands/camera-region",
                data={
                    "csrf_token": "fixed-token",
                    "camera_id": "front",
                    "x": str(region.x),
                    "y": str(region.y),
                    "width": str(region.width),
                    "height": str(region.height),
                },
                headers={"Origin": "http://testserver", "Accept": "application/json"},
            )
            assert response.status_code == 200

    assert runtime.region_updates == [("front", first), ("front", second)]


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
        ("/commands/object-detector-adapter", {"adapter": "objects365"}),
        ("/commands/camera-snapshot", {"camera_id": "front"}),
        (
            "/commands/camera-region",
            {"camera_id": "front", "x": "0", "y": "0", "width": "1", "height": "1"},
        ),
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
        ("/commands/object-detector-adapter", {"adapter": "objects365"}),
        ("/commands/camera-snapshot", {"camera_id": "front"}),
        (
            "/commands/camera-region",
            {"camera_id": "front", "x": "0", "y": "0", "width": "1", "height": "1"},
        ),
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
