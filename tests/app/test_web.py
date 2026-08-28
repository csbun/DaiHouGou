from pathlib import Path

from fastapi.testclient import TestClient

from daihougou.runtime import RuntimeSnapshot
from daihougou.storage import EventRecord, Storage
from daihougou.web import create_app


class FakeRuntime:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            app="running",
            database="ready",
            camera="degraded",
            detector="starting",
            speaker_auth="unknown",
            presence="unknown",
            last_sequence=0,
            last_confidence=None,
            last_detection_latency_ms=None,
            last_error="camera_no_frames",
        )


def test_home_shows_status_and_disabled_rule(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    runtime = FakeRuntime()

    with TestClient(create_app(storage, runtime, csrf_token="fixed-token")) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "大口九" in response.text
    assert "大吼狗" not in response.text
    assert "人员进入欢迎" in response.text
    assert "摄像头" in response.text
    assert "已关闭" in response.text
    assert response.cookies["daihougou_csrf"] == "fixed-token"
    assert runtime.started is True
    assert runtime.stopped is True


def test_healthz_reports_degraded_without_failing_web_process(tmp_path: Path) -> None:
    with TestClient(
        create_app(Storage(tmp_path / "app.db"), FakeRuntime(), csrf_token="fixed-token")
    ) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["camera"] == "degraded"


def test_same_origin_form_with_csrf_can_enable_rule(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    app = create_app(storage, FakeRuntime(), csrf_token="fixed-token")

    with TestClient(app) as client:
        client.get("/")
        response = client.post(
            "/rules/welcome_on_person_entry/enable",
            data={"csrf_token": "fixed-token"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert storage.rule_enabled("welcome_on_person_entry") is True
    assert storage.recent_events()[0].kind == "rule_enabled_changed"


def test_home_shows_latest_rule_trigger_result(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.record_event(
        EventRecord(
            "speaker_completed",
            True,
            rule_id="welcome_on_person_entry",
            latency_ms=321,
        )
    )

    with TestClient(create_app(storage, FakeRuntime(), csrf_token="fixed-token")) as client:
        response = client.get("/")

    assert "最近触发：成功" in response.text
    assert "321 ms" in response.text


def test_rule_update_rejects_missing_csrf_and_cross_origin(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    app = create_app(storage, FakeRuntime(), csrf_token="fixed-token")

    with TestClient(app) as client:
        client.get("/")
        no_token = client.post(
            "/rules/welcome_on_person_entry/enable",
            headers={"Origin": "http://testserver"},
        )
        wrong_origin = client.post(
            "/rules/welcome_on_person_entry/enable",
            data={"csrf_token": "fixed-token"},
            headers={"Origin": "http://attacker.test"},
        )

    assert no_token.status_code == 403
    assert wrong_origin.status_code == 403
    assert storage.rule_enabled("welcome_on_person_entry") is False


def test_unknown_rule_and_action_are_not_accepted(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    app = create_app(storage, FakeRuntime(), csrf_token="fixed-token")

    with TestClient(app) as client:
        client.get("/")
        headers = {"Origin": "http://testserver"}
        data = {"csrf_token": "fixed-token"}
        unknown_rule = client.post("/rules/other/enable", data=data, headers=headers)
        unknown_action = client.post(
            "/rules/welcome_on_person_entry/toggle", data=data, headers=headers
        )

    assert unknown_rule.status_code == 404
    assert unknown_action.status_code == 404
