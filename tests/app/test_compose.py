from pathlib import Path


def test_compose_runs_one_app_with_persistent_data_and_miservice_home() -> None:
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    assert "dockerfile: docker/app.Dockerfile" in compose
    assert "image: guduck:local" in compose
    assert "container_name: guduck-app" in compose
    assert "./deploy/app/state:/var/lib/guduck/data" in compose
    assert "./deploy/app/models:/opt/guduck/models/custom:ro" in compose
    assert "./deploy/miservice/state:/var/lib/guduck/mi" in compose
    assert "HOME: /var/lib/guduck/mi" in compose
    assert 'test: ["CMD", "python", "-m", "guduck.healthcheck"]' in compose
    assert "path: .env" in compose
    assert "required: false" in compose
    assert "--workers" not in compose
    assert "--reload" not in compose


def test_application_environment_example_contains_no_real_credentials() -> None:
    example = Path(".env.example").read_text(encoding="utf-8")

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
        "OBJECTS365_MODEL=/opt/guduck/models/custom/object_detection_objects365_yolo26n_416.onnx"
    ) in example


def test_upgrade_instructions_use_new_defaults_without_copying_legacy_paths() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    runbook = Path("docs/poc-runbook.md").read_text(encoding="utf-8")

    assert "cp .env.mvp .env" not in readme
    assert "cp .env.mvp .env" not in runbook
    assert "cp .env.example .env" in runbook
    assert "docker compose -f compose.poc.yaml stop app" in runbook
