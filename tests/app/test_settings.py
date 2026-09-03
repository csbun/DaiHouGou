import json
from pathlib import Path

import pytest

from guduck.settings import Settings, SpeakerConfig

SPEAKERS = [
    {"id": "living_room", "name": "客厅音箱", "did": "123456789"},
    {"id": "bedroom", "name": "卧室音箱", "did": "987654321"},
]
BASE_ENV = {
    "MI_USER": "owner@example.test",
    "MI_PASS": "secret",
    "MI_SPEAKERS_JSON": json.dumps(SPEAKERS, ensure_ascii=False),
}


def test_settings_parse_speakers_and_discovery_defaults() -> None:
    settings = Settings.from_mapping(BASE_ENV)

    assert settings.speakers == (
        SpeakerConfig("living_room", "客厅音箱", "123456789"),
        SpeakerConfig("bedroom", "卧室音箱", "987654321"),
    )
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
    assert "secret" not in repr(settings)
    assert "123456789" not in repr(settings)


def test_settings_require_speaker_catalog() -> None:
    with pytest.raises(ValueError, match="^MI_SPEAKERS_JSON is required$"):
        Settings.from_mapping({"MI_USER": "owner", "MI_PASS": "secret"})


@pytest.mark.parametrize("legacy_key", ["MI_DID", "STREAM_URL"])
def test_settings_reject_legacy_single_camera_keys_when_new_settings_are_present(
    legacy_key: str,
) -> None:
    with pytest.raises(ValueError, match=f"^{legacy_key} is not supported$"):
        Settings.from_mapping({**BASE_ENV, legacy_key: "legacy-value"})


@pytest.mark.parametrize(
    ("speakers", "message"),
    [
        ([], "MI_SPEAKERS_JSON must contain at least one speaker"),
        ([{"id": "Room", "name": "客厅", "did": "1"}], "speaker id is invalid"),
        (
            [
                {"id": "room", "name": "客厅", "did": "1"},
                {"id": "room", "name": "卧室", "did": "2"},
            ],
            "speaker ids must be unique",
        ),
        (
            [
                {"id": "living_room", "name": "客厅", "did": "1"},
                {"id": "bedroom", "name": "卧室", "did": "1"},
            ],
            "speaker dids must be unique",
        ),
        ([{"id": "room", "name": " ", "did": "1"}], "speaker name is invalid"),
        ([{"id": "room", "name": "客厅", "did": ""}], "speaker did is invalid"),
    ],
)
def test_settings_reject_invalid_speaker_catalog(
    speakers: list[dict[str, str]], message: str
) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        Settings.from_mapping(
            {**BASE_ENV, "MI_SPEAKERS_JSON": json.dumps(speakers, ensure_ascii=False)}
        )


def test_settings_accepts_custom_onnx_model_path() -> None:
    settings = Settings.from_mapping({**BASE_ENV, "MODEL": "/models/person.onnx"})

    assert settings.model == Path("/models/person.onnx")


def test_settings_accepts_custom_object_model_path() -> None:
    settings = Settings.from_mapping({**BASE_ENV, "OBJECT_MODEL": "/models/object.onnx"})

    assert settings.object_model == Path("/models/object.onnx")


def test_settings_accepts_objects365_model_path() -> None:
    settings = Settings.from_mapping(
        {
            **BASE_ENV,
            "OBJECTS365_MODEL": "/models/objects365.onnx",
        }
    )

    assert settings.objects365_model == Path("/models/objects365.onnx")


def test_settings_ignores_removed_object_detector_adapter_environment_variable() -> None:
    settings = Settings.from_mapping({**BASE_ENV, "OBJECT_DETECTOR_ADAPTER": "objects365"})

    assert not hasattr(settings, "object_detector_adapter")


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("MI_USER", "", "MI_USER is required"),
        ("MI_PASS", "", "MI_PASS is required"),
        ("PERSON_THRESHOLD", "1.1", "PERSON_THRESHOLD must be between 0 and 1"),
        ("DETECTION_FPS", "0", "DETECTION_FPS must be greater than 0"),
    ],
)
def test_settings_from_mapping_rejects_invalid_values(key: str, value: str, message: str) -> None:
    env = {**BASE_ENV, key: value}

    with pytest.raises(ValueError, match=f"^{message}$"):
        Settings.from_mapping(env)
