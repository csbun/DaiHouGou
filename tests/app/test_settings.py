from pathlib import Path

import pytest

from guduck.settings import Settings


def test_settings_use_database_backed_xiaomi_configuration() -> None:
    settings = Settings.from_mapping({})

    assert settings.go2rtc_api_url == "http://127.0.0.1:1984"
    assert settings.go2rtc_rtsp_base_url == "rtsp://127.0.0.1:8554"
    assert settings.data_dir == Path("/var/lib/guduck/data")
    assert settings.model == Path("/opt/guduck/models/person_detection_mediapipe_2023mar.onnx")
    assert settings.object_model == Path(
        "/opt/guduck/models/object_detection_nanodet_2022nov.onnx"
    )
    assert not hasattr(settings, "object_detector_adapter")
    assert settings.objects365_model == Path(
        "/opt/guduck/models/custom/object_detection_objects365_yolo26n_416.onnx"
    )
    assert settings.detection_fps == 1.0
    assert settings.person_threshold == 0.55
    assert settings.web_port == 8080
    assert not hasattr(settings, "mi_user")
    assert not hasattr(settings, "mi_pass")
    assert not hasattr(settings, "speakers")


@pytest.mark.parametrize(
    "retired_key",
    ["MI_USER", "MI_PASS", "MI_SPEAKERS_JSON", "MI_DID", "STREAM_URL"],
)
def test_settings_reject_retired_configuration_keys_even_when_blank(
    retired_key: str,
) -> None:
    with pytest.raises(ValueError, match=f"^{retired_key} is not supported$"):
        Settings.from_mapping({retired_key: ""})


def test_settings_accepts_custom_onnx_model_path() -> None:
    settings = Settings.from_mapping({"MODEL": "/models/person.onnx"})

    assert settings.model == Path("/models/person.onnx")


def test_settings_accepts_custom_object_model_path() -> None:
    settings = Settings.from_mapping({"OBJECT_MODEL": "/models/object.onnx"})

    assert settings.object_model == Path("/models/object.onnx")


def test_settings_accepts_objects365_model_path() -> None:
    settings = Settings.from_mapping({"OBJECTS365_MODEL": "/models/objects365.onnx"})

    assert settings.objects365_model == Path("/models/objects365.onnx")


def test_settings_ignores_removed_object_detector_adapter_environment_variable() -> None:
    settings = Settings.from_mapping({"OBJECT_DETECTOR_ADAPTER": "objects365"})

    assert not hasattr(settings, "object_detector_adapter")


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("PERSON_THRESHOLD", "1.1", "PERSON_THRESHOLD must be between 0 and 1"),
        ("DETECTION_FPS", "0", "DETECTION_FPS must be greater than 0"),
        ("LEAVE_SECONDS", "not-a-number", "LEAVE_SECONDS must be a number"),
        ("WEB_PORT", "0", "WEB_PORT must be between 1 and 65535"),
    ],
)
def test_settings_from_mapping_rejects_invalid_values(
    key: str, value: str, message: str
) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        Settings.from_mapping({key: value})
