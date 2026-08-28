from pathlib import Path

import pytest

from daihougou.settings import Settings

BASE_ENV = {"MI_USER": "owner@example.test", "MI_PASS": "secret", "MI_DID": "123456789"}


def test_settings_from_mapping_defaults() -> None:
    settings = Settings.from_mapping(BASE_ENV)

    assert settings.stream_url == "rtsp://127.0.0.1:8554/xiaobai"
    assert settings.data_dir == Path("/var/lib/daihougou/data")
    assert settings.model == Path(
        "/opt/daihougou/models/person_detection_mediapipe_2023mar.onnx"
    )
    assert settings.detection_fps == 1.0
    assert settings.person_threshold == 0.55
    assert settings.web_port == 8080
    assert "secret" not in repr(settings)


def test_settings_accepts_custom_onnx_model_path() -> None:
    settings = Settings.from_mapping({**BASE_ENV, "MODEL": "/models/person.onnx"})

    assert settings.model == Path("/models/person.onnx")


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("MI_USER", "", "MI_USER is required"),
        ("MI_PASS", "", "MI_PASS is required"),
        ("MI_DID", "", "MI_DID is required"),
        ("PERSON_THRESHOLD", "1.1", "PERSON_THRESHOLD must be between 0 and 1"),
        ("DETECTION_FPS", "0", "DETECTION_FPS must be greater than 0"),
    ],
)
def test_settings_from_mapping_rejects_invalid_values(
    key: str, value: str, message: str
) -> None:
    env = {**BASE_ENV, key: value}

    with pytest.raises(ValueError, match=f"^{message}$"):
        Settings.from_mapping(env)
