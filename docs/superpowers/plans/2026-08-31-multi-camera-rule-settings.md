# 大口九多摄像头、规则全局配置与多音箱 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让大口九从 `go2rtc` 手动发现并按需检测多个摄像头，每台摄像头独立开启欢迎规则和固定配对音箱，同时支持在管理页全局编辑欢迎词。

**Architecture:** 单个 FastAPI 进程内由 `Runtime` 编排摄像头发现、按需摄像头执行单元、一个共享检测调度器和按音箱分队列的播报管理器。SQLite 保存摄像头、逐摄像头规则、全局规则配置和事件；`go2rtc` 只在启动及用户手动刷新时查询，FFmpeg 和 ONNX 模型只在至少一台摄像头启用视觉规则时运行。

**Tech Stack:** Python 3.12、FastAPI、Jinja2、标准库 `sqlite3`/`urllib`/`asyncio`、OpenCV DNN、FFmpeg、MiService、Pytest、Ruff、Docker Compose

**Spec:** `docs/superpowers/specs/2026-08-31-multi-camera-rule-settings-design.md`

## Global Constraints

- 继续使用一个 Python 进程、一个 Uvicorn worker和原数据库路径 `/var/lib/daihougou/data/daihougou.db`。
- 不迁移旧数据库 schema；发现不兼容旧表时拒绝启动并指向 runbook 的重置步骤。
- MVP 常驻应用不兼容 `MI_DID` 或单个 `STREAM_URL`；PoC 工具仍可使用 `MI_DID` 做指定
  音箱试播，常驻应用的音箱只从必填的 `MI_SPEAKERS_JSON` 读取。
- 不新增 ORM、数据库、前端框架或动态插件依赖。
- 摄像头名称始终等于 `go2rtc` 码流名称，不能在应用内重命名。
- 设备发现只发生在应用启动和用户点击“刷新摄像头”时，不添加后台发现轮询。
- 新摄像头的所有规则默认关闭，并默认配对 `MI_SPEAKERS_JSON` 第一项。
- 不限制摄像头数量；欢迎规则开启达到 4 路时只显示资源提示。
- 没有启用视觉规则时不运行 FFmpeg，也不加载 ONNX 模型。
- 同一音箱播报串行，不同音箱可以并行；目标音箱失败时不回退。
- 页面、数据库、事件和日志不得包含 DID、账号、密码、Token、Cookie、完整摄像头 URL或小米原始响应。
- 管理页继续无登录，只能部署在可信局域网，所有写操作继续执行同源与 CSRF 校验。
- 欢迎词为 1 至 20 条；去除首尾空白后每条 1 至 100 个字符；空行忽略、重复项保留第一次出现。
- 应用中文名保持“大口九”，内部包名和容器名继续使用 `daihougou`。

## File Structure

- Modify `src/daihougou/settings.py`: 解析并校验音箱数组、go2rtc 和 RTSP 基础地址；移除单摄像头/单音箱配置。
- Modify `.env.mvp.example`: 展示新的多音箱与 go2rtc 配置，不包含真实凭据。
- Modify `src/daihougou/storage.py`: 定义新 schema、摄像头和逐摄像头规则接口、全局欢迎词及带设备归属的事件。
- Create `src/daihougou/go2rtc.py`: 生产应用使用的短超时码流发现客户端和 RTSP URL 构造函数。
- Modify `src/daihougou/rules.py`: 产生包含摄像头和目标音箱的欢迎动作，按摄像头隔离冷却并实时读取全局欢迎词。
- Modify `src/daihougou/speaker_worker.py`: 用 `SpeakerManager` 管理每个音箱的独立队列、全局认证门控和执行前配置复查。
- Create `src/daihougou/detection_scheduler.py`: 串行加载并调用唯一检测器，公平处理各摄像头的单个在途任务。
- Create `src/daihougou/camera_runtime.py`: 管理单路 FFmpeg、人员状态、规则触发和单路健康快照。
- Modify `src/daihougou/runtime.py`: 编排发现、SQLite 配置、摄像头执行单元、共享检测器和多音箱。
- Modify `src/daihougou/main.py`: 按新配置组装生产对象。
- Modify `src/daihougou/web.py`: 提供手动发现、逐摄像头规则/音箱配置、全局欢迎词和聚合健康接口。
- Modify `src/daihougou/templates/index.html`: 渲染全局配置、摄像头列表、资源提示及带设备列的事件。
- Modify `src/daihougou/static/app.css`: 为密集管理页增加响应式表单、状态和表格样式。
- Rewrite the affected tests under `tests/app/`: 以新接口替换单摄像头测试，并增加多路公平性、路由和故障隔离覆盖。
- Modify `README.md` and `docs/poc-runbook.md`: 更新安装、配置、数据库重置、操作和验收步骤。

---

### Task 1: Parse explicit multi-speaker and go2rtc settings

**Files:**
- Modify: `src/daihougou/settings.py`
- Modify: `.env.mvp.example`
- Modify: `tests/app/test_settings.py`
- Modify: `tests/app/test_compose_mvp.py`

**Interfaces:**
- Produces: `SpeakerConfig(id: str, name: str, did: str)` with `did` excluded from `repr`.
- Produces: `Settings.speakers: tuple[SpeakerConfig, ...]`.
- Produces: `Settings.go2rtc_api_url: str` and `Settings.go2rtc_rtsp_base_url: str`.
- Removes: `Settings.mi_did`, `Settings.stream_url`, `MI_DID`, and `STREAM_URL`.

- [ ] **Step 1: Replace the settings tests with failing multi-speaker tests**

```python
import json
from pathlib import Path

import pytest

from daihougou.settings import Settings, SpeakerConfig


SPEAKERS = [
    {"id": "living_room", "name": "客厅音箱", "did": "123456789"},
    {"id": "bedroom", "name": "卧室音箱", "did": "987654321"},
]
BASE_ENV = {
    "MI_USER": "owner@example.test",
    "MI_PASS": "secret",
    "MI_SPEAKERS_JSON": json.dumps(SPEAKERS, ensure_ascii=False),
}


def test_settings_parse_speakers_and_discovery_defaults() -> None:
    settings = Settings.from_mapping(BASE_ENV)

    assert settings.speakers == (
        SpeakerConfig("living_room", "客厅音箱", "123456789"),
        SpeakerConfig("bedroom", "卧室音箱", "987654321"),
    )
    assert settings.go2rtc_api_url == "http://127.0.0.1:1984"
    assert settings.go2rtc_rtsp_base_url == "rtsp://127.0.0.1:8554"
    assert settings.data_dir == Path("/var/lib/daihougou/data")
    assert "secret" not in repr(settings)
    assert "123456789" not in repr(settings)


def test_settings_do_not_fall_back_to_mi_did() -> None:
    with pytest.raises(ValueError, match="^MI_SPEAKERS_JSON is required$"):
        Settings.from_mapping(
            {"MI_USER": "owner", "MI_PASS": "secret", "MI_DID": "123456789"}
        )


@pytest.mark.parametrize(
    ("speakers", "message"),
    [
        ([], "MI_SPEAKERS_JSON must contain at least one speaker"),
        ([{"id": "Room", "name": "客厅", "did": "1"}], "speaker id is invalid"),
        (
            [
                {"id": "room", "name": "客厅", "did": "1"},
                {"id": "room", "name": "卧室", "did": "2"},
            ],
            "speaker ids must be unique",
        ),
        ([{"id": "room", "name": " ", "did": "1"}], "speaker name is invalid"),
        ([{"id": "room", "name": "客厅", "did": ""}], "speaker did is invalid"),
    ],
)
def test_settings_reject_invalid_speaker_catalog(
    speakers: list[dict[str, str]], message: str
) -> None:
    with pytest.raises(ValueError, match=f"^{message}$"):
        Settings.from_mapping(
            {**BASE_ENV, "MI_SPEAKERS_JSON": json.dumps(speakers, ensure_ascii=False)}
        )
```

Update `tests/app/test_compose_mvp.py` to require `MI_SPEAKERS_JSON=` and both go2rtc URL variables, and to assert `MI_DID=` and `STREAM_URL=` are absent from `.env.mvp.example`.

- [ ] **Step 2: Run the settings and environment tests to verify they fail**

Run: `pytest -q tests/app/test_settings.py tests/app/test_compose_mvp.py`

Expected: FAIL because `SpeakerConfig`, `Settings.speakers`, and the new example variables do not exist.

- [ ] **Step 3: Implement strict speaker catalog parsing and new settings fields**

Use these public shapes in `src/daihougou/settings.py`:

```python
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SPEAKER_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class SpeakerConfig:
    id: str
    name: str
    did: str = field(repr=False)


def _speaker_catalog(raw: str) -> tuple[SpeakerConfig, ...]:
    if not raw.strip():
        raise ValueError("MI_SPEAKERS_JSON is required")
    try:
        payload: Any = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("MI_SPEAKERS_JSON must be valid JSON") from error
    if not isinstance(payload, list) or not payload:
        raise ValueError("MI_SPEAKERS_JSON must contain at least one speaker")

    speakers: list[SpeakerConfig] = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("each speaker must be an object")
        speaker_id = item.get("id")
        name = item.get("name")
        did = item.get("did")
        if not isinstance(speaker_id, str) or not SPEAKER_ID_PATTERN.fullmatch(speaker_id):
            raise ValueError("speaker id is invalid")
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 50:
            raise ValueError("speaker name is invalid")
        if not isinstance(did, str) or not did.strip():
            raise ValueError("speaker did is invalid")
        speakers.append(SpeakerConfig(speaker_id, name.strip(), did.strip()))
    if len({speaker.id for speaker in speakers}) != len(speakers):
        raise ValueError("speaker ids must be unique")
    return tuple(speakers)
```

Make `Settings.from_mapping()` require `MI_USER`, `MI_PASS`, and `MI_SPEAKERS_JSON`; set URL defaults exactly as declared in the Interfaces block; retain existing numeric validation.

Update `.env.mvp.example` to:

```dotenv
MI_USER=
MI_PASS=
MI_SPEAKERS_JSON='[{"id":"living_room","name":"客厅音箱","did":"填写音箱DID"}]'
GO2RTC_API_URL=http://127.0.0.1:1984
GO2RTC_RTSP_BASE_URL=rtsp://127.0.0.1:8554
DATA_DIR=/var/lib/daihougou/data
MODEL=/opt/daihougou/models/person_detection_mediapipe_2023mar.onnx
DETECTION_FPS=1.0
PERSON_THRESHOLD=0.55
LEAVE_SECONDS=10.0
WELCOME_COOLDOWN_SECONDS=60.0
WEB_HOST=SERVER_LAN_IP
WEB_PORT=8080
```

- [ ] **Step 4: Run focused tests and lint**

Run: `pytest -q tests/app/test_settings.py tests/app/test_compose_mvp.py && ruff check src/daihougou/settings.py tests/app/test_settings.py`

Expected: all selected tests pass and Ruff exits 0.

- [ ] **Step 5: Commit settings changes**

```bash
git add src/daihougou/settings.py .env.mvp.example tests/app/test_settings.py tests/app/test_compose_mvp.py
git commit -m "feat: configure multiple speakers"
```

### Task 2: Replace the empty database with the camera-scoped schema

**Files:**
- Modify: `src/daihougou/storage.py`
- Rewrite: `tests/app/test_storage.py`

**Interfaces:**
- Produces: `SCHEMA_VERSION = 2` and `IncompatibleSchemaError`.
- Produces: `CameraConfig(stream_id, speaker_id, first_seen_at, last_seen_at)`.
- Produces: `Storage.sync_cameras(stream_ids, default_speaker_id) -> list[CameraConfig]`.
- Produces: `Storage.list_cameras() -> list[CameraConfig]`.
- Produces: `Storage.camera_rule_enabled(camera_id: str, rule_id: str) -> bool` and
  `Storage.set_camera_rule_enabled(camera_id: str, rule_id: str, enabled: bool) -> None`.
- Produces: `Storage.camera_speaker_id(camera_id: str) -> str` and
  `Storage.set_camera_speaker(camera_id: str, speaker_id: str) -> None`.
- Produces: `Storage.welcome_phrases() -> tuple[str, ...]` and
  `Storage.set_welcome_phrases(lines: Sequence[str]) -> tuple[str, ...]`.
- Extends: `EventRecord` and `StoredEvent` with `camera_id` and `speaker_id`.

- [ ] **Step 1: Write failing schema, discovery sync, configuration, and event tests**

```python
import json
import sqlite3
from pathlib import Path

import pytest

from daihougou.rules import WELCOME_PHRASES, WELCOME_RULE_ID
from daihougou.storage import EventRecord, IncompatibleSchemaError, SCHEMA_VERSION, Storage


def test_new_database_has_v2_schema_and_default_welcome_phrases(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()

    with sqlite3.connect(tmp_path / "app.db") as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION == 2
    assert storage.welcome_phrases() == WELCOME_PHRASES


def test_existing_v1_tables_are_rejected_without_mutation(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE rules(id TEXT PRIMARY KEY, enabled INTEGER)")

    with pytest.raises(IncompatibleSchemaError, match="reset the database"):
        Storage(path).initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'rules'"
        ).fetchone() is not None


def test_sync_creates_disabled_camera_rules_and_preserves_missing_camera(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.sync_cameras(["xiaobai", "xiaobai_25k"], "living_room")
    storage.set_camera_rule_enabled("xiaobai", WELCOME_RULE_ID, True)
    storage.set_camera_speaker("xiaobai", "bedroom")

    storage.sync_cameras(["xiaobai_25k"], "living_room")

    assert [camera.stream_id for camera in storage.list_cameras()] == [
        "xiaobai",
        "xiaobai_25k",
    ]
    assert storage.camera_rule_enabled("xiaobai", WELCOME_RULE_ID) is True
    assert storage.camera_speaker_id("xiaobai") == "bedroom"
    assert storage.camera_rule_enabled("xiaobai_25k", WELCOME_RULE_ID) is False


def test_welcome_phrases_normalize_and_reject_invalid_update(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()

    assert storage.set_welcome_phrases([" 你好 ", "", "欢迎", "你好"]) == (
        "你好",
        "欢迎",
    )
    with pytest.raises(ValueError, match="at least one"):
        storage.set_welcome_phrases(["", "  "])
    assert storage.welcome_phrases() == ("你好", "欢迎")


def test_events_retain_camera_and_safe_speaker_ids(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.record_event(
        EventRecord(
            "speaker_completed",
            True,
            rule_id=WELCOME_RULE_ID,
            camera_id="xiaobai",
            speaker_id="living_room",
            details={"did": "secret", "confidence": 0.8},
        )
    )

    event = storage.recent_events()[0]
    assert (event.camera_id, event.speaker_id) == ("xiaobai", "living_room")
    assert json.loads(event.details_json) == {"confidence": 0.8, "did": "[REDACTED]"}
```

Also retain the existing WAL and 5000-row pruning tests, adapted to the new schema.

- [ ] **Step 2: Run the storage tests to verify they fail**

Run: `pytest -q tests/app/test_storage.py`

Expected: FAIL because schema versioning, camera records, global configuration, and device event fields do not exist.

- [ ] **Step 3: Implement schema version 2 and typed records**

Use these table definitions in one `executescript()` call for a brand-new database:

```sql
CREATE TABLE cameras (
    stream_id TEXT PRIMARY KEY,
    speaker_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);
CREATE TABLE camera_rules (
    camera_id TEXT NOT NULL REFERENCES cameras(stream_id),
    rule_id TEXT NOT NULL,
    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
    updated_at TEXT NOT NULL,
    PRIMARY KEY (camera_id, rule_id)
);
CREATE TABLE rule_configs (
    rule_id TEXT PRIMARY KEY,
    config_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    rule_id TEXT,
    camera_id TEXT,
    speaker_id TEXT,
    success INTEGER NOT NULL CHECK (success IN (0, 1)),
    latency_ms INTEGER,
    details_json TEXT NOT NULL
);
PRAGMA user_version = 2;
```

Before creating tables, query `PRAGMA user_version` and `sqlite_master`. Accept only a completely empty database or version 2 with all expected tables. Raise `IncompatibleSchemaError("incompatible database; reset the database using the runbook")` for old or partial schemas. Enable `PRAGMA foreign_keys = ON`, WAL and busy timeout on every connection.

Normalize welcome phrases with this exact helper:

```python
def normalize_welcome_phrases(lines: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    phrases = tuple(dict.fromkeys(line.strip() for line in lines if line.strip()))
    if not phrases:
        raise ValueError("welcome phrases must contain at least one phrase")
    if len(phrases) > 20:
        raise ValueError("welcome phrases must contain at most 20 phrases")
    if any(len(phrase) > 100 for phrase in phrases):
        raise ValueError("each welcome phrase must contain at most 100 characters")
    return phrases
```

Make `sync_cameras()` sort and deduplicate stream IDs, use a single transaction, insert missing cameras and their disabled welcome rule, and update `last_seen_at` only for names in the successful result. Do not delete missing camera records.

- [ ] **Step 4: Run focused tests and lint**

Run: `pytest -q tests/app/test_storage.py && ruff check src/daihougou/storage.py tests/app/test_storage.py`

Expected: all storage tests pass and Ruff exits 0.

- [ ] **Step 5: Commit the new storage schema**

```bash
git add src/daihougou/storage.py tests/app/test_storage.py
git commit -m "feat: store camera scoped rule settings"
```

### Task 3: Add a production go2rtc discovery client

**Files:**
- Create: `src/daihougou/go2rtc.py`
- Create: `tests/app/test_go2rtc.py`

**Interfaces:**
- Produces: `DiscoveryError` with safe classified messages.
- Produces: `Go2RtcClient(base_url, get_json=None).stream_names() -> tuple[str, ...]`.
- Produces: `rtsp_stream_url(base_url: str, stream_id: str) -> str`.

- [ ] **Step 1: Write failing discovery and URL safety tests**

```python
import pytest

from daihougou.go2rtc import DiscoveryError, Go2RtcClient, rtsp_stream_url


def test_stream_names_are_sorted_and_validated() -> None:
    client = Go2RtcClient(
        "http://127.0.0.1:1984/",
        get_json=lambda _path: {"xiaobai_25k": {}, "xiaobai": {}},
    )
    assert client.stream_names() == ("xiaobai", "xiaobai_25k")


@pytest.mark.parametrize("payload", [[], {"": {}}, {1: {}}])
def test_invalid_stream_payload_is_classified(payload: object) -> None:
    client = Go2RtcClient("http://127.0.0.1:1984", get_json=lambda _path: payload)
    with pytest.raises(DiscoveryError, match="^invalid_stream_response$"):
        client.stream_names()


def test_stream_id_is_url_encoded_as_one_path_segment() -> None:
    assert rtsp_stream_url("rtsp://127.0.0.1:8554/", "room/a b") == (
        "rtsp://127.0.0.1:8554/room%2Fa%20b"
    )
```

- [ ] **Step 2: Run the discovery tests to verify they fail**

Run: `pytest -q tests/app/test_go2rtc.py`

Expected: FAIL because `daihougou.go2rtc` does not exist.

- [ ] **Step 3: Implement the short-timeout client without logging responses**

```python
import json
from collections.abc import Callable
from typing import Any
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
```

Catch network, timeout and JSON failures without embedding the original exception text in the public message.

- [ ] **Step 4: Run focused tests and lint**

Run: `pytest -q tests/app/test_go2rtc.py && ruff check src/daihougou/go2rtc.py tests/app/test_go2rtc.py`

Expected: all discovery tests pass and Ruff exits 0.

- [ ] **Step 5: Commit production discovery support**

```bash
git add src/daihougou/go2rtc.py tests/app/test_go2rtc.py
git commit -m "feat: discover go2rtc camera streams"
```

### Task 4: Make welcome rules camera-scoped and dynamically configured

**Files:**
- Modify: `src/daihougou/rules.py`
- Rewrite: `tests/app/test_rules.py`

**Interfaces:**
- Produces: `SpeechAction(camera_id, rule_id, speaker_id, text, created_monotonic)`.
- Produces: `WelcomeRule(camera_id, state, cooldown_seconds=60, chooser=random.choice)`.
- Consumes from Task 2: `camera_rule_enabled`, `camera_speaker_id`, and `welcome_phrases`.

- [ ] **Step 1: Write failing camera isolation and live configuration tests**

```python
from daihougou.presence import PresenceEvent, PresenceEventKind
from daihougou.rules import WELCOME_RULE_ID, WelcomeRule


class RuleState:
    def __init__(self) -> None:
        self.enabled = {"front": True, "back": False}
        self.speakers = {"front": "living_room", "back": "bedroom"}
        self.phrases = ("第一句",)

    def camera_rule_enabled(self, camera_id: str, rule_id: str) -> bool:
        assert rule_id == WELCOME_RULE_ID
        return self.enabled[camera_id]

    def camera_speaker_id(self, camera_id: str) -> str:
        return self.speakers[camera_id]

    def welcome_phrases(self) -> tuple[str, ...]:
        return self.phrases


def event(at: float) -> PresenceEvent:
    return PresenceEvent(PresenceEventKind.PERSON_ENTERED, at)


def test_action_carries_camera_and_fixed_target_speaker() -> None:
    state = RuleState()
    action = WelcomeRule("front", state, chooser=lambda phrases: phrases[0]).handle(event(10))

    assert action is not None
    assert action.camera_id == "front"
    assert action.rule_id == WELCOME_RULE_ID
    assert action.speaker_id == "living_room"
    assert action.text == "第一句"


def test_rule_reads_latest_global_phrases_for_each_new_action() -> None:
    state = RuleState()
    rule = WelcomeRule("front", state, cooldown_seconds=1, chooser=lambda phrases: phrases[0])

    assert rule.handle(event(1)).text == "第一句"
    state.phrases = ("更新后",)
    assert rule.handle(event(2)).text == "更新后"


def test_disabled_camera_does_not_affect_other_camera() -> None:
    state = RuleState()
    assert WelcomeRule("back", state).handle(event(1)) is None
    assert WelcomeRule("front", state).handle(event(1)) is not None
```

- [ ] **Step 2: Run the rule tests to verify they fail**

Run: `pytest -q tests/app/test_rules.py`

Expected: FAIL because the current rule state and action do not contain a camera or speaker.

- [ ] **Step 3: Implement camera-scoped rule state and actions**

Use this public interface:

```python
class RuleState(Protocol):
    def camera_rule_enabled(self, camera_id: str, rule_id: str) -> bool: ...
    def camera_speaker_id(self, camera_id: str) -> str: ...
    def welcome_phrases(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class SpeechAction:
    camera_id: str
    rule_id: str
    speaker_id: str
    text: str
    created_monotonic: float
```

Store `camera_id` on `WelcomeRule`. On a person-entered event, check only that camera's rule, enforce only that instance's cooldown, read the current phrase tuple, read the current paired speaker, choose a phrase, then create `SpeechAction`. Do not cache the phrase list or speaker ID on rule construction.

- [ ] **Step 4: Run focused tests and lint**

Run: `pytest -q tests/app/test_rules.py && ruff check src/daihougou/rules.py tests/app/test_rules.py`

Expected: all rule tests pass and Ruff exits 0.

- [ ] **Step 5: Commit camera-scoped rules**

```bash
git add src/daihougou/rules.py tests/app/test_rules.py
git commit -m "feat: scope welcome rules by camera"
```

### Task 5: Route speech through one queue per configured speaker

**Files:**
- Modify: `src/daihougou/speaker_worker.py`
- Rewrite: `tests/app/test_speaker_worker.py`

**Interfaces:**
- Produces: `SpeakerManager(speakers: Mapping[str, Speaker], storage, queue_size=4)`.
- Produces: async `start()`, `stop()`, and `join()`; synchronous `submit(action) -> bool`.
- Produces: `auth_status`, `fatal_error`, and `speaker_statuses: dict[str, str]`.
- Consumes from Task 4: camera- and speaker-aware `SpeechAction`.
- Consumes from Task 2: camera rule and pairing lookup plus event recording.

- [ ] **Step 1: Write failing routing, stale action, concurrency, and authentication tests**

```python
import asyncio
import threading

from daihougou.rules import SpeechAction, WELCOME_RULE_ID
from daihougou.speaker import SpeakResult
from daihougou.speaker_worker import SpeakerManager
from daihougou.storage import EventRecord


class FakeStorage:
    def __init__(self) -> None:
        self.enabled = {"front": True, "back": True}
        self.pairings = {"front": "living_room", "back": "bedroom"}
        self.events: list[EventRecord] = []

    def camera_rule_enabled(self, camera_id: str, rule_id: str) -> bool:
        return self.enabled[camera_id] and rule_id == WELCOME_RULE_ID

    def camera_speaker_id(self, camera_id: str) -> str:
        return self.pairings[camera_id]

    def record_event(self, event: EventRecord) -> None:
        self.events.append(event)


class FakeSpeaker:
    def __init__(self, result: SpeakResult | None = None) -> None:
        self.result = result or SpeakResult(True, 1, 0)
        self.spoken: list[str] = []

    def speak(self, text: str) -> SpeakResult:
        self.spoken.append(text)
        return self.result


def action(camera: str, speaker: str, text: str) -> SpeechAction:
    return SpeechAction(camera, WELCOME_RULE_ID, speaker, text, 1.0)


def test_manager_routes_each_camera_to_its_fixed_speaker() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        living = FakeSpeaker()
        bedroom = FakeSpeaker()
        manager = SpeakerManager(
            {"living_room": living, "bedroom": bedroom}, storage
        )
        await manager.start()
        manager.submit(action("front", "living_room", "前门"))
        manager.submit(action("back", "bedroom", "后门"))
        await manager.join()
        await manager.stop()
        assert living.spoken == ["前门"]
        assert bedroom.spoken == ["后门"]

    asyncio.run(scenario())


def test_manager_drops_action_after_camera_pairing_changes() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        living = FakeSpeaker()
        manager = SpeakerManager({"living_room": living}, storage)
        storage.pairings["front"] = "bedroom"
        await manager.start()
        manager.submit(action("front", "living_room", "过期"))
        await manager.join()
        await manager.stop()
        assert living.spoken == []
        assert storage.events[-1].kind == "speaker_skipped_pairing_changed"

    asyncio.run(scenario())


def test_reauthentication_on_one_speaker_gates_all_queues() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        manager = SpeakerManager(
            {
                "living_room": FakeSpeaker(
                    SpeakResult(False, 1, "reauth_required", "reauth_required")
                ),
                "bedroom": FakeSpeaker(),
            },
            storage,
        )
        await manager.start()
        manager.submit(action("front", "living_room", "触发认证"))
        await manager.join()
        assert manager.auth_status == "reauth_required"
        assert manager.submit(action("back", "bedroom", "不得播放")) is False
        await manager.stop()

    asyncio.run(scenario())
```

Add two synchronization tests using `threading.Event`: two actions for one fake speaker must never overlap; two different fake speakers must both enter `speak()` before either is released. Do not assert wall-clock timing.

- [ ] **Step 2: Run speaker manager tests to verify they fail**

Run: `pytest -q tests/app/test_speaker_worker.py`

Expected: FAIL because `SpeakerManager` and per-speaker queues do not exist.

- [ ] **Step 3: Implement bounded per-speaker queues and global auth gating**

Create one `asyncio.Queue[SpeechAction | None]` and one consumer task per mapping entry. `submit()` must:

```python
def submit(self, action: SpeechAction) -> bool:
    if self.auth_status == "reauth_required" or self.fatal_error:
        return False
    queue = self._queues.get(action.speaker_id)
    if queue is None:
        self._record_rejected(action, "speaker_unknown")
        return False
    try:
        queue.put_nowait(action)
        return True
    except asyncio.QueueFull:
        self._record_rejected(action, "speaker_queue_full")
        return False
```

Each consumer must re-read `camera_rule_enabled(action.camera_id, action.rule_id)` and `camera_speaker_id(action.camera_id)` immediately before `asyncio.to_thread(speaker.speak, action.text)`. Record camera ID and safe speaker ID on every queue, skip, auth and completion event. A reauthentication result sets the single manager-wide auth status before the consumer accepts another action. Ordinary failures set only that speaker's status to `degraded` and never reroute.

- [ ] **Step 4: Run focused tests and lint**

Run: `pytest -q tests/app/test_speaker_worker.py tests/speakers/test_direct.py && ruff check src/daihougou/speaker_worker.py tests/app/test_speaker_worker.py`

Expected: all selected tests pass; the existing direct MiService adapter remains unchanged and Ruff exits 0.

- [ ] **Step 5: Commit multi-speaker scheduling**

```bash
git add src/daihougou/speaker_worker.py tests/app/test_speaker_worker.py
git commit -m "feat: route actions across speaker queues"
```

### Task 6: Add the lazy shared detection scheduler

**Files:**
- Create: `src/daihougou/detection_scheduler.py`
- Create: `tests/app/test_detection_scheduler.py`

**Interfaces:**
- Produces: `DetectionScheduler(detector_factory)`.
- Produces: async `start()`, `detect(camera_id, sample) -> PersonDetection`, and `stop()`.
- Produces: `status: str`, `fatal_error: bool`, and `loaded: bool`.
- Guarantees: exactly one detector instance between `start()` and `stop()`, FIFO service across one in-flight request per camera, and no model instance while stopped.

- [ ] **Step 1: Write failing lazy-load, serialization, fairness, and release tests**

```python
import asyncio
from collections import deque

import numpy as np

from daihougou.detection_scheduler import DetectionScheduler
from daihougou.vision.frame_source import FrameSample
from daihougou.vision.person_detector import PersonDetection


def sample(sequence: int) -> FrameSample:
    return FrameSample(sequence, float(sequence), np.zeros((256, 256, 3), dtype=np.uint8))


def test_scheduler_loads_one_detector_and_releases_it_when_stopped() -> None:
    class Detector:
        def detect(self, frame: np.ndarray) -> PersonDetection:
            return PersonDetection(False, 0.1, 1)

    async def scenario() -> None:
        created: list[Detector] = []

        def factory() -> Detector:
            detector = Detector()
            created.append(detector)
            return detector

        scheduler = DetectionScheduler(factory)
        assert scheduler.loaded is False
        await scheduler.start()
        assert scheduler.loaded is True
        await asyncio.gather(
            scheduler.detect("front", sample(1)),
            scheduler.detect("back", sample(2)),
        )
        assert len(created) == 1
        await scheduler.stop()
        assert scheduler.loaded is False

    asyncio.run(scenario())


def test_fifo_queue_prevents_one_camera_from_starving_another() -> None:
    class OrderedDetector:
        def __init__(self) -> None:
            self.sequences: list[int] = []

        def detect(self, frame: np.ndarray) -> PersonDetection:
            self.sequences.append(int(frame[0, 0, 0]))
            return PersonDetection(False, 0.1, 1)

    async def scenario() -> None:
        detector = OrderedDetector()
        scheduler = DetectionScheduler(lambda: detector)
        await scheduler.start()
        frames = [sample(1), sample(2), sample(3)]
        for frame in frames:
            frame.frame[0, 0, 0] = frame.sequence
        front_first = asyncio.create_task(scheduler.detect("front", frames[0]))
        back = asyncio.create_task(scheduler.detect("back", frames[1]))
        await front_first
        front_second = asyncio.create_task(scheduler.detect("front", frames[2]))
        await asyncio.gather(back, front_second)
        await scheduler.stop()
        assert detector.sequences == [1, 2, 3]

    asyncio.run(scenario())
```

Add a detector that blocks on a `threading.Event` and assert its maximum concurrent `detect()` count is 1.

- [ ] **Step 2: Run scheduler tests to verify they fail**

Run: `pytest -q tests/app/test_detection_scheduler.py`

Expected: FAIL because the scheduler module does not exist.

- [ ] **Step 3: Implement one FIFO async consumer around a detector factory**

Use a private job type and queue:

```python
@dataclass(frozen=True)
class _DetectionJob:
    camera_id: str
    sample: FrameSample
    future: asyncio.Future[PersonDetection]
```

`start()` loads the detector with `await asyncio.to_thread(self._detector_factory)` before starting the consumer. `detect()` rejects duplicate in-flight submissions for the same camera, adds the camera ID to a pending set, enqueues one job, and awaits its future. The consumer runs `detector.detect` through `asyncio.to_thread`, completes the future, and removes the ID in `finally`. `stop()` sends a sentinel, awaits the consumer with a bounded timeout, fails remaining futures with `RuntimeError("detector_stopped")`, clears the queue and pending set, and drops the detector reference.

Classify model construction or background consumer termination through `status="degraded"` and `fatal_error=True`; never expose backend exception text in snapshots.

- [ ] **Step 4: Run focused tests and lint**

Run: `pytest -q tests/app/test_detection_scheduler.py && ruff check src/daihougou/detection_scheduler.py tests/app/test_detection_scheduler.py`

Expected: all scheduler tests pass and Ruff exits 0.

- [ ] **Step 5: Commit the shared detector scheduler**

```bash
git add src/daihougou/detection_scheduler.py tests/app/test_detection_scheduler.py
git commit -m "feat: share one detector across cameras"
```

### Task 7: Isolate each camera's stream, presence, and rule state

**Files:**
- Create: `src/daihougou/camera_runtime.py`
- Create: `tests/app/test_camera_runtime.py`
- Remove single-camera behavior from: `tests/app/test_runtime.py`

**Interfaces:**
- Produces: `CameraSnapshot(stream_id, stream, detector, presence, last_sequence, last_confidence, last_detection_latency_ms, last_error)`.
- Produces: `CameraRuntime(stream_id, frame_source, scheduler, presence, welcome_rule, speaker_manager, storage, clock=time.monotonic)`.
- Produces: async `start()` and `stop()`, plus synchronous `snapshot()`.
- Consumes: Task 4 `WelcomeRule`, Task 5 `SpeakerManager.submit`, Task 6 `DetectionScheduler.detect`.

- [ ] **Step 1: Move and rewrite the single-camera runtime tests against `CameraRuntime`**

Define these focused test doubles at the top of `tests/app/test_camera_runtime.py`:

```python
class FakeScheduler:
    def __init__(self, observations: list[bool]) -> None:
        self._observations = deque(observations)

    async def detect(self, camera_id: str, sample: FrameSample) -> PersonDetection:
        present = self._observations.popleft()
        return PersonDetection(present, 0.8 if present else 0.1, 4)


class FakeSpeakerManager:
    def __init__(self) -> None:
        self.actions: list[SpeechAction] = []

    def submit(self, action: SpeechAction) -> bool:
        self.actions.append(action)
        return True


def configured_storage(
    tmp_path: Path,
    camera_id: str,
    speaker_id: str,
    *,
    enabled: bool,
) -> Storage:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.sync_cameras([camera_id], speaker_id)
    storage.set_camera_rule_enabled(camera_id, WELCOME_RULE_ID, enabled)
    return storage
```

Retain a `FakeFrameSource` equivalent to the current one in `tests/app/test_runtime.py`, but move it into
`tests/app/test_camera_runtime.py` because the frame source is now owned by `CameraRuntime`.

```python
def test_camera_runtime_processes_entry_and_tags_events(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = configured_storage(tmp_path, "front", "living_room", enabled=True)
        source = FakeFrameSource([0, 1, 2, 3, 4])
        scheduler = FakeScheduler([False, False, False, True, True])
        speaker = FakeSpeakerManager()
        camera = CameraRuntime(
            "front",
            source,
            scheduler,
            PresenceTracker(),
            WelcomeRule("front", storage, chooser=lambda phrases: phrases[0]),
            speaker,
            storage,
        )

        await camera.start()
        await wait_for_sequence(camera, 5)
        snapshot = camera.snapshot()
        await camera.stop()

        assert source.started is True
        assert source.stopped is True
        assert snapshot.stream == "ready"
        assert snapshot.detector == "ready"
        assert snapshot.presence == "present"
        assert speaker.actions[0].camera_id == "front"
        assert all(
            event.camera_id == "front"
            for event in storage.recent_events()
            if event.kind == "presence_changed"
        )

    asyncio.run(scenario())
```

Port these existing regression cases from `tests/app/test_runtime.py` with `camera_id="front"` assertions:

- inference failure invalidates presence and records only `detector_failed`;
- 30 seconds without a frame records `camera_no_frames` only once;
- a frame gap cannot create a false leave and re-entry;
- `stop()` always stops the owned frame source;
- action submission failure does not crash the camera loop.

Add a two-camera test with separate `PresenceTracker` and `WelcomeRule` instances: observations that enter on `front` must not change `back` or consume its cooldown.

- [ ] **Step 2: Run camera runtime tests to verify they fail**

Run: `pytest -q tests/app/test_camera_runtime.py`

Expected: FAIL because `CameraRuntime` and `CameraSnapshot` do not exist.

- [ ] **Step 3: Extract the existing detection loop into a camera-owned module**

Use this snapshot:

```python
@dataclass(frozen=True)
class CameraSnapshot:
    stream_id: str
    stream: str
    detector: str
    presence: str
    last_sequence: int
    last_confidence: float | None
    last_detection_latency_ms: int | None
    last_error: str
```

The loop retains the current 2-second `wait_for_frame` and 30-second outage classification. Replace direct detector calls with `await scheduler.detect(stream_id, sample)`. Tag `presence_changed`, `camera_no_frames`, and `detector_failed` events with `camera_id`. Submit the action returned by that camera's `WelcomeRule` without waiting for speech.

On frame timeout call `presence.invalidate()`; do not convert missing data into an absent observation. Start from a new `PresenceTracker` every time the camera runtime is created so re-enabling performs startup calibration.

- [ ] **Step 4: Run focused tests and lint**

Run: `pytest -q tests/app/test_camera_runtime.py tests/app/test_presence.py && ruff check src/daihougou/camera_runtime.py tests/app/test_camera_runtime.py`

Expected: all selected tests pass and Ruff exits 0.

- [ ] **Step 5: Commit per-camera runtime isolation**

```bash
git add src/daihougou/camera_runtime.py tests/app/test_camera_runtime.py tests/app/test_runtime.py
git commit -m "feat: isolate camera detection state"
```

### Task 8: Rebuild `Runtime` as the device orchestrator

**Files:**
- Rewrite: `src/daihougou/runtime.py`
- Rewrite: `tests/app/test_runtime.py`

**Interfaces:**
- Produces: `CameraView` combining persisted settings, current availability, `CameraSnapshot`, and latest trigger.
- Produces: `RuntimeSnapshot` with aggregate states, counts, cameras, safe speaker options, recent events, and `resource_warning`.
- Produces: async `Runtime.start()`, `stop()`, `refresh_cameras()`, `set_rule_enabled(camera_id, enabled)`, and `set_camera_speaker(camera_id, speaker_id)`.
- Produces: `Runtime.welcome_phrases()` and `set_welcome_phrases(lines)`.
- Consumes: Tasks 1–7 interfaces.

- [ ] **Step 1: Write failing startup discovery, manual-only refresh, and lifecycle tests**

Define a reusable harness in `tests/app/test_runtime.py`; it must construct real `Storage` and fake only
external/runtime-heavy adapters:

```python
class FakeDiscovery:
    def __init__(self, results: list[tuple[str, ...] | Exception]) -> None:
        self._results = deque(results)
        self.calls = 0

    def stream_names(self) -> tuple[str, ...]:
        self.calls += 1
        result = self._results.popleft()
        if isinstance(result, Exception):
            raise result
        return result


class FakeSource:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def wait_for_frame(self, after_sequence: int, timeout: float) -> None:
        time.sleep(0.005)
        return None

    def stop(self) -> None:
        self.stopped = True


def make_runtime(
    tmp_path: Path,
    discoveries: list[tuple[str, ...] | Exception],
) -> tuple[Runtime, FakeDiscovery, dict[str, FakeSource], FakeDetectionScheduler]:
    storage = Storage(tmp_path / "app.db")
    discovery = FakeDiscovery(discoveries)
    sources: dict[str, FakeSource] = {}
    scheduler = FakeDetectionScheduler()
    speakers = (SpeakerConfig("living_room", "客厅音箱", "secret-did"),)
    runtime = Runtime(
        storage=storage,
        discovery=discovery,
        rtsp_base_url="rtsp://127.0.0.1:8554",
        speakers=speakers,
        speaker_manager=FakeSpeakerManager(storage),
        scheduler=scheduler,
        frame_source_factory=lambda url: sources.setdefault(
            unquote(url.rsplit("/", 1)[-1]), FakeSource()
        ),
        leave_seconds=10,
        welcome_cooldown_seconds=60,
    )
    return runtime, discovery, sources, scheduler
```

Use these exact lightweight implementations in the same test file; they must not start threads or call
MiService:

```python
class FakeDetectionScheduler:
    def __init__(self) -> None:
        self.loaded = False
        self.status = "stopped"
        self.fatal_error = False

    async def start(self) -> None:
        self.loaded = True
        self.status = "ready"

    async def stop(self) -> None:
        self.loaded = False
        self.status = "stopped"

    async def detect(self, camera_id: str, sample: FrameSample) -> PersonDetection:
        return PersonDetection(False, 0.1, 1)


class FakeSpeakerManager:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage
        self.auth_status = "unknown"
        self.fatal_error = False
        self.speaker_statuses = {"living_room": "unknown"}

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    def submit(self, action: SpeechAction) -> bool:
        return True
```

```python
def test_start_discovers_once_and_new_cameras_stay_idle(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime, discovery, sources, scheduler = make_runtime(
            tmp_path, discoveries=[("front", "back")]
        )
        await runtime.start()
        await asyncio.sleep(0.05)
        snapshot = runtime.snapshot()

        assert discovery.calls == 1
        assert snapshot.saved_camera_count == 2
        assert snapshot.enabled_camera_count == 0
        assert snapshot.ready_camera_count == 0
        assert sources == {}
        assert scheduler.loaded is False
        assert snapshot.overall == "ready"
        await runtime.stop()

    asyncio.run(scenario())


def test_enable_starts_only_target_and_last_disable_releases_detector(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime, _discovery, sources, scheduler = make_runtime(
            tmp_path, discoveries=[("front", "back")]
        )
        await runtime.start()
        await runtime.set_rule_enabled("front", True)
        assert set(sources) == {"front"}
        assert sources["front"].started is True
        assert scheduler.loaded is True

        await runtime.set_rule_enabled("front", False)
        assert sources["front"].stopped is True
        assert scheduler.loaded is False
        await runtime.stop()

    asyncio.run(scenario())


def test_refresh_is_manual_and_missing_stream_keeps_configuration(tmp_path: Path) -> None:
    async def scenario() -> None:
        runtime, discovery, sources, _scheduler = make_runtime(
            tmp_path, discoveries=[("front", "back"), ("back",)]
        )
        await runtime.start()
        await runtime.set_rule_enabled("front", True)
        await asyncio.sleep(0.05)
        assert discovery.calls == 1

        await runtime.refresh_cameras()
        snapshot = runtime.snapshot()
        front = next(camera for camera in snapshot.cameras if camera.stream_id == "front")
        assert discovery.calls == 2
        assert front.available is False
        assert front.rule_enabled is True
        assert sources["front"].stopped is True
        await runtime.stop()

    asyncio.run(scenario())
```

Add tests for:

- initial discovery failure starts previously saved enabled cameras with availability `unknown`;
- later refresh failure preserves the last availability and running camera set;
- a removed stream reappears and resumes without losing rule or speaker pairing;
- unknown speaker pairing blocks the update and never changes storage;
- 4 enabled cameras set `resource_warning=True`, while 3 do not;
- one camera degradation does not mark another camera degraded;
- shared scheduler or speaker manager fatal task makes aggregate status `unhealthy`;
- no enabled cameras is healthy idle state.

- [ ] **Step 2: Run runtime tests to verify they fail**

Run: `pytest -q tests/app/test_runtime.py`

Expected: FAIL because the old `Runtime` constructor and snapshot are single-camera.

- [ ] **Step 3: Implement orchestration and deterministic reconciliation**

Use dependency factories rather than constructing concrete implementations inside `Runtime`:

```python
FrameSourceFactory = Callable[[str], FrameSource]


class Runtime:
    def __init__(
        self,
        storage: Storage,
        discovery: Go2RtcClient,
        rtsp_base_url: str,
        speakers: tuple[SpeakerConfig, ...],
        speaker_manager: SpeakerManager,
        scheduler: DetectionScheduler,
        frame_source_factory: FrameSourceFactory,
        leave_seconds: float,
        welcome_cooldown_seconds: float,
    ) -> None:
        self._storage = storage
        self._discovery = discovery
        self._rtsp_base_url = rtsp_base_url
        self._speakers = speakers
        self._speaker_ids = frozenset(speaker.id for speaker in speakers)
        self._speaker_manager = speaker_manager
        self._scheduler = scheduler
        self._frame_source_factory = frame_source_factory
        self._leave_seconds = leave_seconds
        self._welcome_cooldown_seconds = welcome_cooldown_seconds
        self._available_stream_ids: frozenset[str] | None = None
        self._camera_runtimes: dict[str, CameraRuntime] = {}
        self._app_status = "stopped"
        self._database_status = "starting"
        self._discovery_status = "unknown"
        self._last_error = ""
```

`start()` initializes storage, starts the speaker manager, performs exactly one `refresh_cameras()`, catches `DiscoveryError` into safe discovery state, then reconciles saved enabled cameras. `refresh_cameras()` calls `discovery.stream_names()` through `asyncio.to_thread`; only a successful result calls `storage.sync_cameras()` and replaces the in-memory availability set.

The private `_reconcile()` computes desired stream IDs as saved enabled cameras that are in the last successful availability set. When no successful discovery exists, saved enabled cameras are desired so startup can try their RTSP paths. It must execute transitions in this order:

1. stop camera runtimes no longer desired;
2. if desired is empty, stop the shared scheduler;
3. if desired is non-empty and scheduler is stopped, start it once;
4. create and start newly desired camera runtimes.

Construct RTSP URLs only with `rtsp_stream_url`. Each newly created camera gets a new `PresenceTracker`, `WelcomeRule`, and `FfmpegFrameSource`. Updating the paired speaker does not restart FFmpeg; queued actions are invalidated by Task 5's execution-time check.

Use safe speaker options in snapshots:

```python
@dataclass(frozen=True)
class SpeakerOption:
    id: str
    name: str


@dataclass(frozen=True)
class CameraView:
    stream_id: str
    speaker_id: str
    speaker: str
    available: bool | None
    rule_enabled: bool
    stream: str
    detector: str
    presence: str
    last_confidence: float | None
    last_detection_latency_ms: int | None
    last_error: str
    latest_trigger: StoredEvent | None

    def health_dict(self) -> dict[str, str | bool | float | int | None]:
        return {
            "stream_id": self.stream_id,
            "available": self.available,
            "rule_enabled": self.rule_enabled,
            "stream": self.stream,
            "detector": self.detector,
            "presence": self.presence,
            "last_confidence": self.last_confidence,
            "last_detection_latency_ms": self.last_detection_latency_ms,
            "last_error": self.last_error,
        }


@dataclass(frozen=True)
class RuntimeSnapshot:
    app: str
    database: str
    discovery: str
    detector: str
    speaker_auth: str
    speakers: tuple[SpeakerOption, ...]
    cameras: tuple[CameraView, ...]
    events: tuple[StoredEvent, ...]
    last_error: str

    @property
    def saved_camera_count(self) -> int:
        return len(self.cameras)

    @property
    def discovered_camera_count(self) -> int:
        return sum(camera.available is True for camera in self.cameras)

    @property
    def enabled_camera_count(self) -> int:
        return sum(camera.rule_enabled for camera in self.cameras)

    @property
    def ready_camera_count(self) -> int:
        return sum(
            camera.rule_enabled
            and camera.stream == "ready"
            and camera.detector == "ready"
            for camera in self.cameras
        )

    @property
    def resource_warning(self) -> bool:
        return self.enabled_camera_count >= 4

    @property
    def overall(self) -> str:
        if self.app == "unhealthy" or self.database == "unhealthy":
            return "unhealthy"
        enabled = tuple(camera for camera in self.cameras if camera.rule_enabled)
        if self.discovery == "degraded" or any(
            camera.stream == "degraded" or camera.detector == "degraded"
            for camera in enabled
        ):
            return "degraded"
        return "ready"
```

Never include `SpeakerConfig.did` in a snapshot or event.

- [ ] **Step 4: Run focused tests and lint**

Run: `pytest -q tests/app/test_runtime.py tests/app/test_camera_runtime.py tests/app/test_detection_scheduler.py && ruff check src/daihougou/runtime.py tests/app/test_runtime.py`

Expected: all selected tests pass and Ruff exits 0.

- [ ] **Step 5: Commit device orchestration**

```bash
git add src/daihougou/runtime.py tests/app/test_runtime.py
git commit -m "feat: orchestrate multiple cameras on demand"
```

### Task 9: Replace the management page with camera and global rule controls

**Files:**
- Rewrite: `src/daihougou/web.py`
- Rewrite: `src/daihougou/templates/index.html`
- Modify: `src/daihougou/static/app.css`
- Rewrite: `tests/app/test_web.py`

**Interfaces:**
- Consumes from Task 8: snapshots and runtime command methods.
- Produces form routes: `POST /commands/refresh-cameras`, `/commands/camera-rule`, `/commands/camera-speaker`, and `/commands/welcome-phrases`.
- Keeps: `GET /`, `GET /healthz`, same-origin enforcement and double-submit CSRF token.

- [ ] **Step 1: Write failing page and command tests**

Define `FakeRuntime` in `tests/app/test_web.py` with concrete in-memory command tracking:

```python
class FakeRuntime:
    def __init__(
        self,
        camera_count: int = 1,
        camera_ids: tuple[str, ...] | None = None,
    ) -> None:
        self.refresh_calls = 0
        self.rule_updates: list[tuple[str, bool]] = []
        self.speaker_updates: list[tuple[str, str]] = []
        self.phrase_updates: list[list[str]] = []
        self._phrases = ("你好呀，欢迎回来。",)
        self._snapshot = snapshot_fixture(camera_count, camera_ids)

    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def refresh_cameras(self) -> None:
        self.refresh_calls += 1

    async def set_rule_enabled(self, camera_id: str, enabled: bool) -> None:
        if camera_id not in {camera.stream_id for camera in self._snapshot.cameras}:
            raise KeyError(camera_id)
        self.rule_updates.append((camera_id, enabled))

    async def set_camera_speaker(self, camera_id: str, speaker_id: str) -> None:
        self.speaker_updates.append((camera_id, speaker_id))

    def welcome_phrases(self) -> tuple[str, ...]:
        return self._phrases

    def set_welcome_phrases(self, lines: list[str]) -> tuple[str, ...]:
        normalized = normalize_welcome_phrases(lines)
        self.phrase_updates.append(lines)
        self._phrases = normalized
        return normalized

    def snapshot(self) -> RuntimeSnapshot:
        return self._snapshot
```

Implement `snapshot_fixture(camera_count)` in the same test file using the Task 8 dataclasses. Its first
two stream IDs are `front` and `back`; additional IDs are `camera_3`, `camera_4`, and upward. It returns
only `SpeakerOption("living_room", "客厅音箱")`, never a DID.

```python
def snapshot_fixture(
    camera_count: int,
    camera_ids: tuple[str, ...] | None = None,
) -> RuntimeSnapshot:
    names = list(camera_ids) if camera_ids is not None else ["front", "back"] + [
        f"camera_{number}" for number in range(3, camera_count + 1)
    ]
    cameras = tuple(
        CameraView(
            stream_id=name,
            speaker_id="living_room",
            speaker="ready",
            available=True,
            rule_enabled=True,
            stream="ready",
            detector="ready",
            presence="absent",
            last_confidence=0.1,
            last_detection_latency_ms=4,
            last_error="",
            latest_trigger=None,
        )
        for name in names[:camera_count]
    )
    return RuntimeSnapshot(
        app="running",
        database="ready",
        discovery="ready",
        detector="ready" if cameras else "stopped",
        speaker_auth="ready",
        speakers=(SpeakerOption("living_room", "客厅音箱"),),
        cameras=cameras,
        events=(),
        last_error="",
    )
```

```python
def test_home_lists_cameras_global_phrases_and_safe_speakers() -> None:
    runtime = FakeRuntime(camera_count=2)
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "大口九" in response.text
    assert "front" in response.text
    assert "back" in response.text
    assert "全局规则配置" in response.text
    assert "你好呀，欢迎回来。" in response.text
    assert "客厅音箱" in response.text
    assert "123456789" not in response.text


def test_manual_refresh_command_calls_runtime_once() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            "/commands/refresh-cameras",
            data={"csrf_token": "fixed-token"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert runtime.refresh_calls == 1


def test_rule_command_uses_hidden_camera_id_not_url_path() -> None:
    runtime = FakeRuntime(camera_count=1, camera_ids=("room/a b",))
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            "/commands/camera-rule",
            data={
                "csrf_token": "fixed-token",
                "camera_id": "room/a b",
                "enabled": "true",
            },
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )
    assert response.status_code == 303
    assert runtime.rule_updates == [("room/a b", True)]


def test_invalid_welcome_phrases_render_error_without_writing() -> None:
    runtime = FakeRuntime()
    with TestClient(create_app(runtime, csrf_token="fixed-token")) as client:
        client.get("/")
        response = client.post(
            "/commands/welcome-phrases",
            data={"csrf_token": "fixed-token", "phrases": "\n\n"},
            headers={"Origin": "http://testserver"},
        )
    assert response.status_code == 422
    assert "至少填写一条欢迎词" in response.text
    assert runtime.phrase_updates == []
```

Add tests that all four commands reject missing CSRF and cross-origin requests; unknown cameras and speakers return 404; camera rule and speaker updates redirect after success; 4 enabled cameras show the resource message; `/healthz` contains counts and camera states but excludes welcome phrases and DID.

- [ ] **Step 2: Run web tests to verify they fail**

Run: `pytest -q tests/app/test_web.py`

Expected: FAIL because `create_app` still expects separate storage and exposes only the old rule route.

- [ ] **Step 3: Implement shared request validation and command routes**

Change the constructor to `create_app(runtime: ManagedRuntime, csrf_token: str | None = None)`. Define one private validator used by every command:

```python
def _verify_write_request(request: Request, form_token: str, expected_token: str) -> None:
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    cookie = request.cookies.get("daihougou_csrf", "")
    if not origin or urlsplit(origin).netloc != host:
        raise HTTPException(status_code=403, detail="cross_origin_request")
    if not cookie or not secrets.compare_digest(cookie, expected_token):
        raise HTTPException(status_code=403, detail="invalid_csrf")
    if not form_token or not secrets.compare_digest(form_token, expected_token):
        raise HTTPException(status_code=403, detail="invalid_csrf")
```

Render all page context from `runtime.snapshot()` and `runtime.welcome_phrases()`. For invalid phrases, render the same template with the submitted text and a Chinese field error at status 422. For valid writes, call the runtime method and return `RedirectResponse("/", status_code=303)`.

Return `/healthz` as:

```python
{
    "status": snapshot.overall,
    "app": snapshot.app,
    "database": snapshot.database,
    "discovery": snapshot.discovery,
    "detector": snapshot.detector,
    "speaker_auth": snapshot.speaker_auth,
    "saved_camera_count": snapshot.saved_camera_count,
    "discovered_camera_count": snapshot.discovered_camera_count,
    "enabled_camera_count": snapshot.enabled_camera_count,
    "ready_camera_count": snapshot.ready_camera_count,
    "cameras": [camera.health_dict() for camera in snapshot.cameras],
}
```

- [ ] **Step 4: Build the compact responsive single-page UI**

The template must contain these unframed sections in order:

1. topbar with application name and aggregate status;
2. status summary grid;
3. global welcome phrase `<textarea rows="6">` and save button;
4. camera section header with refresh button and conditional 4-camera resource notice;
5. one `.camera-row` per camera with stable status dimensions, switch form, speaker `<select>`, and latest result;
6. recent event table with time, camera, speaker, event, result and latency.

Use text buttons only for “保存欢迎词” and “刷新摄像头”; keep the existing switch for the binary rule state. Do not put sections inside cards or use a marketing hero. At `max-width: 760px`, make each camera row a single-column layout and let the event table scroll horizontally. Preserve 6px or smaller corner radii and `letter-spacing: 0`.

- [ ] **Step 5: Run focused web tests, lint, and HTML safety assertions**

Run: `pytest -q tests/app/test_web.py && ruff check src/daihougou/web.py tests/app/test_web.py && rg -n "MI_DID|did|password|token" src/daihougou/templates/index.html`

Expected: tests and Ruff pass; the final `rg` command returns only the CSRF field name and no device DID or credentials.

- [ ] **Step 6: Commit the management page**

```bash
git add src/daihougou/web.py src/daihougou/templates/index.html src/daihougou/static/app.css tests/app/test_web.py
git commit -m "feat: manage cameras and welcome phrases"
```

### Task 10: Wire production dependencies and prove the multi-camera pipeline

**Files:**
- Rewrite: `src/daihougou/main.py`
- Create: `tests/app/test_main.py`
- Rewrite: `tests/app/test_generated_video_pipeline.py`
- Modify: `tests/app/test_mvp_container.py`
- Modify: `tests/app/test_mvp_dockerfile.py`

**Interfaces:**
- Consumes all production interfaces from Tasks 1–9.
- Produces: `create_production_app(settings=None)` with no model load, FFmpeg process, network discovery, or MiService call until FastAPI lifespan starts.

- [ ] **Step 1: Write failing production composition tests**

```python
def test_production_app_builds_one_detector_factory_and_one_speaker_per_config(
    tmp_path: Path, monkeypatch
) -> None:
    settings = Settings.from_mapping(
        {
            "MI_USER": "owner",
            "MI_PASS": "secret",
            "MI_SPEAKERS_JSON": json.dumps(
                [
                    {"id": "living_room", "name": "客厅", "did": "1"},
                    {"id": "bedroom", "name": "卧室", "did": "2"},
                ]
            ),
            "DATA_DIR": str(tmp_path),
        }
    )
    created_dids: list[str] = []
    monkeypatch.setattr(
        main_module,
        "DirectSpeaker",
        lambda user, password, did: created_dids.append(did) or FakeSpeaker(),
    )

    app = create_production_app(settings)

    assert app.title == "大口九"
    assert created_dids == ["1", "2"]
```

Mock `Go2RtcClient`, `PersonDetector`, and `FfmpegFrameSource` in a lifespan test and assert: startup discovery occurs once; with all rules disabled no detector or frame source is constructed; enabling one camera constructs exactly one detector and one frame source.

- [ ] **Step 2: Run composition tests to verify they fail**

Run: `pytest -q tests/app/test_main.py tests/app/test_mvp_container.py tests/app/test_mvp_dockerfile.py`

Expected: FAIL because production wiring still constructs one `DirectSpeaker`, one `Runtime`, one detector, and one stream source eagerly.

- [ ] **Step 3: Wire the production object graph**

Use this composition in `create_production_app()`:

```python
storage = Storage(resolved.data_dir / "daihougou.db")
speaker_manager = SpeakerManager(
    {
        speaker.id: DirectSpeaker(
            resolved.mi_user,
            resolved.mi_pass,
            speaker.did,
        )
        for speaker in resolved.speakers
    },
    storage,
)
scheduler = DetectionScheduler(
    lambda: PersonDetector(resolved.model, resolved.person_threshold)
)
runtime = Runtime(
    storage=storage,
    discovery=Go2RtcClient(resolved.go2rtc_api_url),
    rtsp_base_url=resolved.go2rtc_rtsp_base_url,
    speakers=resolved.speakers,
    speaker_manager=speaker_manager,
    scheduler=scheduler,
    frame_source_factory=lambda url: FfmpegFrameSource(url, resolved.detection_fps),
    leave_seconds=resolved.leave_seconds,
    welcome_cooldown_seconds=resolved.welcome_cooldown_seconds,
)
return create_app(runtime)
```

Do not construct `PersonDetector` outside its factory. Keep `uvicorn` at one worker.

- [ ] **Step 4: Replace the generated-video test with two camera and two speaker routing**

Generate two six-frame FFmpeg streams: both start with three black frames; `front` becomes white for the last three frames and routes to `living_room`; `back` becomes white later and routes to `bedroom`. Use one `BrightnessDetector` instance through the real `DetectionScheduler`, two real `CameraRuntime` instances through `Runtime`, and fake speakers through `SpeakerManager`.

Assert:

```python
assert living_room.spoken == ["你好呀，欢迎回来。"]
assert bedroom.spoken == ["你好呀，欢迎回来。"]
assert detector_factory_calls == 1
assert {
    (event.camera_id, event.speaker_id)
    for event in storage.recent_events()
    if event.kind == "speaker_completed"
} == {("front", "living_room"), ("back", "bedroom")}
```

Then disable both rules through `Runtime.set_rule_enabled` and assert both generated frame sources stopped and `scheduler.loaded is False`.

- [ ] **Step 5: Run application and generated pipeline tests**

Run: `pytest -q tests/app`

Expected: all application tests pass; generated-video tests skip only when host FFmpeg is unavailable.

- [ ] **Step 6: Run lint and commit production wiring**

Run: `ruff check src tests/app`

Expected: Ruff exits 0.

```bash
git add src/daihougou/main.py tests/app/test_main.py tests/app/test_generated_video_pipeline.py tests/app/test_mvp_container.py tests/app/test_mvp_dockerfile.py
git commit -m "feat: assemble multi-camera runtime"
```

### Task 11: Update Chinese installation and operations documentation

**Files:**
- Modify: `README.md`
- Modify: `docs/poc-runbook.md`

**Interfaces:**
- Documents the exact environment and UI behavior implemented in Tasks 1–10.
- Preserves all previously validated PoC troubleshooting, including camera power, TCP transport, container working-directory tests, DID lookup, and token persistence.

- [ ] **Step 1: Update README configuration and startup guidance**

Replace the single `xiaobai` and `MI_DID` application configuration with:

```dotenv
MI_USER='小米账号'
MI_PASS='小米账号密码'
MI_SPEAKERS_JSON='[{"id":"living_room","name":"客厅音箱","did":"音箱数值DID"},{"id":"bedroom","name":"卧室音箱","did":"另一个音箱数值DID"}]'
GO2RTC_API_URL=http://127.0.0.1:1984
GO2RTC_RTSP_BASE_URL=rtsp://127.0.0.1:8554
WEB_HOST=服务器的局域网IPv4地址
WEB_PORT=8080
```

Explain that every configured `go2rtc` stream appears after startup or “刷新摄像头”, new rules are disabled, the first speaker is the default, camera names come from `go2rtc`, global welcome phrases are edited one per line, and 4 enabled cameras only trigger a resource warning.

- [ ] **Step 2: Add the pre-upgrade database reset and rollback procedure to the runbook**

Document the already executed recoverable operation with the exact safe sequence:

```bash
docker compose -f compose.poc.yaml stop app
backup_dir="deploy/app/state/backup-before-multicamera-$(date +%Y%m%d-%H%M%S)"
sudo mkdir -p "$backup_dir"
for file in daihougou.db daihougou.db-wal daihougou.db-shm; do
  if sudo test -e "deploy/app/state/$file"; then
    sudo mv "deploy/app/state/$file" "$backup_dir/$file"
  fi
done
sudo ls -la "$backup_dir" deploy/app/state
```

State that the old app must remain stopped until the new image is deployed; the new app recreates `deploy/app/state/daihougou.db`; rollback requires moving the new DB aside, restoring the old code and the matching old DB backup together.

- [ ] **Step 3: Add multi-camera and multi-speaker acceptance steps**

Add a runbook section that verifies:

1. at least two named streams appear after manual refresh;
2. both rules start disabled and no FFmpeg detection process runs;
3. each camera is paired with a different configured speaker;
4. entering each view plays only the paired room's speaker;
5. editing welcome phrases affects the next trigger without restart;
6. deleting or powering off one stream does not break the other;
7. removing a paired speaker ID requires explicit re-selection and never falls back;
8. enabling a fourth camera shows the resource warning without blocking the change.

- [ ] **Step 4: Validate documentation consistency and sensitive values**

Run:

```bash
git diff --check
rg -n 'STREAM_URL=|MVP 只要求配置一条|只接受一个摄像头' README.md docs/poc-runbook.md
rg -n -C 2 'MI_DID=' README.md docs/poc-runbook.md
rg -n 'MI_SPEAKERS_JSON|刷新摄像头|欢迎词|backup-before-multicamera' README.md docs/poc-runbook.md
```

Expected: `git diff --check` exits 0; the obsolete-pattern scan returns no matches; every `MI_DID=` match
is explicitly part of `.env.poc` or a one-shot PoC command rather than `.env.mvp`; the new-pattern scan
finds installation and operations guidance in both documents.

- [ ] **Step 5: Commit documentation**

```bash
git add README.md docs/poc-runbook.md
git commit -m "docs: explain multi-camera deployment"
```

### Task 12: Run final regression, container, security, and visual verification

**Files:**
- Modify only if verification exposes a defect: files owned by Tasks 1–11 and their matching tests.

**Interfaces:**
- Verifies the complete spec without real camera or Xiaomi account access during automated tests.

- [ ] **Step 1: Run the full local test and lint suites**

Run:

```bash
pytest -q
ruff check src tests
git diff --check
```

Expected: all tests pass, with only the existing host-FFmpeg conditional skips; Ruff and diff checks exit 0.

- [ ] **Step 2: Build the production image and run its self-contained tests**

Run:

```bash
docker compose -f compose.poc.yaml build app
docker run --rm --entrypoint pytest daihougou-mvp:local -q
```

Expected: image build exits 0 and every container test passes, including generated FFmpeg pipelines.

- [ ] **Step 3: Validate Compose and scan tracked output for secrets**

Run:

```bash
docker compose -f compose.poc.yaml config --quiet
git grep -n -E '(MI_PASS=.+|"did"[[:space:]]*:[[:space:]]*"[0-9]{6,}|xiaomi://)' -- ':!docs/superpowers' ':!.env*.example'
```

Expected: Compose validation exits 0; the tracked-secret scan returns no matches.

- [ ] **Step 4: Start an isolated application container and inspect desktop and mobile UI**

Do not read or modify the user's `.env.mvp`. Start `go2rtc`, then run the built application image as a
separate `daihougou-ui-check` container with fake account values, a fake speaker DID, an isolated temporary
state directory and port 18081. All newly discovered rules remain disabled, so MiService is never called.
Use the in-app browser at desktop 1440×900 and mobile 390×844 to verify:

- title and H1 are “大口九”;
- global welcome phrase input, refresh command, camera rows and event columns render;
- switches, selects and buttons do not resize or overlap;
- 4-camera resource text wraps inside its section;
- no DID appears in page HTML or `/healthz`;
- discovery failure renders a classified state without breaking page navigation.

Run:

```bash
docker compose -f compose.poc.yaml up -d go2rtc
ui_state_dir=$(mktemp -d)
docker run --rm --detach --name daihougou-ui-check --network host \
  --env MI_USER=fake-ui-user \
  --env MI_PASS=fake-ui-password \
  --env 'MI_SPEAKERS_JSON=[{"id":"living_room","name":"客厅音箱","did":"fake-ui-did"}]' \
  --env DATA_DIR=/var/lib/daihougou/data \
  --env WEB_HOST=127.0.0.1 \
  --env WEB_PORT=18081 \
  --volume "$ui_state_dir:/var/lib/daihougou/data" \
  daihougou-mvp:local
docker ps --filter name=daihougou-ui-check
curl --fail --silent --show-error http://127.0.0.1:18081/healthz | python3 -m json.tool
```

Expected: the isolated container is running, `/healthz` returns HTTP 200 with `ready` or `degraded`, and
browser screenshots show no overlap at either viewport. Stop `daihougou-ui-check` after inspection; retain
the temporary directory path in the verification notes instead of deleting it during the task.

- [ ] **Step 5: Verify final commit history and working tree**

Run:

```bash
git status --short --branch
git log --oneline --decorate -12
```

Expected: implementation and documentation commits are present; the worktree is clean except for ignored local environment and persistent state files; `master` remains ahead of `origin/master` until the user explicitly requests a push.
