import json
from collections.abc import Callable
from typing import Any
from urllib.request import urlopen


class Go2RtcClient:
    def __init__(
        self,
        base_url: str,
        get_json: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._get_json = get_json or self._request_json

    def _request_json(self, path: str) -> dict[str, Any]:
        with urlopen(self.base_url + path, timeout=10) as response:
            return json.load(response)

    def health(self) -> dict[str, Any]:
        return self._get_json("/api")

    def stream_names(self) -> list[str]:
        return sorted(self._get_json("/api/streams"))

    def stream_info(self, name: str) -> dict[str, Any]:
        return self._get_json(f"/api/streams?src={name}")
