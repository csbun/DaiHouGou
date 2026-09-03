from pathlib import Path


def test_compose_runs_one_app_with_persistent_data_and_miservice_home() -> None:
    compose = Path("compose.poc.yaml").read_text(encoding="utf-8")

    assert "dockerfile: docker/mvp.Dockerfile" in compose
    assert "container_name: daihougou-app" in compose
    assert "./deploy/app/state:/var/lib/daihougou/data" in compose
    assert "./deploy/app/models:/opt/daihougou/models/custom:ro" in compose
    assert "./deploy/miservice/state:/var/lib/daihougou/mi" in compose
    assert "HOME: /var/lib/daihougou/mi" in compose
    assert 'test: ["CMD", "python", "-m", "daihougou.healthcheck"]' in compose
    assert "path: .env.mvp" in compose
    assert "required: false" in compose
    assert "--workers" not in compose
    assert "--reload" not in compose


def test_mvp_environment_example_contains_no_real_credentials() -> None:
    example = Path(".env.mvp.example").read_text(encoding="utf-8")

    assert "MI_USER=" in example
    assert "MI_PASS=" in example
    assert "MI_SPEAKERS_JSON=" in example
    assert "GO2RTC_API_URL=http://127.0.0.1:1984" in example
    assert "GO2RTC_RTSP_BASE_URL=rtsp://127.0.0.1:8554" in example
    assert "MI_DID=" not in example
    assert "STREAM_URL=" not in example
    assert "owner@example" not in example
    assert "123456789" not in example
    assert "WEB_HOST=SERVER_LAN_IP" in example
    assert "OBJECT_DETECTOR_ADAPTER" not in example
    assert (
        "OBJECTS365_MODEL=/opt/daihougou/models/custom/object_detection_objects365_yolo26n_416.onnx"
    ) in example
