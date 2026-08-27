# Device Compatibility PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a disposable, repeatable probe that proves whether the two Xiaomi cameras can provide stable streams through go2rtc and whether the L05C speaker is more reliable through Home Assistant or direct MiService control on the target i3-3217U/4 GB server.

**Architecture:** This plan deliberately stops before the childcare application, visual models, rule engine, and management page. A Python CLI drives black-box probes against go2rtc, a direct MiService speaker backend, and an optional Home Assistant backend; every attempt is written as redacted JSONL and summarized into a Markdown gate report. go2rtc and optional Home Assistant run as pinned, independently replaceable containers on the host network.

**Tech Stack:** Python 3.12, standard library CLI/HTTP clients, MiService 3.0.1, pytest 9.0.2, Ruff 0.16.4, FFmpeg/ffprobe, go2rtc 1.9.14, optional Home Assistant Container 2026.8.3, Docker Compose.

---

## Scope And Stop Conditions

This is a separate sub-project from the MVP. Do not implement FastAPI, SQLite, person detection, pointing detection, rules, target catalogs, or a browser UI in this plan.

The PoC passes only when all of these are true:

1. The actual MIoT model and firmware are recorded for both cameras and the L05C speaker.
2. Each camera completes a 30-minute decode test; the selected primary camera then completes an 8-hour decode test.
3. After a go2rtc restart, the primary camera returns within 60 seconds without editing configuration.
4. At least one speaker backend completes 30/30 API-accepted trials and at least 29/30 manually confirmed audible trials.
5. The selected speaker backend completes at least 99/100 API-accepted trials and 98/100 manually confirmed audible trials.
6. The server retains at least 750 MiB `MemAvailable` during the 8-hour primary-camera soak, and no process is killed by the OOM killer.
7. If Home Assistant is selected, a non-administrator token can perform the configured speaker action and survives a Home Assistant restart.

Stop instead of continuing to the MVP when:

- Neither camera passes the 30-minute test. Replace it with an H.264 RTSP/ONVIF camera as already agreed.
- Neither speaker backend meets the 30-trial gate. Preserve the reports and investigate device/account integration before writing application code.
- The 8-hour run breaches the memory floor or triggers OOM. Do not compensate by adding swap and declaring success; first measure a lower-quality stream, then upgrade RAM if needed.

## File Map

| Path | Responsibility |
|---|---|
| `.gitignore` | Exclude secrets, generated go2rtc/HA state, and PoC artifacts. |
| `.env.poc.example` | Document every non-secret and secret input without real credentials. |
| `pyproject.toml` | Pin Python runtime dependencies and tool configuration. |
| `docker/poc.Dockerfile` | Reproducible probe image with Python and FFmpeg. |
| `compose.poc.yaml` | Pinned go2rtc, probe, and optional HA services. |
| `deploy/go2rtc/go2rtc.example.yaml` | Local-only go2rtc API/RTSP endpoints and JSON logs; copied to ignored runtime state. |
| `deploy/homeassistant/configuration.yaml` | Minimal HA configuration with Recorder disabled for the PoC. |
| `src/daihougou_poc/settings.py` | Validate environment input; never log secret values. |
| `src/daihougou_poc/events.py` | Define JSONL event records and secret redaction. |
| `src/daihougou_poc/report.py` | Append/read events and calculate trial summaries. |
| `src/daihougou_poc/go2rtc.py` | Query go2rtc health, stream metadata, and snapshots. |
| `src/daihougou_poc/camera.py` | Run FFmpeg decode soaks and recovery polling. |
| `src/daihougou_poc/speakers/base.py` | Define the speaker probe protocol and result type. |
| `src/daihougou_poc/speakers/direct.py` | Invoke L05C MIoT action `5-3` through MiService. |
| `src/daihougou_poc/speakers/home_assistant.py` | Invoke a configured HA service over its REST API. |
| `src/daihougou_poc/speaker_trials.py` | Run numbered trials and add manual audible annotations. |
| `src/daihougou_poc/gate.py` | Produce the pass/fail Markdown decision report. |
| `src/daihougou_poc/cli.py` | Expose inventory, camera, speaker, resource, and report commands. |
| `scripts/capture-host-stats.sh` | Capture host memory, load, Docker usage, and kernel OOM evidence. |
| `config/poc-devices.example.json` | Schema/example for non-secret device inventory. |
| `tests/` | Contract tests that require no Xiaomi account or physical device. |
| `docs/poc-runbook.md` | Exact operator steps, including the few required human confirmations. |

## Task 1: Scaffold The Reproducible Probe

**Files:**
- Create: `.gitignore`
- Create: `.env.poc.example`
- Create: `pyproject.toml`
- Create: `docker/poc.Dockerfile`
- Create: `src/daihougou_poc/__init__.py`
- Create: `src/daihougou_poc/cli.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write the failing CLI smoke test**

```python
# tests/test_cli.py
from daihougou_poc.cli import build_parser


def test_cli_lists_required_command_groups() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "inventory" in help_text
    assert "camera" in help_text
    assert "speaker" in help_text
    assert "report" in help_text
```

- [ ] **Step 2: Add pinned project and container metadata**

```toml
# pyproject.toml
[build-system]
requires = ["setuptools==80.9.0"]
build-backend = "setuptools.build_meta"

[project]
name = "daihougou-poc"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["miservice==3.0.1"]

[project.optional-dependencies]
dev = ["pytest==9.0.2", "ruff==0.16.4"]

[project.scripts]
daihougou-poc = "daihougou_poc.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

```dockerfile
# docker/poc.Dockerfile
FROM python:3.12.11-slim-bookworm

RUN apt-get update \
    && apt-get install --no-install-recommends -y ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /workspace
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir '.[dev]'
ENTRYPOINT ["daihougou-poc"]
```

```gitignore
# .gitignore
.env.poc
artifacts/
config/poc-devices.json
deploy/go2rtc/state/
deploy/homeassistant/state/
__pycache__/
.pytest_cache/
.ruff_cache/
*.pyc
```

```dotenv
# .env.poc.example
POC_ARTIFACT_DIR=/workspace/artifacts/poc
GO2RTC_API_URL=http://127.0.0.1:1984
GO2RTC_RTSP_BASE=rtsp://127.0.0.1:8554
MI_USER=
MI_PASS=
MI_DID=
HA_BASE_URL=http://127.0.0.1:8123
HA_ACCESS_TOKEN=
HA_SPEAKER_SERVICE=notify.send_message
HA_SPEAKER_ENTITY=
HA_TEXT_FIELD=message
HA_EXTRA_DATA_JSON={}
```

- [ ] **Step 3: Run the test to verify the CLI is missing**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test .`

Expected: image build fails because `daihougou_poc.cli` does not exist yet.

- [ ] **Step 4: Implement the minimal command parser**

```python
# src/daihougou_poc/cli.py
import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daihougou-poc")
    commands = parser.add_subparsers(dest="group", required=True)
    for name in ("inventory", "camera", "speaker", "report"):
        commands.add_parser(name)
    return parser


def main() -> None:
    build_parser().parse_args()
```

Create an empty `src/daihougou_poc/__init__.py`.

- [ ] **Step 5: Build and verify the scaffold**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test .`

Run: `docker run --rm --entrypoint pytest daihougou-poc:test -q`

Expected: `1 passed`.

Run: `docker run --rm --entrypoint ruff daihougou-poc:test check src tests`

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add .gitignore .env.poc.example pyproject.toml docker src tests
git commit -m "build: scaffold device compatibility probe"
```

## Task 2: Add Validated Settings And Redacted JSONL Events

**Files:**
- Create: `src/daihougou_poc/settings.py`
- Create: `src/daihougou_poc/events.py`
- Create: `src/daihougou_poc/report.py`
- Test: `tests/test_settings.py`
- Test: `tests/test_report.py`

- [ ] **Step 1: Write failing settings and redaction tests**

```python
# tests/test_settings.py
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
```

```python
# tests/test_report.py
import json
from pathlib import Path

from daihougou_poc.events import ProbeEvent
from daihougou_poc.report import JsonlReport


def test_report_redacts_secret_fields(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    report = JsonlReport(path)
    report.append(
        ProbeEvent.create(
            component="speaker.direct",
            operation="speak",
            success=False,
            details={"token": "secret", "password": "secret", "code": 401},
        )
    )
    data = json.loads(path.read_text().strip())
    assert data["details"] == {"token": "[REDACTED]", "password": "[REDACTED]", "code": 401}
```

- [ ] **Step 2: Run tests and confirm import failures**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test . && docker run --rm --entrypoint pytest daihougou-poc:test -q`

Expected: collection fails for missing `settings`, `events`, and `report` modules.

- [ ] **Step 3: Implement settings validation**

```python
# src/daihougou_poc/settings.py
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


def _require_loopback(value: str, name: str) -> str:
    host = urlparse(value).hostname
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError(f"{name} must use a loopback address during the PoC")
    return value.rstrip("/")


@dataclass(frozen=True)
class Settings:
    artifact_dir: Path
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
```

- [ ] **Step 4: Implement event creation, recursive redaction, and JSONL persistence**

```python
# src/daihougou_poc/events.py
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

SECRET_FRAGMENTS = ("token", "password", "pass", "secret", "authorization", "cookie")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if any(part in key.lower() for part in SECRET_FRAGMENTS) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


@dataclass(frozen=True)
class ProbeEvent:
    timestamp: str
    correlation_id: str
    component: str
    operation: str
    success: bool
    latency_ms: int | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, component: str, operation: str, success: bool, **kwargs: Any) -> "ProbeEvent":
        return cls(
            timestamp=datetime.now(UTC).isoformat(),
            correlation_id=kwargs.pop("correlation_id", str(uuid4())),
            component=component,
            operation=operation,
            success=success,
            latency_ms=kwargs.pop("latency_ms", None),
            details=redact(kwargs.pop("details", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
```

```python
# src/daihougou_poc/report.py
import json
from pathlib import Path

from daihougou_poc.events import ProbeEvent


class JsonlReport:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: ProbeEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=True) + "\n")

    def read(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]
```

- [ ] **Step 5: Run tests and lint**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test . && docker run --rm --entrypoint pytest daihougou-poc:test -q`

Expected: `3 passed`.

Run: `docker run --rm --entrypoint ruff daihougou-poc:test check src tests`

Expected: `All checks passed!`

- [ ] **Step 6: Commit**

```bash
git add src/daihougou_poc tests
git commit -m "feat: add redacted probe event store"
```

## Task 3: Deploy And Probe go2rtc

**Files:**
- Create: `compose.poc.yaml`
- Create: `deploy/go2rtc/go2rtc.example.yaml`
- Create: `src/daihougou_poc/go2rtc.py`
- Create: `config/poc-devices.example.json`
- Test: `tests/test_go2rtc.py`

- [ ] **Step 1: Write a failing go2rtc client contract test**

```python
# tests/test_go2rtc.py
from daihougou_poc.go2rtc import Go2RtcClient


def test_stream_names_are_sorted() -> None:
    responses = {
        "/api": {"host": "127.0.0.1:1984"},
        "/api/streams": {"xiaobai_25k": {}, "xiaobai": {}},
    }
    client = Go2RtcClient("http://127.0.0.1:1984", get_json=lambda path: responses[path])
    assert client.health()["host"] == "127.0.0.1:1984"
    assert client.stream_names() == ["xiaobai", "xiaobai_25k"]
```

- [ ] **Step 2: Run the test and verify the missing module**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test . && docker run --rm --entrypoint pytest daihougou-poc:test tests/test_go2rtc.py -q`

Expected: collection fails because `daihougou_poc.go2rtc` does not exist.

- [ ] **Step 3: Add the local-only go2rtc deployment**

```yaml
# deploy/go2rtc/go2rtc.example.yaml
api:
  listen: "127.0.0.1:1984"
  local_auth: false
rtsp:
  listen: "127.0.0.1:8554"
webrtc:
  listen: ""
log:
  format: json
  level: info
  output: stdout
```

```yaml
# compose.poc.yaml
services:
  go2rtc:
    image: ghcr.io/alexxit/go2rtc:1.9.14
    container_name: daihougou-go2rtc
    network_mode: host
    restart: unless-stopped
    volumes:
      - ./deploy/go2rtc/state:/config
    command: ["-c", "/config/go2rtc.yaml"]

  probe:
    build:
      context: .
      dockerfile: docker/poc.Dockerfile
    image: daihougou-poc:local
    network_mode: host
    env_file: .env.poc
    volumes:
      - ./artifacts:/workspace/artifacts
      - ./config:/workspace/config:ro
    profiles: ["tools"]

  homeassistant:
    image: ghcr.io/home-assistant/home-assistant:2026.8.3
    container_name: daihougou-homeassistant
    network_mode: host
    restart: unless-stopped
    stop_grace_period: 60s
    volumes:
      - ./deploy/homeassistant/state:/config
    profiles: ["ha"]
```

The `go2rtc` API and RTSP port remain loopback-only. To configure Xiaomi interactively from another computer, use `ssh -L 1984:127.0.0.1:1984 <server>` and browse to `http://127.0.0.1:1984`; never expose port 1984 on the router.

- [ ] **Step 4: Implement the HTTP client with injectable transport**

```python
# src/daihougou_poc/go2rtc.py
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
        with urlopen(self.base_url + path, timeout=10) as response:  # noqa: S310
            return json.load(response)

    def health(self) -> dict[str, Any]:
        return self._get_json("/api")

    def stream_names(self) -> list[str]:
        return sorted(self._get_json("/api/streams"))

    def stream_info(self, name: str) -> dict[str, Any]:
        return self._get_json(f"/api/streams?src={name}")
```

```json
// config/poc-devices.example.json
{
  "cameras": [
    {"name": "xiaobai", "miot_model": "", "firmware": "", "ip": "", "codec": ""},
    {"name": "xiaobai_25k", "miot_model": "", "firmware": "", "ip": "", "codec": ""}
  ],
  "speaker": {"name": "xiaomi_play_enhanced", "miot_model": "xiaomi.wifispeaker.l05c", "firmware": ""},
  "primary_camera": "xiaobai_25k"
}
```

- [ ] **Step 5: Test, start go2rtc, and verify health**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test . && docker run --rm --entrypoint pytest daihougou-poc:test tests/test_go2rtc.py -q`

Expected: `1 passed`.

Run: `cp .env.poc.example .env.poc && mkdir -p deploy/go2rtc/state && cp deploy/go2rtc/go2rtc.example.yaml deploy/go2rtc/state/go2rtc.yaml && cp config/poc-devices.example.json config/poc-devices.json`

Run: `docker compose -f compose.poc.yaml up -d go2rtc`

Run: `curl --fail http://127.0.0.1:1984/api`

Expected: JSON containing `config_path`, `host`, and RTSP listener information.

- [ ] **Step 6: Configure both Xiaomi streams and record immutable device facts**

Through the SSH tunnel and go2rtc Web UI:

1. Add the Xiaomi account.
2. Add both cameras as `xiaobai` and `xiaobai_25k`.
3. Select the lowest usable stream quality (`subtype=sd` first).
4. Fill `config/poc-devices.json` from the discovered model/firmware/IP and active codec.
5. Run `curl --fail http://127.0.0.1:1984/api/streams` and confirm both names exist.

Do not put the Xiaomi token, username, password, DID, or full Xiaomi source URL in `poc-devices.json` or Git.

- [ ] **Step 7: Commit**

```bash
git add compose.poc.yaml deploy/go2rtc/go2rtc.example.yaml config/poc-devices.example.json src/daihougou_poc/go2rtc.py tests/test_go2rtc.py
git commit -m "feat: add local go2rtc compatibility probe"
```

## Task 4: Implement The Direct L05C Probe

**Files:**
- Create: `src/daihougou_poc/speakers/__init__.py`
- Create: `src/daihougou_poc/speakers/base.py`
- Create: `src/daihougou_poc/speakers/direct.py`
- Test: `tests/speakers/test_direct.py`

- [ ] **Step 1: Write the failing direct-backend test**

```python
# tests/speakers/test_direct.py
import json

from daihougou_poc.speakers.direct import DirectSpeaker


def test_direct_speaker_uses_l05c_play_text_action_without_a_shell() -> None:
    captured = {}

    def run(command: list[str], env: dict[str, str], timeout: int):
        captured.update(command=command, env=env, timeout=timeout)
        return 0, '{"code":0}', ""

    speaker = DirectSpeaker("user", "pass", "did", run=run)
    result = speaker.speak("zebra. zebra.")
    payload = json.loads(captured["command"][4])
    assert captured["command"][:3] == ["python", "-m", "miservice"]
    assert payload == {"did": "did", "siid": 5, "aiid": 3, "in": ["zebra. zebra."]}
    assert result.success is True
```

- [ ] **Step 2: Run the test and verify imports fail**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test . && docker run --rm --entrypoint pytest daihougou-poc:test tests/speakers/test_direct.py -q`

Expected: collection fails because the speaker modules do not exist.

- [ ] **Step 3: Implement the backend protocol and subprocess result**

```python
# src/daihougou_poc/speakers/base.py
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SpeakResult:
    success: bool
    latency_ms: int
    code: int | str | None
    error: str = ""


class Speaker(Protocol):
    def speak(self, text: str) -> SpeakResult: ...
```

```python
# src/daihougou_poc/speakers/direct.py
import json
import os
import subprocess
import time
from collections.abc import Callable

from daihougou_poc.speakers.base import SpeakResult

Runner = Callable[[list[str], dict[str, str], int], tuple[int, str, str]]


def _run(command: list[str], env: dict[str, str], timeout: int) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        env=env,
        timeout=timeout,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.returncode, completed.stdout, completed.stderr


class DirectSpeaker:
    def __init__(self, user: str, password: str, did: str, run: Runner = _run) -> None:
        if not user or not password or not did:
            raise ValueError("MI_USER, MI_PASS, and MI_DID are required")
        self._env = {**os.environ, "MI_USER": user, "MI_PASS": password, "MI_DID": did}
        self._did = did
        self._run = run

    def speak(self, text: str) -> SpeakResult:
        payload = json.dumps({"did": self._did, "siid": 5, "aiid": 3, "in": [text]})
        started = time.monotonic()
        returncode, stdout, stderr = self._run(
            ["python", "-m", "miservice", "action", payload], self._env, 30
        )
        latency_ms = round((time.monotonic() - started) * 1000)
        try:
            body = json.loads(stdout)
        except json.JSONDecodeError:
            body = {}
        code = body.get("code", returncode)
        success = returncode == 0 and code in {0, None}
        return SpeakResult(success=success, latency_ms=latency_ms, code=code, error=stderr[-500:])
```

Create an empty `src/daihougou_poc/speakers/__init__.py`.

- [ ] **Step 4: Test and lint**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test . && docker run --rm --entrypoint pytest daihougou-poc:test tests/speakers/test_direct.py -q`

Expected: `1 passed`.

Run: `docker run --rm --entrypoint ruff daihougou-poc:test check src tests`

Expected: `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/daihougou_poc/speakers tests/speakers
git commit -m "feat: add direct L05C speaker probe"
```

## Task 5: Implement The Optional Home Assistant Probe

**Files:**
- Create: `deploy/homeassistant/configuration.yaml`
- Create: `src/daihougou_poc/speakers/home_assistant.py`
- Test: `tests/speakers/test_home_assistant.py`

- [ ] **Step 1: Write the failing HA adapter test**

```python
# tests/speakers/test_home_assistant.py
from daihougou_poc.speakers.home_assistant import HomeAssistantSpeaker


def test_ha_speaker_posts_configured_service_payload() -> None:
    captured = {}

    def post(url: str, token: str, payload: dict[str, object], timeout: int):
        captured.update(url=url, token=token, payload=payload, timeout=timeout)
        return 200, [{"entity_id": "notify.xiaoai"}]

    speaker = HomeAssistantSpeaker(
        base_url="http://127.0.0.1:8123",
        token="token",
        service="notify.send_message",
        entity_id="notify.xiaoai",
        text_field="message",
        extra_data={"title": "DaiHouGou"},
        post=post,
    )
    result = speaker.speak("hello")
    assert captured["url"].endswith("/api/services/notify/send_message")
    assert captured["payload"] == {
        "entity_id": "notify.xiaoai",
        "message": "hello",
        "title": "DaiHouGou",
    }
    assert result.success is True
```

- [ ] **Step 2: Run the test and verify the module is absent**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test . && docker run --rm --entrypoint pytest daihougou-poc:test tests/speakers/test_home_assistant.py -q`

Expected: collection fails for missing `home_assistant`.

- [ ] **Step 3: Add minimal Home Assistant configuration**

```yaml
# deploy/homeassistant/configuration.yaml
default_config:

recorder:
  purge_keep_days: 1
  exclude:
    entity_globs:
      - "*"
```

On first HA start, copy this file into `deploy/homeassistant/state/configuration.yaml` before onboarding. Do not commit the `state` directory.

- [ ] **Step 4: Implement the generic HA service adapter**

```python
# src/daihougou_poc/speakers/home_assistant.py
import json
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from daihougou_poc.speakers.base import SpeakResult

Poster = Callable[[str, str, dict[str, object], int], tuple[int, Any]]


def _post(url: str, token: str, payload: dict[str, object], timeout: int) -> tuple[int, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
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
        payload = {**self._extra_data, "entity_id": self._entity_id, self._text_field: text}
        started = time.monotonic()
        try:
            status, _ = self._post(self._url, self._token, payload, 30)
            error = ""
        except (HTTPError, URLError, TimeoutError) as exc:
            status, error = 0, type(exc).__name__
        latency_ms = round((time.monotonic() - started) * 1000)
        return SpeakResult(200 <= status < 300, latency_ms, status, error)
```

- [ ] **Step 5: Test the adapter**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test . && docker run --rm --entrypoint pytest daihougou-poc:test tests/speakers/test_home_assistant.py -q`

Expected: `1 passed`.

- [ ] **Step 6: Start and configure the temporary HA instance**

Run:

```bash
mkdir -p deploy/homeassistant/state
cp deploy/homeassistant/configuration.yaml deploy/homeassistant/state/configuration.yaml
docker compose -f compose.poc.yaml --profile ha up -d homeassistant
```

Complete onboarding at `http://<server-ip>:8123`, add the Xiaomi integration, and bind the L05C speaker. Create a dedicated non-administrator HA user, log in as that user, create a Long-Lived Access Token, and put only that token in `.env.poc`. Use Developer Tools to determine the working service/entity/text-field combination, then set `HA_SPEAKER_SERVICE`, `HA_SPEAKER_ENTITY`, `HA_TEXT_FIELD`, and `HA_EXTRA_DATA_JSON`.

If the non-administrator user cannot perform the action, record that result; do not silently substitute an owner token.

- [ ] **Step 7: Commit**

```bash
git add deploy/homeassistant/configuration.yaml src/daihougou_poc/speakers/home_assistant.py tests/speakers/test_home_assistant.py
git commit -m "feat: add optional Home Assistant speaker probe"
```

## Task 6: Run Repeatable Speaker Trials And Audible Annotations

**Files:**
- Create: `src/daihougou_poc/speaker_trials.py`
- Modify: `src/daihougou_poc/cli.py`
- Test: `tests/test_speaker_trials.py`

- [ ] **Step 1: Write the failing trial orchestration test**

```python
# tests/test_speaker_trials.py
from pathlib import Path

from daihougou_poc.report import JsonlReport
from daihougou_poc.speaker_trials import annotate_audible, run_trials
from daihougou_poc.speakers.base import SpeakResult


class FakeSpeaker:
    def speak(self, text: str) -> SpeakResult:
        return SpeakResult(True, 12, 0)


def test_trials_are_numbered_and_manual_misses_are_separate(tmp_path: Path) -> None:
    report = JsonlReport(tmp_path / "events.jsonl")
    run_id = run_trials("direct", FakeSpeaker(), report, count=3, interval_seconds=0)
    annotate_audible(report, run_id=run_id, count=3, missed={2})
    events = report.read()
    assert [event["details"]["trial"] for event in events[:3]] == [1, 2, 3]
    assert events[-1]["details"]["audible_successes"] == 2
```

- [ ] **Step 2: Run the test and verify failure**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test . && docker run --rm --entrypoint pytest daihougou-poc:test tests/test_speaker_trials.py -q`

Expected: collection fails for missing `speaker_trials`.

- [ ] **Step 3: Implement numbered trials and annotations**

```python
# src/daihougou_poc/speaker_trials.py
import time
from uuid import uuid4

from daihougou_poc.events import ProbeEvent
from daihougou_poc.report import JsonlReport
from daihougou_poc.speakers.base import Speaker


def run_trials(
    backend: str,
    speaker: Speaker,
    report: JsonlReport,
    count: int,
    interval_seconds: float,
) -> str:
    run_id = str(uuid4())
    for trial in range(1, count + 1):
        result = speaker.speak(f"播报测试 {trial}")
        report.append(
            ProbeEvent.create(
                component=f"speaker.{backend}",
                operation="speak",
                success=result.success,
                correlation_id=run_id,
                latency_ms=result.latency_ms,
                details={"trial": trial, "code": result.code, "error": result.error},
            )
        )
        if trial < count:
            time.sleep(interval_seconds)
    return run_id


def annotate_audible(report: JsonlReport, run_id: str, count: int, missed: set[int]) -> None:
    invalid = {number for number in missed if number < 1 or number > count}
    if invalid:
        raise ValueError(f"missed trial numbers out of range: {sorted(invalid)}")
    report.append(
        ProbeEvent.create(
            component="speaker.manual",
            operation="audible_annotation",
            success=(count - len(missed)) / count >= 0.98,
            correlation_id=run_id,
            details={
                "count": count,
                "missed": sorted(missed),
                "audible_successes": count - len(missed),
            },
        )
    )
```

- [ ] **Step 4: Wire explicit CLI subcommands**

Replace the placeholder command construction in `cli.py` with subparsers for:

```text
speaker run --backend direct|ha --count N --interval-seconds N
speaker annotate --run-id UUID --count N --missed 2,7
```

The handler must build `DirectSpeaker` or `HomeAssistantSpeaker` from `Settings`, parse `HA_EXTRA_DATA_JSON` with `json.loads`, write to `${POC_ARTIFACT_DIR}/events.jsonl`, print the generated `run_id`, and never print settings or environment contents. Keep construction in small helpers named `_direct_speaker(settings)` and `_ha_speaker(settings)` so tests can replace them.

- [ ] **Step 5: Test and perform the 30-trial comparison**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test . && docker run --rm --entrypoint pytest daihougou-poc:test -q`

Expected: all tests pass.

Run each backend while an adult listens and records missed trial numbers:

```bash
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker run --backend direct --count 30 --interval-seconds 8
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker annotate --run-id <printed-direct-run-id> --count 30 --missed <comma-separated-misses>
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker run --backend ha --count 30 --interval-seconds 8
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker annotate --run-id <printed-ha-run-id> --count 30 --missed <comma-separated-misses>
```

If no trials were missed, pass `--missed ''`. The operator must not infer audible success from HTTP/MIoT response codes.

- [ ] **Step 6: Commit**

```bash
git add src/daihougou_poc/cli.py src/daihougou_poc/speaker_trials.py tests/test_speaker_trials.py
git commit -m "feat: add observable speaker reliability trials"
```

## Task 7: Add Camera Decode, Recovery, And Resource Soaks

**Files:**
- Create: `src/daihougou_poc/camera.py`
- Create: `scripts/capture-host-stats.sh`
- Modify: `src/daihougou_poc/cli.py`
- Test: `tests/test_camera.py`

- [ ] **Step 1: Write the failing FFmpeg command test**

```python
# tests/test_camera.py
from daihougou_poc.camera import build_ffmpeg_command


def test_decode_uses_tcp_without_audio_or_output_files() -> None:
    command = build_ffmpeg_command("rtsp://127.0.0.1:8554/xiaobai", 1800)
    assert command == [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-t",
        "1800",
        "-i",
        "rtsp://127.0.0.1:8554/xiaobai",
        "-map",
        "0:v:0",
        "-an",
        "-f",
        "null",
        "-",
    ]
```

- [ ] **Step 2: Run the test and verify failure**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test . && docker run --rm --entrypoint pytest daihougou-poc:test tests/test_camera.py -q`

Expected: collection fails for missing `camera`.

- [ ] **Step 3: Implement decode and recovery primitives**

```python
# src/daihougou_poc/camera.py
import subprocess
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import urlopen


def build_ffmpeg_command(rtsp_url: str, duration_seconds: int) -> list[str]:
    return [
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
        "-rtsp_transport", "tcp", "-t", str(duration_seconds), "-i", rtsp_url,
        "-map", "0:v:0", "-an", "-f", "null", "-",
    ]


def decode(rtsp_url: str, duration_seconds: int) -> tuple[bool, int, str]:
    started = time.monotonic()
    completed = subprocess.run(
        build_ffmpeg_command(rtsp_url, duration_seconds),
        timeout=duration_seconds + 60,
        check=False,
        capture_output=True,
        text=True,
    )
    elapsed = round(time.monotonic() - started)
    return completed.returncode == 0, elapsed, completed.stderr[-2000:]


def wait_for_snapshot(api_url: str, stream: str, max_seconds: int) -> int | None:
    started = time.monotonic()
    query = urlencode({"src": stream, "width": 320})
    while time.monotonic() - started <= max_seconds:
        try:
            with urlopen(f"{api_url}/api/frame.jpeg?{query}", timeout=5) as response:  # noqa: S310
                if response.status == 200 and response.headers.get_content_type() == "image/jpeg":
                    return round(time.monotonic() - started)
        except (URLError, TimeoutError):
            time.sleep(2)
    return None
```

- [ ] **Step 4: Add host resource sampling without mounting the Docker socket**

```bash
#!/usr/bin/env bash
# scripts/capture-host-stats.sh
set -euo pipefail

output_path="${1:?usage: capture-host-stats.sh OUTPUT_PATH [INTERVAL_SECONDS]}"
interval_seconds="${2:-30}"
mkdir -p "$(dirname "$output_path")"

while true; do
  {
    date --iso-8601=seconds
    awk '/MemAvailable|SwapFree|SwapTotal/ {print}' /proc/meminfo
    cat /proc/loadavg
    docker stats --no-stream --format '{{json .}}' daihougou-go2rtc daihougou-homeassistant 2>/dev/null || true
    dmesg --since '1 minute ago' 2>/dev/null | grep -i -E 'out of memory|killed process' || true
  } >> "$output_path"
  sleep "$interval_seconds"
done
```

Set the executable bit with `chmod +x scripts/capture-host-stats.sh`.

- [ ] **Step 5: Add camera CLI commands and event writes**

Add these exact commands to `cli.py`:

```text
camera decode --stream NAME --duration-seconds N
camera wait --stream NAME --max-seconds N
```

`camera decode` must construct `${GO2RTC_RTSP_BASE}/{stream}`, call `decode`, and append a `camera.<stream>/decode` event containing `duration_requested`, `duration_actual`, and the last 2,000 error characters. `camera wait` must call `wait_for_snapshot` and append `camera.<stream>/recovery` with `recovery_seconds`; success is `recovery_seconds is not None`.

- [ ] **Step 6: Run tests and the 30-minute camera gate**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test . && docker run --rm --entrypoint pytest daihougou-poc:test -q`

Expected: all tests pass.

Start host sampling in a separate terminal:

```bash
scripts/capture-host-stats.sh artifacts/poc/host-stats-30m.log 30
```

Run both cameras separately:

```bash
docker compose -f compose.poc.yaml --profile tools run --rm probe camera decode --stream xiaobai --duration-seconds 1800
docker compose -f compose.poc.yaml --profile tools run --rm probe camera decode --stream xiaobai_25k --duration-seconds 1800
```

Expected: both commands exit zero and each JSONL event reports `duration_actual >= 1800`.

- [ ] **Step 7: Test deterministic recovery**

Run:

```bash
docker compose -f compose.poc.yaml restart go2rtc
docker compose -f compose.poc.yaml --profile tools run --rm probe camera wait --stream <primary-camera> --max-seconds 60
```

Expected: exit zero and `recovery_seconds <= 60`.

- [ ] **Step 8: Run the 8-hour primary-camera gate**

Run:

```bash
scripts/capture-host-stats.sh artifacts/poc/host-stats-8h.log 60
docker compose -f compose.poc.yaml --profile tools run --rm probe camera decode --stream <primary-camera> --duration-seconds 28800
```

Expected: decode exits zero, no OOM line appears, and every `MemAvailable` sample is at least `768000 kB`.

- [ ] **Step 9: Commit**

```bash
git add src/daihougou_poc/camera.py src/daihougou_poc/cli.py scripts/capture-host-stats.sh tests/test_camera.py
git commit -m "feat: add camera stability and resource soaks"
```

## Task 8: Generate The Architecture Gate Report

**Files:**
- Create: `src/daihougou_poc/gate.py`
- Modify: `src/daihougou_poc/cli.py`
- Create: `docs/poc-runbook.md`
- Test: `tests/test_gate.py`

- [ ] **Step 1: Write the failing gate-decision test**

```python
# tests/test_gate.py
from daihougou_poc.gate import choose_speaker_backend


def test_gate_prefers_more_reliable_backend_then_lower_p95() -> None:
    direct = {"api_successes": 100, "audible_successes": 98, "count": 100, "p95_ms": 1200}
    ha = {"api_successes": 100, "audible_successes": 99, "count": 100, "p95_ms": 1800}
    assert choose_speaker_backend({"direct": direct, "ha": ha}) == "ha"


def test_gate_rejects_backends_below_minimum() -> None:
    direct = {"api_successes": 98, "audible_successes": 98, "count": 100, "p95_ms": 900}
    assert choose_speaker_backend({"direct": direct}) is None
```

- [ ] **Step 2: Run the test and verify failure**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test . && docker run --rm --entrypoint pytest daihougou-poc:test tests/test_gate.py -q`

Expected: collection fails for missing `gate`.

- [ ] **Step 3: Implement deterministic backend selection**

```python
# src/daihougou_poc/gate.py
from typing import TypedDict


class BackendSummary(TypedDict):
    api_successes: int
    audible_successes: int
    count: int
    p95_ms: int


def choose_speaker_backend(backends: dict[str, BackendSummary]) -> str | None:
    passing = {
        name: result
        for name, result in backends.items()
        if result["count"] >= 100
        and result["api_successes"] >= 99
        and result["audible_successes"] >= 98
    }
    if not passing:
        return None
    return min(
        passing,
        key=lambda name: (
            -passing[name]["audible_successes"],
            -passing[name]["api_successes"],
            passing[name]["p95_ms"],
            name,
        ),
    )
```

Add `summarize_speaker_events` and `render_gate_report` in the same module. Use this aggregation contract so the gate report is reproducible:

```python
def summarize_speaker_events(events: list[dict[str, object]], backend: str) -> BackendSummary:
    trials = [
        event for event in events
        if event["component"] == f"speaker.{backend}" and event["operation"] == "speak"
    ]
    annotations = [
        event for event in events
        if event["component"] == "speaker.manual"
        and event["operation"] == "audible_annotation"
        and event["correlation_id"] in {event["correlation_id"] for event in trials}
    ]
    latencies = sorted(
        int(event["latency_ms"])
        for event in trials
        if event["latency_ms"] is not None
    )
    p95_index = max(0, min(len(latencies) - 1, (len(latencies) * 95 + 99) // 100 - 1))
    audible_successes = sum(
        int(annotation["details"]["audible_successes"])
        for annotation in annotations
    )
    return {
        "api_successes": sum(bool(event["success"]) for event in trials),
        "audible_successes": audible_successes,
        "count": len(trials),
        "p95_ms": latencies[p95_index] if latencies else 0,
    }


def render_gate_report(
    events: list[dict[str, object]], inventory: dict[str, object], host_stats: str
) -> str:
    summaries = {
        backend: summarize_speaker_events(events, backend)
        for backend in ("direct", "ha")
        if any(event["component"] == f"speaker.{backend}" for event in events)
    }
    selected = choose_speaker_backend(summaries)
    camera_events = [event for event in events if str(event["component"]).startswith("camera.")]
    expected_camera_count = len(inventory.get("cameras", []))
    camera_decodes = [
        event for event in camera_events
        if event["operation"] == "decode" and event["details"].get("duration_requested") == 1800
    ]
    camera_pass = len(camera_decodes) >= expected_camera_count and all(
        bool(event["success"])
        for event in camera_decodes
    )
    memory_floor_pass = all(
        int(line.split()[1]) >= 768000
        for line in host_stats.splitlines()
        if line.startswith("MemAvailable:")
    )
    status = "PASS" if selected and camera_pass and memory_floor_pass else "FAIL"
    return "\n".join(
        [
            "# Device Compatibility Gate",
            "## Inventory",
            f"- Cameras: `{inventory.get('cameras', [])}`",
            f"- Speaker: `{inventory.get('speaker', {})}`",
            "## Camera 30-Minute Results",
            f"- { 'PASS' if camera_pass else 'FAIL' }",
            "## Primary Camera 8-Hour Result",
            f"- { 'PASS' if any(event['details'].get('duration_requested') == 28800 and event['success'] for event in camera_events) else 'NOT RUN' }",
            "## Recovery Result",
            f"- { 'PASS' if any(event['operation'] == 'recovery' and event['success'] for event in camera_events) else 'NOT RUN' }",
            "## Speaker 30-Trial Results",
            *[f"- {name}: {summary}" for name, summary in summaries.items() if summary["count"] == 30],
            "## Speaker 100-Trial Results",
            *[f"- {name}: {summary}" for name, summary in summaries.items() if summary["count"] >= 100],
            "## Host Resource Floor",
            f"- { 'PASS' if memory_floor_pass else 'FAIL' }",
            "## Selected Speaker Backend",
            f"- `{selected or 'none'}`",
            "## Stop Or Continue Decision",
            f"- **{status}**",
        ]
    )
```

The report must produce these sections with explicit `PASS`, `FAIL`, or `NOT RUN` values:

```markdown
# Device Compatibility Gate
## Inventory
## Camera 30-Minute Results
## Primary Camera 8-Hour Result
## Recovery Result
## Speaker 30-Trial Results
## Speaker 100-Trial Results
## Host Resource Floor
## Selected Speaker Backend
## Stop Or Continue Decision
```

The report must distinguish API acceptance from audible confirmation. It must not claim a speaker played audio merely because the API returned success.

- [ ] **Step 4: Add the report command**

Add:

```text
report gate --inventory /workspace/config/poc-devices.json --host-stats /workspace/artifacts/poc/host-stats-8h.log
```

The command must read `${POC_ARTIFACT_DIR}/events.jsonl`, call `render_gate_report`, write `${POC_ARTIFACT_DIR}/gate.md`, print only that path, and exit `0` for PASS or `2` for FAIL/NOT RUN.

- [ ] **Step 5: Write the operator runbook**

`docs/poc-runbook.md` must contain these ordered checkpoints:

1. Verify Docker Engine 23+ and at least 10 GB free disk.
2. Copy `.env.poc.example` to `.env.poc`; fill secrets locally.
3. Start go2rtc and configure both Xiaomi streams over an SSH tunnel.
4. Fill `config/poc-devices.json` without credentials.
5. Run unit tests and lint.
6. Run both 30-minute camera tests and recovery test.
7. Start temporary HA, create a non-admin user/token, and find a working L05C service action.
8. Run and manually annotate 30 direct and 30 HA speaker trials.
9. Stop any backend that fails the 30-trial gate.
10. Run 100 trials for each backend that passed, annotating audible misses.
11. Run the 8-hour primary-camera soak with host sampling.
12. Generate `gate.md`, review it, and record the chosen speaker backend.
13. Stop and remove only PoC containers with `docker compose -f compose.poc.yaml --profile ha down`; preserve `artifacts/poc`.

Include a warning that `docker compose down -v` is not used because it can delete state, and that PoC artifacts may contain device names/IP addresses even though tokens are redacted.

- [ ] **Step 6: Run complete automated verification**

Run:

```bash
docker build -f docker/poc.Dockerfile -t daihougou-poc:test .
docker run --rm --entrypoint pytest daihougou-poc:test -q
docker run --rm --entrypoint ruff daihougou-poc:test check src tests
docker compose -f compose.poc.yaml config --quiet
```

Expected: all tests pass, Ruff reports no errors, and Compose exits zero.

- [ ] **Step 7: Run the extended speaker gate and render the real report**

For each backend that passed 30 trials:

```bash
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker run --backend <backend> --count 100 --interval-seconds 8
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker annotate --run-id <printed-run-id> --count 100 --missed <comma-separated-misses>
```

Then run:

```bash
docker compose -f compose.poc.yaml --profile tools run --rm probe report gate \
  --inventory /workspace/config/poc-devices.json \
  --host-stats /workspace/artifacts/poc/host-stats-8h.log
```

Expected: `artifacts/poc/gate.md` contains an unambiguous stop/continue decision and, on PASS, one selected speaker backend.

- [ ] **Step 8: Commit**

```bash
git add src/daihougou_poc/gate.py src/daihougou_poc/cli.py tests/test_gate.py docs/poc-runbook.md
git commit -m "docs: add device compatibility gate runbook"
```

## Post-PoC Handoff

Do not begin the MVP merely because individual commands worked once. Review `artifacts/poc/gate.md` and make one of these recorded decisions:

- `CONTINUE_GO2RTC_HA`: camera passed and HA was selected.
- `CONTINUE_GO2RTC_DIRECT`: camera passed and MiService was selected.
- `REPLACE_CAMERA`: neither Xiaomi camera passed; buy an H.264 RTSP/ONVIF camera and rerun only camera tasks.
- `FIX_SPEAKER_INTEGRATION`: camera passed but neither speaker route passed.
- `UPGRADE_SERVER`: compatibility passed but the memory floor failed.

Only after a `CONTINUE_*` result should a separate MVP implementation plan be written. That plan will cover the FastAPI/Jinja management page, SQLite retention, `CameraSource`, low-frame-rate person detector benchmark, entry/absence state machine, single-slot speaker dispatcher, internal rules, quiet hours, and the experimental versioned learning-target catalog.
