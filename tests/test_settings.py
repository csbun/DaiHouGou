from pathlib import Path

import pytest

from guduck_poc.settings import Settings


def _base_env(tmp_path: Path) -> dict[str, str]:
    return {
        "POC_ARTIFACT_DIR": str(tmp_path),
        "POC_CAMPAIGN_ID": "campaign-1",
        "POC_INVENTORY_PATH": str(tmp_path / "inventory.json"),
        "GO2RTC_API_URL": "http://127.0.0.1:1984",
        "GO2RTC_RTSP_BASE": "rtsp://127.0.0.1:8554",
    }


@pytest.mark.parametrize(
    "retired_key", ["MI_USER", "MI_PASS", "MI_SPEAKERS_JSON", "MI_DID"]
)
def test_settings_reject_retired_direct_xiaomi_configuration(
    tmp_path: Path, retired_key: str
) -> None:
    with pytest.raises(ValueError, match=f"^{retired_key} is not supported$"):
        Settings.from_mapping({**_base_env(tmp_path), retired_key: ""})


def test_settings_reject_non_local_go2rtc_api(tmp_path: Path) -> None:
    env = {
        "POC_ARTIFACT_DIR": str(tmp_path),
        "POC_CAMPAIGN_ID": "campaign-1",
        "POC_INVENTORY_PATH": str(tmp_path / "inventory.json"),
        "GO2RTC_API_URL": "http://192.168.1.20:1984",
        "GO2RTC_RTSP_BASE": "rtsp://127.0.0.1:8554",
    }
    with pytest.raises(ValueError, match="loopback"):
        Settings.from_mapping(env)


def test_settings_require_campaign_and_inventory_path(tmp_path: Path) -> None:
    env = {
        "POC_ARTIFACT_DIR": str(tmp_path),
        "GO2RTC_API_URL": "http://127.0.0.1:1984",
        "GO2RTC_RTSP_BASE": "rtsp://127.0.0.1:8554",
    }

    with pytest.raises(KeyError, match="POC_CAMPAIGN_ID"):
        Settings.from_mapping(env)


def test_settings_reject_campaign_placeholder(tmp_path: Path) -> None:
    env = {
        "POC_ARTIFACT_DIR": str(tmp_path),
        "POC_CAMPAIGN_ID": "replace-with-a-unique-poc-run-id",
        "POC_INVENTORY_PATH": str(tmp_path / "inventory.json"),
        "GO2RTC_API_URL": "http://127.0.0.1:1984",
        "GO2RTC_RTSP_BASE": "rtsp://127.0.0.1:8554",
    }

    with pytest.raises(ValueError, match="unique"):
        Settings.from_mapping(env)
