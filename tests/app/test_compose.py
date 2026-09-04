from pathlib import Path

RETIRED_XIAOMI_CONFIG = (
    "MI_USER",
    "MI_PASS",
    "MI_SPEAKERS_JSON",
    "MI_DID",
    ".mi.token",
    "/var/lib/guduck/mi",
    "deploy/miservice/state",
)


def test_compose_runs_one_app_with_database_backed_xiaomi_state() -> None:
    compose = Path("compose.yaml").read_text(encoding="utf-8")

    assert "dockerfile: docker/app.Dockerfile" in compose
    assert "image: guduck:local" in compose
    assert "container_name: guduck-app" in compose
    assert "./deploy/app/state:/var/lib/guduck/data" in compose
    assert "./deploy/app/models:/opt/guduck/models/custom:ro" in compose
    assert 'test: ["CMD", "python", "-m", "guduck.healthcheck"]' in compose
    assert "path: .env" in compose
    assert "required: false" in compose
    assert "--workers" not in compose
    assert "--reload" not in compose
    assert all(value not in compose for value in RETIRED_XIAOMI_CONFIG)


def test_application_environment_example_contains_no_xiaomi_credentials() -> None:
    example = Path(".env.example").read_text(encoding="utf-8")

    assert "GO2RTC_API_URL=http://127.0.0.1:1984" in example
    assert "GO2RTC_RTSP_BASE_URL=rtsp://127.0.0.1:8554" in example
    assert "WEB_HOST=SERVER_LAN_IP" in example
    assert "OBJECT_DETECTOR_ADAPTER" not in example
    assert (
        "OBJECTS365_MODEL=/opt/guduck/models/custom/object_detection_objects365_yolo26n_416.onnx"
    ) in example
    assert all(value not in example for value in RETIRED_XIAOMI_CONFIG)


def test_poc_environment_example_contains_no_direct_xiaomi_credentials() -> None:
    example = Path(".env.poc.example").read_text(encoding="utf-8")

    assert "HA_BASE_URL=http://127.0.0.1:8123" in example
    assert all(value not in example for value in RETIRED_XIAOMI_CONFIG)


def test_upgrade_instructions_use_database_backed_authorization() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")
    runbook = Path("docs/poc-runbook.md").read_text(encoding="utf-8")

    assert "cp .env.mvp .env" not in readme
    assert "cp .env.mvp .env" not in runbook
    assert "cp .env.example .env" in readme
    assert "cp .env.example .env" in runbook
    assert "docker compose stop app" in runbook
    assert all(value not in readme for value in RETIRED_XIAOMI_CONFIG)
    assert all(value not in runbook for value in RETIRED_XIAOMI_CONFIG)
