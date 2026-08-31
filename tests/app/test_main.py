import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

import daihougou.main as main_module
from daihougou.main import create_production_app
from daihougou.settings import Settings
from daihougou.speaker import SpeakResult
from daihougou.web import create_app


class FakeSpeaker:
    def speak(self, text: str) -> SpeakResult:
        return SpeakResult(True, 1, 0)


def settings_for(tmp_path: Path) -> Settings:
    return Settings.from_mapping(
        {
            "MI_USER": "owner",
            "MI_PASS": "secret",
            "MI_SPEAKERS_JSON": json.dumps(
                [
                    {"id": "living_room", "name": "客厅", "did": "1"},
                    {"id": "bedroom", "name": "卧室", "did": "2"},
                ]
            ),
            "DATA_DIR": str(tmp_path),
        }
    )


def test_production_app_builds_one_speaker_per_config(
    tmp_path: Path, monkeypatch
) -> None:
    created_dids: list[str] = []
    monkeypatch.setattr(
        main_module,
        "DirectSpeaker",
        lambda user, password, did: created_dids.append(did) or FakeSpeaker(),
    )

    app = create_production_app(settings_for(tmp_path))

    assert app.title == "大口九"
    assert created_dids == ["1", "2"]


def test_production_lifespan_discovers_once_and_loads_visual_stack_on_enable(
    tmp_path: Path, monkeypatch
) -> None:
    calls = {"discovery": 0, "detector": 0, "source": 0}

    class FakeDiscovery:
        def __init__(self, base_url: str) -> None:
            assert base_url == "http://127.0.0.1:1984"

        def stream_names(self) -> tuple[str, ...]:
            calls["discovery"] += 1
            return ("front",)

    class FakeDetector:
        def detect(self, frame):
            raise AssertionError("no frame should be detected in this test")

    class FakeSource:
        def start(self) -> None:
            pass

        def wait_for_frame(self, after_sequence: int, timeout: float) -> None:
            time.sleep(0.005)

        def stop(self) -> None:
            pass

    def detector_factory(model: Path, threshold: float) -> FakeDetector:
        calls["detector"] += 1
        return FakeDetector()

    def source_factory(url: str, fps: float) -> FakeSource:
        assert url == "rtsp://127.0.0.1:8554/front"
        calls["source"] += 1
        return FakeSource()

    monkeypatch.setattr(main_module, "Go2RtcClient", FakeDiscovery)
    monkeypatch.setattr(main_module, "PersonDetector", detector_factory)
    monkeypatch.setattr(main_module, "FfmpegFrameSource", source_factory)
    monkeypatch.setattr(main_module, "DirectSpeaker", lambda *args: FakeSpeaker())
    monkeypatch.setattr(
        main_module,
        "create_app",
        lambda runtime: create_app(runtime, csrf_token="fixed-token"),
    )

    app = create_production_app(settings_for(tmp_path))
    assert calls == {"discovery": 0, "detector": 0, "source": 0}

    with TestClient(app) as client:
        client.get("/")
        assert calls == {"discovery": 1, "detector": 0, "source": 0}
        response = client.post(
            "/commands/camera-rule",
            data={
                "csrf_token": "fixed-token",
                "camera_id": "front",
                "enabled": "true",
            },
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert calls == {"discovery": 1, "detector": 1, "source": 1}
