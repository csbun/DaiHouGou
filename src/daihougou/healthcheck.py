import json
import os
from collections.abc import Mapping
from urllib.request import urlopen


def health_url(mapping: Mapping[str, str] = os.environ) -> str:
    host = mapping.get("WEB_HOST", "127.0.0.1")
    if host == "0.0.0.0":
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = int(mapping.get("WEB_PORT", "8080"))
    return f"http://{host}:{port}/healthz"


def main() -> None:
    with urlopen(health_url(), timeout=3) as response:
        body = json.load(response)
        status = response.status
    if status != 200 or body.get("status") not in {"ready", "degraded"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
