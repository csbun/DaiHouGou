from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


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
    mi_did: str = field(repr=False)
    stream_url: str = "rtsp://127.0.0.1:8554/xiaobai"
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
            mi_did=_required(mapping, "MI_DID"),
            stream_url=mapping.get("STREAM_URL", "rtsp://127.0.0.1:8554/xiaobai"),
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
