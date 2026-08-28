import json
import os
from urllib.request import urlopen


def main() -> None:
    port = int(os.environ.get("WEB_PORT", "8080"))
    with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as response:
        body = json.load(response)
        status = response.status
    if status != 200 or body.get("status") not in {"ready", "degraded"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
