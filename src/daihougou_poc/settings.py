import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def _require_loopback(value: str, name: str) -> str:
    host = urlparse(value).hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"{name} must use a loopback address during the PoC")
    return value.rstrip("/")


def _require_campaign_id(value: str) -> str:
    if value == "replace-with-a-unique-poc-run-id" or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}", value
    ):
        raise ValueError("POC_CAMPAIGN_ID must be a unique 3-64 character identifier")
    return value


@dataclass(frozen=True)
class Settings:
    artifact_dir: Path
    campaign_id: str
    inventory_path: Path
    go2rtc_api_url: str
    go2rtc_rtsp_base: str
    mi_user: str = ""
    mi_pass: str = ""
    mi_did: str = ""
    ha_base_url: str = ""
    ha_access_token: str = ""
    ha_speaker_service: str = ""
    ha_speaker_entity: str = ""
    ha_text_field: str = "message"
    ha_extra_data_json: str = "{}"

    @classmethod
    def from_mapping(cls, env: dict[str, str]) -> "Settings":
        return cls(
            artifact_dir=Path(env["POC_ARTIFACT_DIR"]),
            campaign_id=_require_campaign_id(env["POC_CAMPAIGN_ID"]),
            inventory_path=Path(env["POC_INVENTORY_PATH"]),
            go2rtc_api_url=_require_loopback(env["GO2RTC_API_URL"], "GO2RTC_API_URL"),
            go2rtc_rtsp_base=_require_loopback(env["GO2RTC_RTSP_BASE"], "GO2RTC_RTSP_BASE"),
            mi_user=env.get("MI_USER", ""),
            mi_pass=env.get("MI_PASS", ""),
            mi_did=env.get("MI_DID", ""),
            ha_base_url=env.get("HA_BASE_URL", "").rstrip("/"),
            ha_access_token=env.get("HA_ACCESS_TOKEN", ""),
            ha_speaker_service=env.get("HA_SPEAKER_SERVICE", ""),
            ha_speaker_entity=env.get("HA_SPEAKER_ENTITY", ""),
            ha_text_field=env.get("HA_TEXT_FIELD", "message"),
            ha_extra_data_json=env.get("HA_EXTRA_DATA_JSON", "{}"),
        )
