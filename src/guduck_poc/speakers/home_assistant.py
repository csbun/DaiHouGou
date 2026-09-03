import json
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from guduck_poc.speakers.base import SpeakResult

Poster = Callable[[str, str, dict[str, object], int], tuple[int, Any]]


def _post(url: str, token: str, payload: dict[str, object], timeout: int) -> tuple[int, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        return response.status, json.load(response)


class HomeAssistantSpeaker:
    def __init__(
        self,
        base_url: str,
        token: str,
        service: str,
        entity_id: str,
        text_field: str,
        extra_data: dict[str, object],
        post: Poster = _post,
    ) -> None:
        domain, action = service.split(".", 1)
        self._url = f"{base_url.rstrip('/')}/api/services/{domain}/{action}"
        self._token = token
        self._entity_id = entity_id
        self._text_field = text_field
        self._extra_data = extra_data
        self._post = post

    def speak(self, text: str) -> SpeakResult:
        payload = {
            "title": "GuDuck",
            **self._extra_data,
            "entity_id": self._entity_id,
            self._text_field: text,
        }
        started = time.monotonic()
        try:
            status, _ = self._post(self._url, self._token, payload, 30)
            error = ""
        except (HTTPError, URLError, TimeoutError) as exc:
            status, error = 0, type(exc).__name__
        latency_ms = round((time.monotonic() - started) * 1000)
        return SpeakResult(200 <= status < 300, latency_ms, status, error)
