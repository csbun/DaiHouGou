from pathlib import Path

import pytest

from daihougou_poc.settings import Settings


def test_settings_reject_non_local_go2rtc_api(tmp_path: Path) -> None:
    env = {
        "POC_ARTIFACT_DIR": str(tmp_path),
        "GO2RTC_API_URL": "http://192.168.1.20:1984",
        "GO2RTC_RTSP_BASE": "rtsp://127.0.0.1:8554",
    }
    with pytest.raises(ValueError, match="loopback"):
        Settings.from_mapping(env)
