from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import quote
from urllib.request import urlopen


class DiscoveryError(RuntimeError):
    pass


class Go2RtcClient:
    def __init__(
        self,
        base_url: str,
        get_json: Callable[[str], object] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._get_json = get_json or self._request_json

    def _request_json(self, path: str) -> object:
        try:
            with urlopen(self._base_url + path, timeout=3) as response:
                return json.load(response)
        except Exception as error:
            raise DiscoveryError("go2rtc_unavailable") from error

    def stream_names(self) -> tuple[str, ...]:
        payload = self._get_json("/api/streams")
        if not isinstance(payload, dict) or any(
            not isinstance(name, str) or not name for name in payload
        ):
            raise DiscoveryError("invalid_stream_response")
        return tuple(sorted(payload))


def rtsp_stream_url(base_url: str, stream_id: str) -> str:
    return f"{base_url.rstrip('/')}/{quote(stream_id, safe='')}"
