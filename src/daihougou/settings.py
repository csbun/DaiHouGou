import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SPEAKER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class SpeakerConfig:
    id: str
    name: str
    did: str = field(repr=False)


def _speaker_catalog(raw: str) -> tuple[SpeakerConfig, ...]:
    if not raw.strip():
        raise ValueError("MI_SPEAKERS_JSON is required")
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("MI_SPEAKERS_JSON must be valid JSON") from error
    if not isinstance(payload, list) or not payload:
        raise ValueError("MI_SPEAKERS_JSON must contain at least one speaker")

    speakers: list[SpeakerConfig] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError(  # noqa: TRY004 - malformed config uses one error family.
                "each speaker must be an object"
            )
        speaker_id = item.get("id")
        name = item.get("name")
        did = item.get("did")
        if not isinstance(speaker_id, str) or not SPEAKER_ID_PATTERN.fullmatch(speaker_id):
            raise ValueError("speaker id is invalid")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 50:
            raise ValueError("speaker name is invalid")
        if not isinstance(did, str) or not did.strip():
            raise ValueError("speaker did is invalid")
        speakers.append(SpeakerConfig(speaker_id, name.strip(), did.strip()))
    if len({speaker.id for speaker in speakers}) != len(speakers):
        raise ValueError("speaker ids must be unique")
    if len({speaker.did for speaker in speakers}) != len(speakers):
        raise ValueError("speaker dids must be unique")
    return tuple(speakers)


def _required(mapping: Mapping[str, str], key: str) -> str:
    value = mapping.get(key, "")
    if not value.strip():
        raise ValueError(f"{key} is required")
    return value


def _float_value(mapping: Mapping[str, str], key: str, default: float) -> float:
    value = mapping.get(key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as error:
        raise ValueError(f"{key} must be a number") from error


def _positive_float(mapping: Mapping[str, str], key: str, default: float) -> float:
    value = _float_value(mapping, key, default)
    if value <= 0:
        raise ValueError(f"{key} must be greater than 0")
    return value


@dataclass(frozen=True)
class Settings:
    mi_user: str = field(repr=False)
    mi_pass: str = field(repr=False)
    speakers: tuple[SpeakerConfig, ...] = ()
    go2rtc_api_url: str = "http://127.0.0.1:1984"
    go2rtc_rtsp_base_url: str = "rtsp://127.0.0.1:8554"
    data_dir: Path = Path("/var/lib/daihougou/data")
    model: Path = Path("/opt/daihougou/models/person_detection_mediapipe_2023mar.onnx")
    detection_fps: float = 1.0
    person_threshold: float = 0.55
    leave_seconds: float = 10.0
    welcome_cooldown_seconds: float = 60.0
    web_host: str = "0.0.0.0"
    web_port: int = 8080

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> "Settings":
        for legacy_key in ("MI_DID", "STREAM_URL"):
            if legacy_key in mapping:
                raise ValueError(f"{legacy_key} is not supported")

        person_threshold = _float_value(mapping, "PERSON_THRESHOLD", 0.55)
        if not 0 < person_threshold < 1:
            raise ValueError("PERSON_THRESHOLD must be between 0 and 1")

        web_port_value = mapping.get("WEB_PORT")
        if web_port_value is None:
            web_port = 8080
        else:
            try:
                web_port = int(web_port_value)
            except ValueError as error:
                raise ValueError("WEB_PORT must be between 1 and 65535") from error
            if not 1 <= web_port <= 65535:
                raise ValueError("WEB_PORT must be between 1 and 65535")

        return cls(
            mi_user=_required(mapping, "MI_USER"),
            mi_pass=_required(mapping, "MI_PASS"),
            speakers=_speaker_catalog(mapping.get("MI_SPEAKERS_JSON", "")),
            go2rtc_api_url=mapping.get("GO2RTC_API_URL", "http://127.0.0.1:1984"),
            go2rtc_rtsp_base_url=mapping.get(
                "GO2RTC_RTSP_BASE_URL", "rtsp://127.0.0.1:8554"
            ),
            data_dir=Path(mapping.get("DATA_DIR", "/var/lib/daihougou/data")),
            model=Path(
                mapping.get(
                    "MODEL",
                    "/opt/daihougou/models/person_detection_mediapipe_2023mar.onnx",
                )
            ),
            detection_fps=_positive_float(mapping, "DETECTION_FPS", 1.0),
            person_threshold=person_threshold,
            leave_seconds=_positive_float(mapping, "LEAVE_SECONDS", 10.0),
            welcome_cooldown_seconds=_positive_float(
                mapping, "WELCOME_COOLDOWN_SECONDS", 60.0
            ),
            web_host=mapping.get("WEB_HOST", "0.0.0.0"),
            web_port=web_port,
        )
