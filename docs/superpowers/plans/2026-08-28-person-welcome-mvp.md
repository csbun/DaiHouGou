# Person Welcome MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在目标 Debian 局域网服务器上运行一个单 worker 应用，从 `xiaobai` 码流检测“无人变有人”，并在规则开启时通过小爱音箱随机播报一句普通话欢迎语。

**Architecture:** go2rtc 继续作为独立容器提供回环 RTSP；新的 `daihougou` Python 包用持久 FFmpeg 进程保留最新帧、OpenCV DNN 完成人员检测、纯状态机判断进入事件，并用有界异步队列串行调用 MiService。FastAPI 管理页、检测循环和播报消费者由同一个 lifespan 拥有，SQLite 只保存脱敏元数据和规则开关；新安装时规则默认关闭。

**Tech Stack:** Python 3.12、FastAPI 0.141.1、Uvicorn 0.52.4、OpenCV DNN 4.14.0.94、NumPy 2.5.2、SQLite、FFmpeg、MiService 3.0.1、Docker Compose、pytest、Ruff

---

## 实施约束

- 开始执行前使用 `superpowers:using-git-worktrees` 创建隔离 worktree；当前 `master` 已包含设计提交 `7ef8ee4`。
- 每个任务严格执行红、绿、重构：先写指定测试并看到预期失败，再写最少实现。
- 不把账号、密码、DID、Token、完整 RTSP/Xiaomi URL 或上游原始错误写入日志、SQLite、测试快照和 Git。
- 应用始终只有一个 Uvicorn worker；不能用 `--reload`，否则会重复加载模型和重复启动检测循环。
- 自动测试不连接真实摄像头或小米账号；只有最后的物理验收使用现场设备。
- `welcome_on_person_entry` 初始为关闭，避免首次部署后在无人看管时自动播报。

## 文件结构

| 路径 | 责任 |
|---|---|
| `src/daihougou/settings.py` | 从环境变量加载、校验非敏感运行配置和小米凭据 |
| `src/daihougou/redaction.py` | 递归脱敏，供 MVP 与 PoC 共用 |
| `src/daihougou/speaker.py` | `Speaker` 协议、`SpeakResult` 和已验证的 MiService 直连适配器 |
| `src/daihougou/storage.py` | SQLite 建表、规则开关、脱敏事件和保留策略 |
| `src/daihougou/presence.py` | `unknown/absent/present` 纯状态机 |
| `src/daihougou/rules.py` | 内置规则注册表、欢迎语选择和冷却 |
| `src/daihougou/speaker_worker.py` | 容量 4 的动作队列、二次开关检查和串行播报 |
| `src/daihougou/vision/frame_source.py` | 持久 FFmpeg 子进程、最新帧和断线退避 |
| `src/daihougou/vision/person_detector.py` | OpenCV DNN 模型加载、输出解析和推理结果 |
| `src/daihougou/runtime.py` | 生命周期、检测循环、状态快照和组件编排 |
| `src/daihougou/web.py` | FastAPI 路由、同源/CSRF 校验和管理页视图模型 |
| `src/daihougou/main.py` | 生产对象装配和单 worker Uvicorn 入口 |
| `src/daihougou/templates/index.html` | 唯一管理页面 |
| `src/daihougou/static/app.css` | 紧凑、响应式的管理页样式 |
| `docker/mvp.Dockerfile` | 安装 FFmpeg、Python 依赖并校验固定模型摘要 |
| `compose.poc.yaml` | 增加常驻 app，持久化应用数据及 MiService Token |
| `.env.mvp.example` | 无秘密的 MVP 环境模板 |
| `docs/poc-runbook.md` | 首次认证、启动、重建验证、管理和物理验收 |
| `tests/app/` | MVP 单元、集成、Web、Compose 和生命周期测试 |

### Task 1: 应用包、依赖与强类型配置

**Files:**
- Create: `src/daihougou/__init__.py`
- Create: `src/daihougou/settings.py`
- Create: `tests/app/test_settings.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: 写配置失败测试**

```python
# tests/app/test_settings.py
from pathlib import Path

import pytest

from daihougou.settings import Settings


BASE_ENV = {
    "MI_USER": "owner@example.test",
    "MI_PASS": "secret",
    "MI_DID": "123456789",
}


def test_settings_use_mvp_defaults_without_exposing_secrets() -> None:
    settings = Settings.from_mapping(BASE_ENV)

    assert settings.stream_url == "rtsp://127.0.0.1:8554/xiaobai"
    assert settings.data_dir == Path("/var/lib/daihougou/data")
    assert settings.detection_fps == 1.0
    assert settings.person_threshold == 0.55
    assert settings.web_port == 8080
    assert "secret" not in repr(settings)


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("MI_USER", "", "MI_USER is required"),
        ("MI_PASS", "", "MI_PASS is required"),
        ("MI_DID", "", "MI_DID is required"),
        ("PERSON_THRESHOLD", "1.1", "PERSON_THRESHOLD must be between 0 and 1"),
        ("DETECTION_FPS", "0", "DETECTION_FPS must be greater than 0"),
    ],
)
def test_settings_reject_invalid_values(key: str, value: str, message: str) -> None:
    env = {**BASE_ENV, key: value}

    with pytest.raises(ValueError, match=message):
        Settings.from_mapping(env)
```

- [ ] **Step 2: 确认测试因新包不存在而失败**

Run: `pytest tests/app/test_settings.py -q`

Expected: FAIL，包含 `ModuleNotFoundError: No module named 'daihougou'`。

- [ ] **Step 3: 增加依赖、入口和配置实现**

把 `pyproject.toml` 改为：

```toml
[build-system]
requires = ["setuptools==80.9.0"]
build-backend = "setuptools.build_meta"

[project]
name = "daihougou-poc"
version = "0.2.0"
requires-python = ">=3.12"
dependencies = [
    "fastapi==0.141.1",
    "jinja2==3.1.6",
    "miservice==3.0.1",
    "numpy==2.5.2",
    "opencv-python-headless==4.14.0.94",
    "python-multipart==0.0.32",
    "uvicorn==0.52.4",
]

[project.optional-dependencies]
dev = ["httpx==0.28.1", "pytest==9.0.2", "ruff==0.16.4"]

[project.scripts]
daihougou = "daihougou.main:run"
daihougou-poc = "daihougou_poc.cli:main"

[tool.setuptools.packages.find]
where = ["src"]

[tool.setuptools.package-data]
daihougou = ["templates/*.html", "static/*.css"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

创建空的 `src/daihougou/__init__.py`，再创建：

```python
# src/daihougou/settings.py
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "")
    if not value.strip():
        raise ValueError(f"{key} is required")
    return value


def _positive_float(env: Mapping[str, str], key: str, default: str) -> float:
    value = float(env.get(key, default))
    if value <= 0:
        raise ValueError(f"{key} must be greater than 0")
    return value


@dataclass(frozen=True)
class Settings:
    mi_user: str = field(repr=False)
    mi_pass: str = field(repr=False)
    mi_did: str = field(repr=False)
    stream_url: str = "rtsp://127.0.0.1:8554/xiaobai"
    data_dir: Path = Path("/var/lib/daihougou/data")
    model: Path = Path("/opt/daihougou/models/person_detection_mediapipe_2023mar.onnx")
    detection_fps: float = 1.0
    person_threshold: float = 0.55
    leave_seconds: float = 10.0
    welcome_cooldown_seconds: float = 60.0
    web_host: str = "0.0.0.0"
    web_port: int = 8080

    @classmethod
    def from_mapping(cls, env: Mapping[str, str]) -> "Settings":
        threshold = float(env.get("PERSON_THRESHOLD", "0.55"))
        if not 0 < threshold < 1:
            raise ValueError("PERSON_THRESHOLD must be between 0 and 1")
        port = int(env.get("WEB_PORT", "8080"))
        if not 1 <= port <= 65535:
            raise ValueError("WEB_PORT must be between 1 and 65535")
        return cls(
            mi_user=_required(env, "MI_USER"),
            mi_pass=_required(env, "MI_PASS"),
            mi_did=_required(env, "MI_DID"),
            stream_url=env.get("STREAM_URL", cls.stream_url),
            data_dir=Path(env.get("DATA_DIR", str(cls.data_dir))),
            model=Path(env.get("MODEL", str(cls.model))),
            detection_fps=_positive_float(env, "DETECTION_FPS", "1.0"),
            person_threshold=threshold,
            leave_seconds=_positive_float(env, "LEAVE_SECONDS", "10.0"),
            welcome_cooldown_seconds=_positive_float(
                env, "WELCOME_COOLDOWN_SECONDS", "60.0"
            ),
            web_host=env.get("WEB_HOST", "0.0.0.0"),
            web_port=port,
        )
```

- [ ] **Step 4: 安装本地开发依赖并运行配置测试**

Run: `python -m pip install -e '.[dev]'`

Expected: 依赖安装完成，退出码为 `0`。

Run: `pytest tests/app/test_settings.py -q`

Expected: `6 passed`。

- [ ] **Step 5: 运行 Ruff 并提交**

Run: `ruff check src/daihougou tests/app/test_settings.py`

Expected: `All checks passed!`

```bash
git add pyproject.toml src/daihougou tests/app/test_settings.py
git commit -m "feat: add mvp application settings"
```

### Task 2: 共用脱敏、MiService 适配器和 Token 持久化

**Files:**
- Create: `src/daihougou/redaction.py`
- Create: `src/daihougou/speaker.py`
- Modify: `src/daihougou_poc/events.py`
- Modify: `src/daihougou_poc/speakers/base.py`
- Modify: `src/daihougou_poc/speakers/direct.py`
- Modify: `tests/speakers/test_direct.py`
- Modify: `tests/test_docker_context.py`
- Modify: `.gitignore`
- Modify: `.dockerignore`
- Modify: `compose.poc.yaml`

- [ ] **Step 1: 写认证分类、HOME 传递和部署策略失败测试**

在 `tests/speakers/test_direct.py` 末尾增加：

```python
def test_direct_speaker_passes_persistent_home_to_miservice() -> None:
    captured = {}

    def run(command: list[str], env: dict[str, str], timeout: int):
        captured.update(env)
        return 0, '{"code":0}', ""

    with patch.dict("os.environ", {"HOME": "/var/lib/daihougou/mi", "PATH": "/bin"}, clear=True):
        DirectSpeaker("user", "password", "did", run=run).speak("hello")

    assert captured["HOME"] == "/var/lib/daihougou/mi"


def test_direct_speaker_classifies_login_failure_without_storing_raw_error() -> None:
    def run(command: list[str], env: dict[str, str], timeout: int):
        return 1, "", "Exception on login owner@example.test: Login auth failed"

    result = DirectSpeaker("owner@example.test", "password", "did", run=run).speak("hello")

    assert result.success is False
    assert result.code == "reauth_required"
    assert result.error == "reauth_required"
    assert "owner@example.test" not in result.error
```

在 `tests/test_docker_context.py` 的第一个集合中增加：

```python
        ".env.mvp",
        "deploy/app/state",
        "deploy/miservice/state",
```

并增加：

```python
def test_probe_persists_miservice_token_in_private_home() -> None:
    compose = Path("compose.poc.yaml").read_text(encoding="utf-8")

    assert "HOME: /var/lib/daihougou/mi" in compose
    assert "./deploy/miservice/state:/var/lib/daihougou/mi" in compose
```

- [ ] **Step 2: 确认测试按预期失败**

Run: `pytest tests/speakers/test_direct.py tests/test_docker_context.py -q`

Expected: FAIL，认证错误仍被分类为 `invalid_response`，且忽略路径与 Compose 挂载缺失。

- [ ] **Step 3: 抽取脱敏和音箱实现，同时保留 PoC 兼容导入**

```python
# src/daihougou/redaction.py
from typing import Any

SECRET_FRAGMENTS = (
    "token",
    "password",
    "pass",
    "secret",
    "authorization",
    "cookie",
    "did",
    "user",
    "url",
)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if any(part in key.lower() for part in SECRET_FRAGMENTS)
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
```

```python
# src/daihougou/speaker.py
import json
import os
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

Runner = Callable[[list[str], dict[str, str], int], tuple[int, str, str]]
REAUTH_MARKERS = (
    "login auth failed",
    "exception on login",
    "verification code",
    "eof when reading a line",
    "验证码",
)


@dataclass(frozen=True)
class SpeakResult:
    success: bool
    latency_ms: int
    code: int | str | None
    error: str = ""


class Speaker(Protocol):
    def speak(self, text: str) -> SpeakResult: ...


def _run(command: list[str], env: dict[str, str], timeout: int) -> tuple[int, str, str]:
    completed = subprocess.run(
        command,
        env=env,
        timeout=timeout,
        check=False,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    return completed.returncode, completed.stdout, completed.stderr


class DirectSpeaker:
    def __init__(self, user: str, password: str, did: str, run: Runner = _run) -> None:
        if not user or not password or not did:
            raise ValueError("MI_USER, MI_PASS, and MI_DID are required")
        safe_environment = {
            key: os.environ[key]
            for key in ("HOME", "LANG", "LC_ALL", "PATH", "PYTHONPATH")
            if key in os.environ
        }
        self._env = {
            **safe_environment,
            "MI_USER": user,
            "MI_PASS": password,
            "MI_DID": did,
        }
        self._did = did
        self._run = run

    def speak(self, text: str) -> SpeakResult:
        payload = json.dumps({"did": self._did, "siid": 5, "aiid": 3, "in": [text]})
        started = time.monotonic()
        try:
            returncode, stdout, stderr = self._run(
                ["python", "-m", "miservice", "action", payload], self._env, 30
            )
        except subprocess.TimeoutExpired:
            latency_ms = round((time.monotonic() - started) * 1000)
            return SpeakResult(False, latency_ms, "timeout", "timeout")
        latency_ms = round((time.monotonic() - started) * 1000)
        lowered_stderr = stderr.lower()
        if returncode != 0 and any(marker in lowered_stderr for marker in REAUTH_MARKERS):
            return SpeakResult(False, latency_ms, "reauth_required", "reauth_required")
        try:
            body = json.loads(stdout)
        except json.JSONDecodeError:
            body = None
        code = body["code"] if isinstance(body, dict) and "code" in body else "invalid_response"
        if code == 70016:
            return SpeakResult(False, latency_ms, "reauth_required", "reauth_required")
        success = returncode == 0 and code == 0
        return SpeakResult(success, latency_ms, code, "subprocess_error" if stderr else "")
```

把 `src/daihougou_poc/speakers/base.py` 改为：

```python
from daihougou.speaker import Speaker, SpeakResult

__all__ = ["Speaker", "SpeakResult"]
```

把 `src/daihougou_poc/speakers/direct.py` 改为：

```python
from daihougou.speaker import DirectSpeaker

__all__ = ["DirectSpeaker"]
```

在 `src/daihougou_poc/events.py` 删除本地 `SECRET_FRAGMENTS` 和 `redact`，改为：

```python
from daihougou.redaction import redact
```

- [ ] **Step 4: 增加状态目录忽略项和 probe Token 挂载**

在 `.gitignore` 增加：

```text
.env.mvp
deploy/app/state/
deploy/miservice/state/
```

在 `.dockerignore` 增加不带结尾斜杠的构建上下文路径：

```text
.env.mvp
deploy/app/state
deploy/miservice/state
```

在 `compose.poc.yaml` 的 `probe` 服务加入：

```yaml
    environment:
      HOME: /var/lib/daihougou/mi
    volumes:
      - ./artifacts:/workspace/artifacts
      - ./config:/workspace/config:ro
      - ./deploy/miservice/state:/var/lib/daihougou/mi
```

注意替换原 `volumes` 块，不能留下重复键。

- [ ] **Step 5: 运行回归测试和 Compose 解析**

Run: `pytest tests/speakers/test_direct.py tests/test_report.py tests/test_docker_context.py -q`

Expected: 所有测试 PASS。

Run: `docker compose -f compose.poc.yaml config --quiet`

Expected: 无输出，退出码 `0`。

- [ ] **Step 6: 提交**

```bash
git add .gitignore .dockerignore compose.poc.yaml src/daihougou src/daihougou_poc tests
git commit -m "feat: persist miservice authentication state"
```

### Task 3: SQLite 规则与脱敏事件存储

**Files:**
- Create: `src/daihougou/storage.py`
- Create: `tests/app/test_storage.py`

- [ ] **Step 1: 写建表、开关、脱敏和保留数量失败测试**

```python
# tests/app/test_storage.py
import json
from pathlib import Path

from daihougou.storage import EventRecord, Storage


def test_storage_starts_welcome_rule_disabled_and_updates_it(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()

    assert storage.rule_enabled("welcome_on_person_entry") is False
    storage.set_rule_enabled("welcome_on_person_entry", True)
    assert storage.rule_enabled("welcome_on_person_entry") is True


def test_storage_redacts_details_and_keeps_newest_events(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db", max_events=2)
    storage.initialize()
    for number in range(3):
        storage.record_event(
            EventRecord(
                kind=f"event_{number}",
                success=True,
                details={"confidence": 0.7, "token": "secret", "stream_url": "private"},
            )
        )

    events = storage.recent_events(limit=10)

    assert [event.kind for event in events] == ["event_2", "event_1"]
    assert json.loads(events[0].details_json) == {
        "confidence": 0.7,
        "token": "[REDACTED]",
        "stream_url": "[REDACTED]",
    }


def test_storage_enables_wal(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()

    assert storage.journal_mode() == "wal"
```

- [ ] **Step 2: 确认存储测试失败**

Run: `pytest tests/app/test_storage.py -q`

Expected: FAIL，包含 `ModuleNotFoundError: No module named 'daihougou.storage'`。

- [ ] **Step 3: 实现连接封装、事务和保留策略**

```python
# src/daihougou/storage.py
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from daihougou.redaction import redact

WELCOME_RULE_ID = "welcome_on_person_entry"


@dataclass(frozen=True)
class EventRecord:
    kind: str
    success: bool
    rule_id: str | None = None
    latency_ms: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoredEvent:
    id: int
    occurred_at: str
    kind: str
    rule_id: str | None
    success: bool
    latency_ms: int | None
    details_json: str


class Storage:
    def __init__(self, path: Path, max_events: int = 5000) -> None:
        self._path = path
        self._max_events = max_events

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS rules (
                    id TEXT PRIMARY KEY,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    occurred_at TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    rule_id TEXT,
                    success INTEGER NOT NULL CHECK (success IN (0, 1)),
                    latency_ms INTEGER,
                    details_json TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO rules(id, enabled, updated_at) VALUES (?, 0, ?)",
                (WELCOME_RULE_ID, datetime.now(UTC).isoformat()),
            )
            self._prune(connection)

    def rule_enabled(self, rule_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT enabled FROM rules WHERE id = ?", (rule_id,)
            ).fetchone()
        if row is None:
            raise KeyError(rule_id)
        return bool(row["enabled"])

    def set_rule_enabled(self, rule_id: str, enabled: bool) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE rules SET enabled = ?, updated_at = ? WHERE id = ?",
                (int(enabled), datetime.now(UTC).isoformat(), rule_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(rule_id)

    def record_event(self, event: EventRecord) -> None:
        details_json = json.dumps(redact(event.details), ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO events(occurred_at, kind, rule_id, success, latency_ms, details_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(UTC).isoformat(),
                    event.kind,
                    event.rule_id,
                    int(event.success),
                    event.latency_ms,
                    details_json,
                ),
            )
            self._prune(connection)

    def recent_events(self, limit: int = 20) -> list[StoredEvent]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            StoredEvent(
                id=row["id"],
                occurred_at=row["occurred_at"],
                kind=row["kind"],
                rule_id=row["rule_id"],
                success=bool(row["success"]),
                latency_ms=row["latency_ms"],
                details_json=row["details_json"],
            )
            for row in rows
        ]

    def journal_mode(self) -> str:
        with self._connect() as connection:
            return str(connection.execute("PRAGMA journal_mode").fetchone()[0])

    def _prune(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM events
            WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)
            """,
            (self._max_events,),
        )
```

- [ ] **Step 4: 运行存储和脱敏回归测试**

Run: `pytest tests/app/test_storage.py tests/test_report.py -q`

Expected: 所有测试 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/daihougou/storage.py tests/app/test_storage.py
git commit -m "feat: add persistent rule and event storage"
```

### Task 4: 人员在场纯状态机

**Files:**
- Create: `src/daihougou/presence.py`
- Create: `tests/app/test_presence.py`

- [ ] **Step 1: 写启动校准、进入、抖动、离开和单调时间测试**

```python
# tests/app/test_presence.py
import pytest

from daihougou.presence import PresenceEventKind, PresenceState, PresenceTracker


def feed(tracker: PresenceTracker, values: list[tuple[float, bool]]):
    return [tracker.observe(at, present) for at, present in values]


def test_startup_calibrates_present_without_emitting_entry() -> None:
    tracker = PresenceTracker(leave_seconds=10)

    events = feed(tracker, [(0, True), (1, False), (2, True)])

    assert events == [None, None, None]
    assert tracker.state is PresenceState.PRESENT


def test_absent_to_two_of_three_present_emits_one_entry() -> None:
    tracker = PresenceTracker(leave_seconds=10)
    feed(tracker, [(0, False), (1, False), (2, False)])

    first = tracker.observe(3, True)
    second = tracker.observe(4, True)
    third = tracker.observe(5, True)

    assert first is None
    assert second is not None
    assert second.kind is PresenceEventKind.PERSON_ENTERED
    assert third is None


def test_single_negative_does_not_make_present_person_leave() -> None:
    tracker = PresenceTracker(leave_seconds=10)
    feed(tracker, [(0, True), (1, True), (2, True)])

    assert tracker.observe(5, False) is None
    assert tracker.observe(6, True) is None
    assert tracker.state is PresenceState.PRESENT


def test_ten_seconds_without_person_changes_to_absent_without_event() -> None:
    tracker = PresenceTracker(leave_seconds=10)
    feed(tracker, [(0, True), (1, True), (2, True)])

    assert tracker.observe(11.9, False) is None
    assert tracker.state is PresenceState.PRESENT
    assert tracker.observe(12, False) is None
    assert tracker.state is PresenceState.ABSENT


def test_observation_time_must_be_monotonic() -> None:
    tracker = PresenceTracker()
    tracker.observe(2, False)

    with pytest.raises(ValueError, match="monotonic"):
        tracker.observe(1, False)
```

- [ ] **Step 2: 确认状态机测试失败**

Run: `pytest tests/app/test_presence.py -q`

Expected: FAIL，包含 `No module named 'daihougou.presence'`。

- [ ] **Step 3: 实现不依赖 I/O 的状态机**

```python
# src/daihougou/presence.py
from collections import deque
from dataclasses import dataclass
from enum import StrEnum


class PresenceState(StrEnum):
    UNKNOWN = "unknown"
    ABSENT = "absent"
    PRESENT = "present"


class PresenceEventKind(StrEnum):
    PERSON_ENTERED = "person_entered"


@dataclass(frozen=True)
class PresenceEvent:
    kind: PresenceEventKind
    occurred_monotonic: float


class PresenceTracker:
    def __init__(self, leave_seconds: float = 10.0) -> None:
        self.state = PresenceState.UNKNOWN
        self._leave_seconds = leave_seconds
        self._recent: deque[bool] = deque(maxlen=3)
        self._last_observed_at: float | None = None
        self._last_person_at: float | None = None

    def observe(self, at: float, person_present: bool) -> PresenceEvent | None:
        if self._last_observed_at is not None and at < self._last_observed_at:
            raise ValueError("observation time must be monotonic")
        self._last_observed_at = at
        self._recent.append(person_present)
        if person_present:
            self._last_person_at = at

        stable_present = len(self._recent) == 3 and sum(self._recent) >= 2
        if self.state is PresenceState.UNKNOWN and len(self._recent) == 3:
            self.state = PresenceState.PRESENT if stable_present else PresenceState.ABSENT
            return None

        if self.state is PresenceState.ABSENT and stable_present:
            self.state = PresenceState.PRESENT
            return PresenceEvent(PresenceEventKind.PERSON_ENTERED, at)

        if (
            self.state is PresenceState.PRESENT
            and not person_present
            and self._last_person_at is not None
            and at - self._last_person_at >= self._leave_seconds
        ):
            self.state = PresenceState.ABSENT
        return None
```

- [ ] **Step 4: 运行状态机测试**

Run: `pytest tests/app/test_presence.py -q`

Expected: `5 passed`。

- [ ] **Step 5: 提交**

```bash
git add src/daihougou/presence.py tests/app/test_presence.py
git commit -m "feat: detect stable person entry transitions"
```

### Task 5: 内置欢迎规则与有界播报消费者

**Files:**
- Create: `src/daihougou/rules.py`
- Create: `src/daihougou/speaker_worker.py`
- Create: `tests/app/test_rules.py`
- Create: `tests/app/test_speaker_worker.py`

- [ ] **Step 1: 写规则关闭、冷却和欢迎语来源测试**

```python
# tests/app/test_rules.py
from daihougou.presence import PresenceEvent, PresenceEventKind
from daihougou.rules import WELCOME_PHRASES, WelcomeRule


class RuleState:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def rule_enabled(self, rule_id: str) -> bool:
        assert rule_id == "welcome_on_person_entry"
        return self.enabled


def event(at: float) -> PresenceEvent:
    return PresenceEvent(PresenceEventKind.PERSON_ENTERED, at)


def test_disabled_rule_produces_no_action() -> None:
    rule = WelcomeRule(RuleState(False), chooser=lambda phrases: phrases[0])

    assert rule.handle(event(10)) is None


def test_enabled_rule_uses_catalog_and_applies_sixty_second_cooldown() -> None:
    rule = WelcomeRule(RuleState(True), cooldown_seconds=60, chooser=lambda phrases: phrases[-1])

    first = rule.handle(event(10))

    assert first is not None
    assert first.text == WELCOME_PHRASES[-1]
    assert rule.handle(event(69.9)) is None
    assert rule.handle(event(70)) is not None
```

- [ ] **Step 2: 写消费者二次开关、成功、重新认证和队列满测试**

```python
# tests/app/test_speaker_worker.py
import asyncio

from daihougou.rules import SpeechAction
from daihougou.speaker import SpeakResult
from daihougou.speaker_worker import SpeakerWorker
from daihougou.storage import EventRecord


class FakeStorage:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.events: list[EventRecord] = []

    def rule_enabled(self, rule_id: str) -> bool:
        return self.enabled

    def record_event(self, event: EventRecord) -> None:
        self.events.append(event)


class FakeSpeaker:
    def __init__(self, result: SpeakResult) -> None:
        self.result = result
        self.spoken: list[str] = []

    def speak(self, text: str) -> SpeakResult:
        self.spoken.append(text)
        return self.result


def action(number: int = 1) -> SpeechAction:
    return SpeechAction("welcome_on_person_entry", f"欢迎 {number}", float(number))


def test_worker_rechecks_rule_before_speaking() -> None:
    async def scenario() -> None:
        storage = FakeStorage(enabled=False)
        speaker = FakeSpeaker(SpeakResult(True, 10, 0))
        worker = SpeakerWorker(speaker, storage)
        await worker.start()
        assert worker.submit(action()) is True
        await worker.join()
        await worker.stop()
        assert speaker.spoken == []
        assert storage.events[-1].kind == "speaker_skipped_disabled"

    asyncio.run(scenario())


def test_worker_records_classified_reauthentication() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        speaker = FakeSpeaker(SpeakResult(False, 20, "reauth_required", "reauth_required"))
        worker = SpeakerWorker(speaker, storage)
        await worker.start()
        worker.submit(action())
        await worker.join()
        await worker.stop()
        assert worker.auth_status == "reauth_required"
        assert storage.events[-1].details == {"code": "reauth_required"}

    asyncio.run(scenario())


def test_worker_drops_newest_action_when_queue_is_full() -> None:
    storage = FakeStorage()
    speaker = FakeSpeaker(SpeakResult(True, 10, 0))
    worker = SpeakerWorker(speaker, storage, queue_size=1)

    assert worker.submit(action(1)) is True
    assert worker.submit(action(2)) is False
    assert storage.events[-1].kind == "speaker_queue_full"
```

- [ ] **Step 3: 确认规则和消费者测试失败**

Run: `pytest tests/app/test_rules.py tests/app/test_speaker_worker.py -q`

Expected: FAIL，两个新模块不存在。

- [ ] **Step 4: 实现内置规则和动作类型**

```python
# src/daihougou/rules.py
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from daihougou.presence import PresenceEvent, PresenceEventKind

WELCOME_RULE_ID = "welcome_on_person_entry"
WELCOME_PHRASES = (
    "你好呀，欢迎回来。",
    "嗨，很高兴见到你。",
    "欢迎你，今天也要开心呀。",
)


class RuleState(Protocol):
    def rule_enabled(self, rule_id: str) -> bool: ...


@dataclass(frozen=True)
class SpeechAction:
    rule_id: str
    text: str
    created_monotonic: float


class WelcomeRule:
    def __init__(
        self,
        state: RuleState,
        cooldown_seconds: float = 60,
        chooser: Callable[[Sequence[str]], str] = random.choice,
    ) -> None:
        self._state = state
        self._cooldown_seconds = cooldown_seconds
        self._chooser = chooser
        self._last_action_at: float | None = None

    def handle(self, event: PresenceEvent) -> SpeechAction | None:
        if event.kind is not PresenceEventKind.PERSON_ENTERED:
            return None
        if not self._state.rule_enabled(WELCOME_RULE_ID):
            return None
        if (
            self._last_action_at is not None
            and event.occurred_monotonic - self._last_action_at < self._cooldown_seconds
        ):
            return None
        self._last_action_at = event.occurred_monotonic
        return SpeechAction(
            WELCOME_RULE_ID,
            self._chooser(WELCOME_PHRASES),
            event.occurred_monotonic,
        )
```

- [ ] **Step 5: 实现有界异步消费者**

```python
# src/daihougou/speaker_worker.py
import asyncio
from typing import Protocol

from daihougou.rules import SpeechAction
from daihougou.speaker import Speaker
from daihougou.storage import EventRecord


class WorkerStorage(Protocol):
    def rule_enabled(self, rule_id: str) -> bool: ...
    def record_event(self, event: EventRecord) -> None: ...


class SpeakerWorker:
    def __init__(self, speaker: Speaker, storage: WorkerStorage, queue_size: int = 4) -> None:
        self._speaker = speaker
        self._storage = storage
        self._queue: asyncio.Queue[SpeechAction | None] = asyncio.Queue(maxsize=queue_size)
        self._task: asyncio.Task[None] | None = None
        self.auth_status = "unknown"

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._consume(), name="speaker-worker")

    def submit(self, action: SpeechAction) -> bool:
        try:
            self._queue.put_nowait(action)
            return True
        except asyncio.QueueFull:
            self._storage.record_event(
                EventRecord("speaker_queue_full", False, rule_id=action.rule_id)
            )
            return False

    async def join(self) -> None:
        await self._queue.join()

    async def stop(self) -> None:
        if self._task is None:
            return
        await self._queue.put(None)
        await self._task
        self._task = None

    async def _consume(self) -> None:
        while True:
            action = await self._queue.get()
            try:
                if action is None:
                    return
                if not self._storage.rule_enabled(action.rule_id):
                    self._storage.record_event(
                        EventRecord("speaker_skipped_disabled", False, rule_id=action.rule_id)
                    )
                    continue
                if self.auth_status == "reauth_required":
                    self._storage.record_event(
                        EventRecord("speaker_reauth_required", False, rule_id=action.rule_id)
                    )
                    continue
                result = await asyncio.to_thread(self._speaker.speak, action.text)
                self.auth_status = (
                    "reauth_required" if result.code == "reauth_required" else "ready"
                )
                self._storage.record_event(
                    EventRecord(
                        "speaker_completed",
                        result.success,
                        rule_id=action.rule_id,
                        latency_ms=result.latency_ms,
                        details={"code": result.code},
                    )
                )
            finally:
                self._queue.task_done()
```

- [ ] **Step 6: 运行规则和消费者测试**

Run: `pytest tests/app/test_rules.py tests/app/test_speaker_worker.py -q`

Expected: `5 passed`。

- [ ] **Step 7: 提交**

```bash
git add src/daihougou/rules.py src/daihougou/speaker_worker.py tests/app
git commit -m "feat: queue welcome speech actions"
```

### Task 6: FFmpeg 最新帧源与断线退避

**Files:**
- Create: `src/daihougou/vision/__init__.py`
- Create: `src/daihougou/vision/frame_source.py`
- Create: `tests/app/vision/test_frame_source.py`

- [ ] **Step 1: 写命令、退避、最新帧覆盖和 EOF 测试**

```python
# tests/app/vision/test_frame_source.py
import io
import time

import numpy as np

from daihougou.vision.frame_source import (
    FRAME_BYTES,
    FfmpegFrameSource,
    build_ffmpeg_command,
    reconnect_delay,
)


def test_ffmpeg_command_forces_tcp_one_fps_and_fixed_bgr_frames() -> None:
    command = build_ffmpeg_command("rtsp://127.0.0.1:8554/xiaobai", 1.0)

    assert command[:4] == ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel"]
    assert command[command.index("-rtsp_transport") + 1] == "tcp"
    assert command[command.index("-vf") + 1] == "fps=1.0,scale=256:256"
    assert command[-6:] == ["-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]


def test_reconnect_delay_caps_at_thirty_seconds() -> None:
    assert [reconnect_delay(attempt) for attempt in range(7)] == [1, 2, 4, 8, 16, 30, 30]


class FakeProcess:
    def __init__(self, payload: bytes) -> None:
        self.stdout = io.BytesIO(payload)
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        self.returncode = 0
        return 0

    def kill(self) -> None:
        self.returncode = -9


def test_source_exposes_only_newest_complete_frame() -> None:
    first = np.zeros((256, 256, 3), dtype=np.uint8)
    second = np.full((256, 256, 3), 7, dtype=np.uint8)
    processes = iter([FakeProcess(first.tobytes() + second.tobytes()), FakeProcess(b"")])
    source = FfmpegFrameSource(
        "rtsp://127.0.0.1:8554/xiaobai",
        process_factory=lambda command: next(processes),
        sleeper=lambda seconds: time.sleep(0.01),
    )

    source.start()
    sample = source.wait_for_frame(after_sequence=0, timeout=1)
    deadline = time.monotonic() + 1
    while sample is not None and sample.sequence < 2 and time.monotonic() < deadline:
        sample = source.wait_for_frame(after_sequence=sample.sequence, timeout=0.1)
    source.stop()

    assert sample is not None
    assert sample.sequence == 2
    assert sample.frame.shape == (256, 256, 3)
    assert int(sample.frame[0, 0, 0]) == 7
    assert FRAME_BYTES == 256 * 256 * 3


def test_truncated_frame_is_never_published() -> None:
    processes = iter([FakeProcess(b"x" * (FRAME_BYTES - 1)), FakeProcess(b"")])
    source = FfmpegFrameSource(
        "rtsp://127.0.0.1:8554/xiaobai",
        process_factory=lambda command: next(processes),
        sleeper=lambda seconds: time.sleep(0.01),
    )

    source.start()
    sample = source.wait_for_frame(after_sequence=0, timeout=0.1)
    source.stop()

    assert sample is None
```

- [ ] **Step 2: 确认帧源测试失败**

Run: `pytest tests/app/vision/test_frame_source.py -q`

Expected: FAIL，包含 `No module named 'daihougou.vision'`。

- [ ] **Step 3: 实现持久进程、条件变量和最新帧语义**

创建空的 `src/daihougou/vision/__init__.py`，再创建：

```python
# src/daihougou/vision/frame_source.py
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import BinaryIO, Protocol

import numpy as np
import numpy.typing as npt

FRAME_WIDTH = 256
FRAME_HEIGHT = 256
FRAME_BYTES = FRAME_WIDTH * FRAME_HEIGHT * 3


class Process(Protocol):
    stdout: BinaryIO | None
    returncode: int | None

    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def kill(self) -> None: ...


ProcessFactory = Callable[[list[str]], Process]


@dataclass(frozen=True)
class FrameSample:
    sequence: int
    captured_monotonic: float
    frame: npt.NDArray[np.uint8]


def build_ffmpeg_command(stream_url: str, fps: float) -> list[str]:
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-rtsp_transport",
        "tcp",
        "-i",
        stream_url,
        "-an",
        "-sn",
        "-dn",
        "-vf",
        f"fps={fps},scale={FRAME_WIDTH}:{FRAME_HEIGHT}",
        "-pix_fmt",
        "bgr24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def reconnect_delay(attempt: int) -> int:
    return min(2**attempt, 30)


def _spawn(command: list[str]) -> Process:
    return subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


class FfmpegFrameSource:
    def __init__(
        self,
        stream_url: str,
        fps: float = 1.0,
        process_factory: ProcessFactory = _spawn,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._command = build_ffmpeg_command(stream_url, fps)
        self._process_factory = process_factory
        self._sleeper = sleeper
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: Process | None = None
        self._latest: FrameSample | None = None
        self._sequence = 0
        self.status = "stopped"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self.status = "starting"
        self._thread = threading.Thread(target=self._run, name="ffmpeg-frame-source", daemon=True)
        self._thread.start()

    def wait_for_frame(self, after_sequence: int, timeout: float) -> FrameSample | None:
        with self._condition:
            self._condition.wait_for(
                lambda: self._latest is not None and self._latest.sequence > after_sequence,
                timeout=timeout,
            )
            if self._latest is None or self._latest.sequence <= after_sequence:
                return None
            return self._latest

    def stop(self) -> None:
        self._stop.set()
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        if thread is not None and thread.is_alive() and process is not None:
            process.kill()
            thread.join(timeout=2)
        self._thread = None
        self.status = "stopped"
        with self._condition:
            self._condition.notify_all()

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            try:
                process = self._process_factory(self._command)
            except (OSError, StopIteration):
                self.status = "degraded"
                if not self._wait_backoff(attempt):
                    break
                attempt += 1
                continue
            self._process = process
            stream = process.stdout
            complete_frames = 0
            if stream is not None:
                while not self._stop.is_set():
                    payload = _read_exact(stream, FRAME_BYTES)
                    if len(payload) != FRAME_BYTES:
                        break
                    frame = np.frombuffer(payload, dtype=np.uint8).reshape(
                        FRAME_HEIGHT, FRAME_WIDTH, 3
                    ).copy()
                    with self._condition:
                        self._sequence += 1
                        self._latest = FrameSample(self._sequence, time.monotonic(), frame)
                        self.status = "ready"
                        self._condition.notify_all()
                    complete_frames += 1
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
            self._process = None
            if self._stop.is_set():
                break
            self.status = "degraded"
            attempt = 0 if complete_frames else attempt + 1
            if not self._wait_backoff(max(0, attempt - 1)):
                break

    def _wait_backoff(self, attempt: int) -> bool:
        delay = reconnect_delay(attempt)
        deadline = time.monotonic() + delay
        while not self._stop.is_set() and time.monotonic() < deadline:
            self._sleeper(min(0.1, max(0, deadline - time.monotonic())))
        return not self._stop.is_set()
```

- [ ] **Step 4: 运行帧源测试并处理线程清理警告**

Run: `pytest tests/app/vision/test_frame_source.py -q -W error`

Expected: `4 passed`，没有未处理线程异常或资源警告。

- [ ] **Step 5: 提交**

```bash
git add src/daihougou/vision tests/app/vision
git commit -m "feat: read latest camera frame with ffmpeg"
```

### Task 7: OpenCV 人员检测器与固定模型镜像

**Files:**
- Create: `src/daihougou/vision/person_detector.py`
- Create: `tests/app/vision/test_person_detector.py`
- Create: `docker/mvp.Dockerfile`
- Create: `tests/app/test_mvp_dockerfile.py`

- [ ] **Step 1: 写模型输出解析和阈值测试**

```python
# tests/app/vision/test_person_detector.py
from pathlib import Path

import numpy as np
import pytest

from daihougou.vision.person_detector import PersonDetector


class FakeNet:
    def __init__(self, outputs: tuple[np.ndarray, np.ndarray]) -> None:
        self.outputs = outputs
        self.inputs: list[np.ndarray] = []
        self.requested_outputs: list[tuple[str, ...]] = []

    def setInput(self, blob: np.ndarray) -> None:
        self.inputs.append(blob)

    def getUnconnectedOutLayersNames(self) -> tuple[str, ...]:
        return ("Identity:0", "Identity_1:0")

    def forward(self, output_names: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray]:
        self.requested_outputs.append(output_names)
        return self.outputs


def outputs(*logits: float) -> tuple[np.ndarray, np.ndarray]:
    boxes = np.zeros((1, len(logits), 12), dtype=np.float32)
    scores = np.array([[[logit] for logit in logits]], dtype=np.float32)
    return boxes, scores


def test_detector_returns_highest_person_confidence() -> None:
    net = FakeNet(outputs(-1000.0, 1.0, 0.5))
    detector = PersonDetector(Path("model.onnx"), 0.55, net=net)
    frame = np.zeros((100, 222, 3), dtype=np.uint8)
    frame[:, :, 2] = 255

    with np.errstate(over="raise"):
        result = detector.detect(frame)

    assert result.person_present is True
    assert result.confidence == pytest.approx(1 / (1 + np.exp(-1.0)))
    assert result.latency_ms >= 0
    assert net.inputs[0].shape == (1, 3, 224, 224)
    assert net.inputs[0].dtype == np.float32
    assert net.inputs[0][0, :, 0, 0] == pytest.approx([0.0, 0.0, 0.0])
    assert net.inputs[0][0, :, 61, 112] == pytest.approx([0.0, 0.0, 0.0])
    assert net.inputs[0][0, :, 62, 112] == pytest.approx([1.0, -1.0, -1.0])
    assert net.inputs[0][0, :, 112, 112] == pytest.approx([1.0, -1.0, -1.0])
    assert net.requested_outputs == [("Identity:0", "Identity_1:0")]


def test_detector_reports_absent_below_threshold() -> None:
    net = FakeNet(outputs(0.1))
    detector = PersonDetector(Path("model.onnx"), 0.55, net=net)

    result = detector.detect(np.zeros((256, 256, 3), dtype=np.uint8))

    assert result.person_present is False
    assert result.confidence == pytest.approx(1 / (1 + np.exp(-0.1)))
```

- [ ] **Step 2: 写 Dockerfile 固定 URL 与摘要策略测试**

```python
# tests/app/test_mvp_dockerfile.py
from pathlib import Path


def test_mvp_image_pins_and_verifies_person_model() -> None:
    dockerfile = Path("docker/mvp.Dockerfile").read_text(encoding="utf-8")

    assert (
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
        "47534e27c9851bb1128ccc0102f1145e27f23f98/"
        "models/person_detection_mediapipe/person_detection_mediapipe_2023mar.onnx"
    ) in dockerfile
    assert "sha384sum --check" in dockerfile
    assert (
        "cdc21e3741c46ae24e4d2fa3c368886bd7dadcd23d98b6acdc0db966d2d9ecc"
        "5624c095fa05d5f949cce69ef1029f9ef"
    ) in dockerfile
    assert "person-detection-0200" not in dockerfile
    assert 'ENTRYPOINT ["daihougou"]' in dockerfile
```

- [ ] **Step 3: 确认检测器和镜像测试失败**

Run: `pytest tests/app/vision/test_person_detector.py tests/app/test_mvp_dockerfile.py -q`

Expected: FAIL，新模块和 Dockerfile 不存在。

- [ ] **Step 4: 实现 OpenCV DNN 适配器**

```python
# src/daihougou/vision/person_detector.py
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
import numpy.typing as npt


class Network(Protocol):
    def setInput(self, blob: npt.NDArray[np.float32]) -> None: ...

    def getUnconnectedOutLayersNames(self) -> tuple[str, ...]: ...

    def forward(
        self, output_names: tuple[str, ...]
    ) -> tuple[npt.NDArray[np.float32], ...]: ...


@dataclass(frozen=True)
class PersonDetection:
    person_present: bool
    confidence: float
    latency_ms: int


class PersonDetector:
    def __init__(
        self,
        model: Path,
        threshold: float = 0.55,
        net: Network | None = None,
    ) -> None:
        if not 0 < threshold < 1:
            raise ValueError("threshold must be between 0 and 1")
        self._threshold = threshold
        self._net = net if net is not None else cv2.dnn.readNetFromONNX(str(model))
        if net is None:
            self._net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self._net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self._output_names = tuple(self._net.getUnconnectedOutLayersNames())

    def detect(self, frame: npt.NDArray[np.uint8]) -> PersonDetection:
        started = time.monotonic()
        height, width = frame.shape[:2]
        scale = min(224 / width, 224 / height)
        resized_width = max(1, int(width * scale))
        resized_height = max(1, int(height * scale))
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        normalized = rgb.astype(np.float32) / 255.0 * 2.0 - 1.0
        resized = cv2.resize(normalized, (resized_width, resized_height))
        horizontal_padding = 224 - resized_width
        vertical_padding = 224 - resized_height
        padded = cv2.copyMakeBorder(
            resized,
            vertical_padding // 2,
            vertical_padding - vertical_padding // 2,
            horizontal_padding // 2,
            horizontal_padding - horizontal_padding // 2,
            cv2.BORDER_CONSTANT,
            value=(0, 0, 0),
        )
        blob = np.ascontiguousarray(padded.transpose(2, 0, 1)[None])
        self._net.setInput(blob)
        outputs = self._net.forward(self._output_names)
        logits = np.asarray(outputs[1], dtype=np.float64)
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(logits, -100.0, 100.0)))
        confidence = float(probabilities.max()) if probabilities.size else 0.0
        return PersonDetection(
            person_present=confidence >= self._threshold,
            confidence=confidence,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
```

- [ ] **Step 5: 创建下载并校验官方模型的生产镜像**

```dockerfile
# docker/mvp.Dockerfile
FROM python:3.12.11-slim-bookworm

ARG MODEL_URL=https://media.githubusercontent.com/media/opencv/opencv_zoo/47534e27c9851bb1128ccc0102f1145e27f23f98/models/person_detection_mediapipe/person_detection_mediapipe_2023mar.onnx
ARG MODEL_SHA384=cdc21e3741c46ae24e4d2fa3c368886bd7dadcd23d98b6acdc0db966d2d9ecc5624c095fa05d5f949cce69ef1029f9ef

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .

RUN mkdir -p /opt/daihougou/models \
    && curl --fail --silent --show-error --location \
      "$MODEL_URL" \
      --output /opt/daihougou/models/person_detection_mediapipe_2023mar.onnx \
    && cd /opt/daihougou/models \
    && printf '%s  %s\n' "$MODEL_SHA384" person_detection_mediapipe_2023mar.onnx \
      | sha384sum --check

ENTRYPOINT ["daihougou"]
```

模型来源固定为 OpenCV Zoo 提交 `47534e27c9851bb1128ccc0102f1145e27f23f98`，并校验
SHA384 摘要。使用 ONNX 是因为 `opencv-python-headless` 不包含加载 OpenVINO IR 所需的
OpenVINO plugin；该 ONNX 模型直接使用 OpenCV DNN CPU 后端，不需要 OpenVINO Runtime。

- [ ] **Step 6: 运行单元测试**

Run: `pytest tests/app/vision/test_person_detector.py tests/app/test_mvp_dockerfile.py -q`

Expected: `3 passed`。

- [ ] **Step 7: 构建镜像并执行真实模型加载烟雾测试**

Run: `docker build -f docker/mvp.Dockerfile -t daihougou-mvp:test .`

Expected: 构建中的 `sha384sum` 输出 `OK`，最终退出码为 `0`。

Run:

```bash
docker run --rm --entrypoint python daihougou-mvp:test -c '
from pathlib import Path
import numpy as np
from daihougou.vision.person_detector import PersonDetector
detector = PersonDetector(
    Path("/opt/daihougou/models/person_detection_mediapipe_2023mar.onnx"),
)
result = detector.detect(np.zeros((256, 256, 3), dtype=np.uint8))
print("model-inference-ok", result.person_present, result.latency_ms)
'
```

Expected: 输出以 `model-inference-ok False` 开头，退出码为 `0`。

- [ ] **Step 8: 提交**

```bash
git add docker/mvp.Dockerfile \
  src/daihougou/vision/person_detector.py \
  tests/app/test_mvp_dockerfile.py \
  tests/app/vision/test_person_detector.py \
  docs/superpowers/specs/2026-08-28-person-welcome-mvp-design.md \
  docs/superpowers/plans/2026-08-28-person-welcome-mvp.md
git commit -m "feat: detect people with pinned opencv model"
```

### Task 8: 生命周期与完整运行数据流

**Files:**
- Create: `src/daihougou/runtime.py`
- Create: `tests/app/test_runtime.py`

- [ ] **Step 1: 写假帧到假播报的运行时失败测试**

```python
# tests/app/test_runtime.py
import asyncio
import time
from collections import deque
from pathlib import Path

import numpy as np

from daihougou.presence import PresenceTracker
from daihougou.rules import WelcomeRule
from daihougou.runtime import Runtime
from daihougou.speaker import SpeakResult
from daihougou.speaker_worker import SpeakerWorker
from daihougou.storage import Storage
from daihougou.vision.frame_source import FrameSample
from daihougou.vision.person_detector import PersonDetection


class FakeFrameSource:
    def __init__(self, times: list[float]) -> None:
        frame = np.zeros((256, 256, 3), dtype=np.uint8)
        self.samples = deque(
            FrameSample(sequence, captured_at, frame)
            for sequence, captured_at in enumerate(times, start=1)
        )
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def wait_for_frame(self, after_sequence: int, timeout: float) -> FrameSample | None:
        if self.samples:
            return self.samples.popleft()
        time.sleep(0.01)
        return None

    def stop(self) -> None:
        self.stopped = True


class FakeDetector:
    def __init__(self, observations: list[bool]) -> None:
        self.observations = deque(observations)

    def detect(self, frame: np.ndarray) -> PersonDetection:
        present = self.observations.popleft()
        return PersonDetection(present, 0.8 if present else 0.1, 4)


class FakeSpeaker:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> SpeakResult:
        self.spoken.append(text)
        return SpeakResult(True, 12, 0)


def test_runtime_processes_person_entry_and_stops_owned_components(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        storage.set_rule_enabled("welcome_on_person_entry", True)
        source = FakeFrameSource([0, 1, 2, 3, 4])
        speaker = FakeSpeaker()
        worker = SpeakerWorker(speaker, storage)
        runtime = Runtime(
            source,
            FakeDetector([False, False, False, True, True]),
            PresenceTracker(),
            WelcomeRule(storage, chooser=lambda phrases: phrases[0]),
            worker,
            storage,
        )

        await runtime.start()
        deadline = time.monotonic() + 2
        while runtime.snapshot().last_sequence < 5 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        await worker.join()
        snapshot = runtime.snapshot()
        await runtime.stop()

        assert source.started is True
        assert source.stopped is True
        assert snapshot.camera == "ready"
        assert snapshot.detector == "ready"
        assert snapshot.presence == "present"
        assert snapshot.last_sequence == 5
        assert speaker.spoken == ["你好呀，欢迎回来。"]
        assert "presence_changed" in [event.kind for event in storage.recent_events()]

    asyncio.run(scenario())


def test_runtime_skips_failed_inference_instead_of_marking_absent(tmp_path: Path) -> None:
    class BrokenDetector:
        def detect(self, frame: np.ndarray) -> PersonDetection:
            raise RuntimeError("private upstream details")

    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        source = FakeFrameSource([0])
        worker = SpeakerWorker(FakeSpeaker(), storage)
        runtime = Runtime(
            source,
            BrokenDetector(),
            PresenceTracker(),
            WelcomeRule(storage),
            worker,
            storage,
        )
        await runtime.start()
        deadline = time.monotonic() + 1
        while runtime.snapshot().last_sequence < 1 and time.monotonic() < deadline:
            await asyncio.sleep(0.01)
        snapshot = runtime.snapshot()
        await runtime.stop()

        assert snapshot.presence == "unknown"
        assert snapshot.detector == "degraded"
        event = storage.recent_events()[0]
        assert event.kind == "detector_failed"
        assert "private upstream details" not in event.details_json

    asyncio.run(scenario())
```

- [ ] **Step 2: 确认运行时测试失败**

Run: `pytest tests/app/test_runtime.py -q`

Expected: FAIL，包含 `No module named 'daihougou.runtime'`。

- [ ] **Step 3: 实现单一生命周期所有者和健康快照**

```python
# src/daihougou/runtime.py
import asyncio
import time
from dataclasses import dataclass
from typing import Protocol

import numpy as np
import numpy.typing as npt

from daihougou.presence import PresenceTracker
from daihougou.rules import WelcomeRule
from daihougou.speaker_worker import SpeakerWorker
from daihougou.storage import EventRecord, Storage
from daihougou.vision.frame_source import FrameSample
from daihougou.vision.person_detector import PersonDetection


class FrameSource(Protocol):
    def start(self) -> None: ...
    def wait_for_frame(self, after_sequence: int, timeout: float) -> FrameSample | None: ...
    def stop(self) -> None: ...


class Detector(Protocol):
    def detect(self, frame: npt.NDArray[np.uint8]) -> PersonDetection: ...


@dataclass(frozen=True)
class RuntimeSnapshot:
    app: str
    database: str
    camera: str
    detector: str
    speaker_auth: str
    presence: str
    last_sequence: int
    last_confidence: float | None
    last_detection_latency_ms: int | None
    last_error: str

    @property
    def overall(self) -> str:
        required = (self.app, self.database, self.camera, self.detector)
        return "ready" if required == ("running", "ready", "ready", "ready") else "degraded"


class Runtime:
    def __init__(
        self,
        frame_source: FrameSource,
        detector: Detector,
        presence: PresenceTracker,
        welcome_rule: WelcomeRule,
        speaker_worker: SpeakerWorker,
        storage: Storage,
        clock=time.monotonic,
    ) -> None:
        self._frame_source = frame_source
        self._detector = detector
        self._presence = presence
        self._welcome_rule = welcome_rule
        self._speaker_worker = speaker_worker
        self._storage = storage
        self._clock = clock
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._started_at: float | None = None
        self._last_frame_at: float | None = None
        self._last_sequence = 0
        self._last_confidence: float | None = None
        self._last_detection_latency_ms: int | None = None
        self._camera_status = "starting"
        self._camera_error_recorded = False
        self._detector_status = "starting"
        self._database_status = "starting"
        self._app_status = "stopped"
        self._last_error = ""

    async def start(self) -> None:
        if self._task is not None:
            return
        self._storage.initialize()
        self._database_status = "ready"
        await self._speaker_worker.start()
        await asyncio.to_thread(self._frame_source.start)
        self._stop.clear()
        self._started_at = self._clock()
        self._app_status = "running"
        self._task = asyncio.create_task(self._detect_loop(), name="person-detection-loop")

    async def stop(self) -> None:
        self._stop.set()
        await asyncio.to_thread(self._frame_source.stop)
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except TimeoutError:
                self._task.cancel()
                await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        try:
            await asyncio.wait_for(self._speaker_worker.stop(), timeout=35)
        except TimeoutError:
            self._last_error = "speaker_stop_timeout"
        self._app_status = "stopped"

    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            app=self._app_status,
            database=self._database_status,
            camera=self._camera_status,
            detector=self._detector_status,
            speaker_auth=self._speaker_worker.auth_status,
            presence=self._presence.state.value,
            last_sequence=self._last_sequence,
            last_confidence=self._last_confidence,
            last_detection_latency_ms=self._last_detection_latency_ms,
            last_error=self._last_error,
        )

    async def _detect_loop(self) -> None:
        while not self._stop.is_set():
            sample = await asyncio.to_thread(
                self._frame_source.wait_for_frame, self._last_sequence, 2.0
            )
            if sample is None:
                reference = self._last_frame_at or self._started_at
                if reference is not None and self._clock() - reference >= 30:
                    self._camera_status = "degraded"
                    self._last_error = "camera_no_frames"
                    if not self._camera_error_recorded:
                        self._storage.record_event(EventRecord("camera_no_frames", False))
                        self._camera_error_recorded = True
                continue
            await self._process_sample(sample)

    async def _process_sample(self, sample: FrameSample) -> None:
        self._last_sequence = sample.sequence
        self._last_frame_at = self._clock()
        self._camera_status = "ready"
        self._camera_error_recorded = False
        try:
            result = await asyncio.to_thread(self._detector.detect, sample.frame)
        except Exception:
            self._detector_status = "degraded"
            self._last_error = "detector_failed"
            self._storage.record_event(EventRecord("detector_failed", False))
            return
        self._detector_status = "ready"
        self._last_error = ""
        self._last_confidence = result.confidence
        self._last_detection_latency_ms = result.latency_ms
        previous = self._presence.state
        event = self._presence.observe(sample.captured_monotonic, result.person_present)
        if self._presence.state is not previous:
            self._storage.record_event(
                EventRecord(
                    "presence_changed",
                    True,
                    details={
                        "from": previous.value,
                        "to": self._presence.state.value,
                        "confidence": round(result.confidence, 4),
                    },
                )
            )
        if event is not None:
            action = self._welcome_rule.handle(event)
            if action is not None:
                self._speaker_worker.submit(action)
```

- [ ] **Step 4: 运行运行时和已有领域测试**

Run: `pytest tests/app/test_runtime.py tests/app/test_presence.py tests/app/test_rules.py -q`

Expected: 所有测试 PASS。

- [ ] **Step 5: 提交**

```bash
git add src/daihougou/runtime.py tests/app/test_runtime.py
git commit -m "feat: orchestrate person welcome runtime"
```

### Task 9: 一页式管理页面、规则开关和健康检查

**Files:**
- Create: `src/daihougou/web.py`
- Create: `src/daihougou/templates/index.html`
- Create: `src/daihougou/static/app.css`
- Create: `tests/app/test_web.py`

- [ ] **Step 1: 写首页、健康检查和规则开关保护测试**

```python
# tests/app/test_web.py
from pathlib import Path

from fastapi.testclient import TestClient

from daihougou.runtime import RuntimeSnapshot
from daihougou.storage import Storage
from daihougou.web import create_app


class FakeRuntime:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def snapshot(self) -> RuntimeSnapshot:
        return RuntimeSnapshot(
            app="running",
            database="ready",
            camera="degraded",
            detector="starting",
            speaker_auth="unknown",
            presence="unknown",
            last_sequence=0,
            last_confidence=None,
            last_detection_latency_ms=None,
            last_error="camera_no_frames",
        )


def test_home_shows_status_and_disabled_rule(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    runtime = FakeRuntime()

    with TestClient(create_app(storage, runtime, csrf_token="fixed-token")) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "大吼狗" in response.text
    assert "人员进入欢迎" in response.text
    assert "摄像头" in response.text
    assert "已关闭" in response.text
    assert response.cookies["daihougou_csrf"] == "fixed-token"
    assert runtime.started is True
    assert runtime.stopped is True


def test_healthz_reports_degraded_without_failing_web_process(tmp_path: Path) -> None:
    with TestClient(
        create_app(Storage(tmp_path / "app.db"), FakeRuntime(), csrf_token="fixed-token")
    ) as client:
        response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["camera"] == "degraded"


def test_same_origin_form_with_csrf_can_enable_rule(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    app = create_app(storage, FakeRuntime(), csrf_token="fixed-token")

    with TestClient(app) as client:
        client.get("/")
        response = client.post(
            "/rules/welcome_on_person_entry/enable",
            data={"csrf_token": "fixed-token"},
            headers={"Origin": "http://testserver"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert storage.rule_enabled("welcome_on_person_entry") is True


def test_rule_update_rejects_missing_csrf_and_cross_origin(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    app = create_app(storage, FakeRuntime(), csrf_token="fixed-token")

    with TestClient(app) as client:
        client.get("/")
        no_token = client.post(
            "/rules/welcome_on_person_entry/enable",
            headers={"Origin": "http://testserver"},
        )
        wrong_origin = client.post(
            "/rules/welcome_on_person_entry/enable",
            data={"csrf_token": "fixed-token"},
            headers={"Origin": "http://attacker.test"},
        )

    assert no_token.status_code == 403
    assert wrong_origin.status_code == 403
    assert storage.rule_enabled("welcome_on_person_entry") is False
```

- [ ] **Step 2: 确认 Web 测试失败**

Run: `pytest tests/app/test_web.py -q`

Expected: FAIL，包含 `No module named 'daihougou.web'`。

- [ ] **Step 3: 实现 lifespan、同源校验和服务端页面**

```python
# src/daihougou/web.py
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from daihougou.runtime import RuntimeSnapshot
from daihougou.storage import Storage, WELCOME_RULE_ID

PACKAGE_DIR = Path(__file__).parent


class ManagedRuntime(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def snapshot(self) -> RuntimeSnapshot: ...


def create_app(
    storage: Storage,
    runtime: ManagedRuntime,
    csrf_token: str | None = None,
) -> FastAPI:
    token = csrf_token or secrets.token_urlsafe(32)
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="大吼狗", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        response = templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "snapshot": runtime.snapshot(),
                "rule_enabled": storage.rule_enabled(WELCOME_RULE_ID),
                "events": storage.recent_events(limit=8),
                "csrf_token": token,
                "rule_id": WELCOME_RULE_ID,
            },
        )
        response.set_cookie(
            "daihougou_csrf",
            token,
            httponly=True,
            samesite="strict",
            secure=False,
        )
        return response

    @app.post("/rules/{rule_id}/{action}")
    async def update_rule(
        request: Request,
        rule_id: str,
        action: str,
        csrf_token: str = Form(default=""),
    ) -> RedirectResponse:
        origin = request.headers.get("origin", "")
        host = request.headers.get("host", "")
        cookie = request.cookies.get("daihougou_csrf", "")
        if not origin or urlsplit(origin).netloc != host:
            raise HTTPException(status_code=403, detail="cross_origin_request")
        if not cookie or not secrets.compare_digest(cookie, token):
            raise HTTPException(status_code=403, detail="invalid_csrf")
        if not csrf_token or not secrets.compare_digest(csrf_token, token):
            raise HTTPException(status_code=403, detail="invalid_csrf")
        if rule_id != WELCOME_RULE_ID or action not in {"enable", "disable"}:
            raise HTTPException(status_code=404, detail="unknown_rule")
        storage.set_rule_enabled(rule_id, action == "enable")
        return RedirectResponse("/", status_code=303)

    @app.get("/healthz")
    async def healthz() -> dict[str, str | int | float | None]:
        snapshot = runtime.snapshot()
        return {
            "status": snapshot.overall,
            "app": snapshot.app,
            "database": snapshot.database,
            "camera": snapshot.camera,
            "detector": snapshot.detector,
            "speaker_auth": snapshot.speaker_auth,
            "presence": snapshot.presence,
            "last_sequence": snapshot.last_sequence,
        }

    return app
```

- [ ] **Step 4: 创建不依赖前端构建工具的一页式模板**

```html
<!-- src/daihougou/templates/index.html -->
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>大吼狗</title>
    <link rel="stylesheet" href="{{ url_for('static', path='/app.css') }}">
  </head>
  <body>
    <header class="topbar">
      <div>
        <p class="eyebrow">家庭陪护</p>
        <h1>大吼狗</h1>
      </div>
      <span class="overall status status-{{ snapshot.overall }}">{{ snapshot.overall }}</span>
    </header>

    <main>
      <section aria-labelledby="device-status">
        <h2 id="device-status">运行状态</h2>
        <dl class="status-grid">
          <div><dt>应用</dt><dd>{{ snapshot.app }}</dd></div>
          <div><dt>摄像头</dt><dd>{{ snapshot.camera }}</dd></div>
          <div><dt>检测器</dt><dd>{{ snapshot.detector }}</dd></div>
          <div><dt>音箱认证</dt><dd>{{ snapshot.speaker_auth }}</dd></div>
          <div><dt>画面状态</dt><dd>{{ snapshot.presence }}</dd></div>
          <div><dt>检测置信度</dt><dd>{{ '%.2f'|format(snapshot.last_confidence) if snapshot.last_confidence is not none else '—' }}</dd></div>
        </dl>
      </section>

      <section aria-labelledby="rules-title">
        <h2 id="rules-title">规则</h2>
        <div class="rule-row">
          <div>
            <h3>人员进入欢迎</h3>
            <p>{{ '已开启' if rule_enabled else '已关闭' }}</p>
          </div>
          <form method="post" action="/rules/{{ rule_id }}/{{ 'disable' if rule_enabled else 'enable' }}">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
            <label class="switch">
              <span class="sr-only">{{ '关闭' if rule_enabled else '开启' }}人员进入欢迎</span>
              <input type="checkbox" {% if rule_enabled %}checked{% endif %} onchange="this.form.submit()">
              <span class="track" aria-hidden="true"></span>
            </label>
          </form>
        </div>
      </section>

      <section aria-labelledby="events-title">
        <h2 id="events-title">最近记录</h2>
        <div class="table-wrap">
          <table>
            <thead><tr><th>时间</th><th>事件</th><th>结果</th><th>耗时</th></tr></thead>
            <tbody>
              {% for event in events %}
              <tr>
                <td>{{ event.occurred_at[:19].replace('T', ' ') }}</td>
                <td>{{ event.kind }}</td>
                <td>{{ '成功' if event.success else '未完成' }}</td>
                <td>{{ event.latency_ms ~ ' ms' if event.latency_ms is not none else '—' }}</td>
              </tr>
              {% else %}
              <tr><td colspan="4" class="empty">暂无记录</td></tr>
              {% endfor %}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  </body>
</html>
```

- [ ] **Step 5: 创建稳定尺寸和移动端样式**

```css
/* src/daihougou/static/app.css */
:root {
  color-scheme: light;
  font-family: Inter, "Noto Sans SC", system-ui, sans-serif;
  color: #202124;
  background: #f4f5f6;
}

* { box-sizing: border-box; }
body { margin: 0; min-width: 320px; }
.topbar {
  min-height: 96px;
  padding: 20px max(24px, calc((100% - 1040px) / 2));
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: #fff;
  background: #24272a;
}
.eyebrow { margin: 0 0 4px; color: #aeb4b9; font-size: 13px; }
h1, h2, h3, p { margin-top: 0; }
h1 { margin-bottom: 0; font-size: 26px; letter-spacing: 0; }
h2 { margin-bottom: 16px; font-size: 18px; letter-spacing: 0; }
h3 { margin-bottom: 6px; font-size: 16px; letter-spacing: 0; }
main { max-width: 1040px; margin: 0 auto; padding: 30px 24px 56px; }
section { padding: 24px 0; border-bottom: 1px solid #d9dde1; }
.status { min-width: 82px; padding: 7px 10px; border-radius: 6px; text-align: center; }
.status-ready { color: #0f5132; background: #d8f3e5; }
.status-degraded { color: #664d03; background: #fff0bf; }
.status-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); margin: 0; border: 1px solid #d9dde1; border-radius: 6px; background: #fff; }
.status-grid div { min-height: 78px; padding: 16px; border-right: 1px solid #e6e8ea; border-bottom: 1px solid #e6e8ea; }
.status-grid div:nth-child(3n) { border-right: 0; }
.status-grid div:nth-last-child(-n+3) { border-bottom: 0; }
dt { color: #656b70; font-size: 13px; }
dd { margin: 8px 0 0; font-weight: 650; overflow-wrap: anywhere; }
.rule-row { min-height: 76px; display: flex; align-items: center; justify-content: space-between; gap: 20px; }
.rule-row p { margin-bottom: 0; color: #656b70; font-size: 14px; }
.switch { position: relative; display: block; width: 48px; height: 28px; }
.switch input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.track { position: absolute; inset: 0; border-radius: 14px; background: #8a9095; cursor: pointer; transition: background 140ms ease; }
.track::after { content: ""; position: absolute; top: 4px; left: 4px; width: 20px; height: 20px; border-radius: 50%; background: #fff; transition: transform 140ms ease; }
.switch input:checked + .track { background: #16784a; }
.switch input:checked + .track::after { transform: translateX(20px); }
.switch input:focus-visible + .track { outline: 3px solid #78a8e8; outline-offset: 3px; }
.table-wrap { overflow-x: auto; border: 1px solid #d9dde1; border-radius: 6px; background: #fff; }
table { width: 100%; min-width: 620px; border-collapse: collapse; }
th, td { padding: 12px 14px; border-bottom: 1px solid #e6e8ea; text-align: left; font-size: 14px; }
th { color: #555b60; background: #f8f9fa; font-size: 12px; }
tbody tr:last-child td { border-bottom: 0; }
.empty { height: 72px; color: #656b70; text-align: center; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
@media (max-width: 700px) {
  .topbar { min-height: 84px; padding: 16px 18px; }
  main { padding: 18px 18px 40px; }
  .status-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .status-grid div:nth-child(3n) { border-right: 1px solid #e6e8ea; }
  .status-grid div:nth-child(2n) { border-right: 0; }
  .status-grid div:nth-last-child(-n+3) { border-bottom: 1px solid #e6e8ea; }
  .status-grid div:nth-last-child(-n+2) { border-bottom: 0; }
}
```

- [ ] **Step 6: 运行 Web 测试和模板静态检查**

Run: `pytest tests/app/test_web.py -q`

Expected: `4 passed`。

Run: `ruff check src/daihougou/web.py tests/app/test_web.py`

Expected: `All checks passed!`

- [ ] **Step 7: 提交**

```bash
git add src/daihougou/web.py src/daihougou/templates src/daihougou/static tests/app/test_web.py
git commit -m "feat: add rule management page"
```

### Task 10: 生产装配、Compose app 服务和健康探针

**Files:**
- Create: `src/daihougou/main.py`
- Create: `src/daihougou/healthcheck.py`
- Create: `.env.mvp.example`
- Create: `tests/app/test_compose_mvp.py`
- Modify: `compose.poc.yaml`

- [ ] **Step 1: 写单 worker、状态挂载、共享 HOME 和健康检查失败测试**

```python
# tests/app/test_compose_mvp.py
from pathlib import Path


def test_compose_runs_one_app_with_persistent_data_and_miservice_home() -> None:
    compose = Path("compose.poc.yaml").read_text(encoding="utf-8")

    assert "dockerfile: docker/mvp.Dockerfile" in compose
    assert "container_name: daihougou-app" in compose
    assert "./deploy/app/state:/var/lib/daihougou/data" in compose
    assert "./deploy/miservice/state:/var/lib/daihougou/mi" in compose
    assert "HOME: /var/lib/daihougou/mi" in compose
    assert "python -m daihougou.healthcheck" in compose
    assert "--workers" not in compose
    assert "--reload" not in compose


def test_mvp_environment_example_contains_no_real_credentials() -> None:
    example = Path(".env.mvp.example").read_text(encoding="utf-8")

    assert "MI_USER=" in example
    assert "MI_PASS=" in example
    assert "MI_DID=" in example
    assert "rtsp://127.0.0.1:8554/xiaobai" in example
    assert "owner@example" not in example
    assert "123456789" not in example
```

- [ ] **Step 2: 确认 Compose 测试失败**

Run: `pytest tests/app/test_compose_mvp.py -q`

Expected: FAIL，app 服务、环境模板与健康探针尚不存在。

- [ ] **Step 3: 实现生产对象装配和唯一应用入口**

```python
# src/daihougou/main.py
import os

import uvicorn
from fastapi import FastAPI

from daihougou.presence import PresenceTracker
from daihougou.rules import WelcomeRule
from daihougou.runtime import Runtime
from daihougou.settings import Settings
from daihougou.speaker import DirectSpeaker
from daihougou.speaker_worker import SpeakerWorker
from daihougou.storage import Storage
from daihougou.vision.frame_source import FfmpegFrameSource
from daihougou.vision.person_detector import PersonDetector
from daihougou.web import create_app


def create_production_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_mapping(os.environ)
    storage = Storage(resolved.data_dir / "daihougou.db")
    speaker_worker = SpeakerWorker(
        DirectSpeaker(resolved.mi_user, resolved.mi_pass, resolved.mi_did),
        storage,
    )
    runtime = Runtime(
        FfmpegFrameSource(resolved.stream_url, resolved.detection_fps),
        PersonDetector(resolved.model, resolved.person_threshold),
        PresenceTracker(resolved.leave_seconds),
        WelcomeRule(storage, resolved.welcome_cooldown_seconds),
        speaker_worker,
        storage,
    )
    return create_app(storage, runtime)


def run() -> None:
    settings = Settings.from_mapping(os.environ)
    uvicorn.run(
        create_production_app(settings),
        host=settings.web_host,
        port=settings.web_port,
        workers=1,
        access_log=False,
    )
```

- [ ] **Step 4: 实现不依赖 curl 的容器健康探针**

```python
# src/daihougou/healthcheck.py
import json
import os
from urllib.request import urlopen


def main() -> None:
    port = int(os.environ.get("WEB_PORT", "8080"))
    with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as response:
        body = json.load(response)
    if response.status != 200 or body.get("status") not in {"ready", "degraded"}:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: 创建无秘密 MVP 环境模板**

```dotenv
# .env.mvp.example
MI_USER=
MI_PASS=
MI_DID=
STREAM_URL=rtsp://127.0.0.1:8554/xiaobai
DATA_DIR=/var/lib/daihougou/data
MODEL=/opt/daihougou/models/person_detection_mediapipe_2023mar.onnx
DETECTION_FPS=1.0
PERSON_THRESHOLD=0.55
LEAVE_SECONDS=10.0
WELCOME_COOLDOWN_SECONDS=60.0
WEB_HOST=0.0.0.0
WEB_PORT=8080
```

- [ ] **Step 6: 在 Compose 增加常驻 app 服务**

在 `go2rtc` 和 `probe` 之间加入：

```yaml
  app:
    build:
      context: .
      dockerfile: docker/mvp.Dockerfile
    image: daihougou-mvp:local
    container_name: daihougou-app
    network_mode: host
    restart: unless-stopped
    depends_on:
      go2rtc:
        condition: service_started
    env_file: .env.mvp
    environment:
      HOME: /var/lib/daihougou/mi
      PYTHONUNBUFFERED: "1"
    volumes:
      - ./deploy/app/state:/var/lib/daihougou/data
      - ./deploy/miservice/state:/var/lib/daihougou/mi
    healthcheck:
      test: ["CMD", "python", "-m", "daihougou.healthcheck"]
      interval: 15s
      timeout: 5s
      retries: 3
      start_period: 45s
```

不为 app 设置 profile；普通的 `docker compose up -d` 就应启动 go2rtc 与 app。`probe` 和
`homeassistant` 的已有 profile 保持不变。

- [ ] **Step 7: 运行静态测试和生产入口导入测试**

Run: `pytest tests/app/test_compose_mvp.py tests/app/test_mvp_dockerfile.py -q`

Expected: 所有测试 PASS。

Run:

```bash
MI_USER=test MI_PASS=test MI_DID=test python -c '
from daihougou.main import create_production_app
app = create_production_app
print(app.__name__)
'
```

Expected: 输出 `create_production_app`；该命令不构造模型、不连接设备。

- [ ] **Step 8: 用忽略的本地环境文件验证 Compose 解析**

Run:

```bash
test -e .env.mvp || cp .env.mvp.example .env.mvp
docker compose -f compose.poc.yaml config --quiet
```

Expected: 无输出，退出码为 `0`。

保留被 Git 忽略的 `.env.mvp` 供后续本地验证。命令不会覆盖用户已有的真实文件。

- [ ] **Step 9: 提交**

```bash
git add .env.mvp.example compose.poc.yaml src/daihougou/main.py src/daihougou/healthcheck.py tests/app/test_compose_mvp.py
git commit -m "feat: deploy single-worker mvp service"
```

### Task 11: 容器级端到端测试与中文 runbook

**Files:**
- Create: `tests/app/test_mvp_container.py`
- Create: `tests/app/test_generated_video_pipeline.py`
- Modify: `docker/mvp.Dockerfile`
- Modify: `docs/poc-runbook.md`

- [ ] **Step 1: 写镜像必须携带测试及完整领域链路的策略测试**

```python
# tests/app/test_mvp_container.py
from pathlib import Path


def test_mvp_dockerfile_contains_test_suite_for_offline_container_verification() -> None:
    dockerfile = Path("docker/mvp.Dockerfile").read_text(encoding="utf-8")

    assert "COPY tests ./tests" in dockerfile
    assert "pip install --no-cache-dir '.[dev]'" in dockerfile
```

- [ ] **Step 2: 确认镜像测试失败**

Run: `pytest tests/app/test_mvp_container.py -q`

Expected: FAIL，生产镜像当前没有测试文件和 dev 依赖。

- [ ] **Step 3: 为本轮 MVP 验证把测试复制进镜像**

把 `docker/mvp.Dockerfile` 的复制与安装段改为：

```dockerfile
COPY pyproject.toml ./
COPY src ./src
COPY tests ./tests
RUN pip install --no-cache-dir '.[dev]'
```

MVP 首轮保留测试工具以便目标 Debian 机器离线复验。稳定后若需要缩小镜像，再单独设计
multi-stage runtime image，不能在本任务顺手删除可重复验证能力。

- [ ] **Step 4: 写真实 FFmpeg 生成视频到假音箱的集成测试**

```python
# tests/app/test_generated_video_pipeline.py
import asyncio
import shutil
import subprocess
import time
from pathlib import Path

import numpy as np
import pytest

from daihougou.presence import PresenceTracker
from daihougou.rules import WelcomeRule
from daihougou.runtime import Runtime
from daihougou.speaker import SpeakResult
from daihougou.speaker_worker import SpeakerWorker
from daihougou.storage import Storage
from daihougou.vision.frame_source import FfmpegFrameSource
from daihougou.vision.person_detector import PersonDetection

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")


def generated_video_process(_command: list[str]) -> subprocess.Popen[bytes]:
    generated_command = [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-re",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=256x256:r=1:d=6",
        "-vf",
        "drawbox=enable='gte(t,3)':x=0:y=0:w=iw:h=ih:color=white:t=fill,format=bgr24",
        "-frames:v",
        "6",
        "-threads",
        "1",
        "-f",
        "rawvideo",
        "pipe:1",
    ]
    return subprocess.Popen(
        generated_command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )


class BrightnessDetector:
    def detect(self, frame: np.ndarray) -> PersonDetection:
        present = float(frame.mean()) > 127
        return PersonDetection(present, 0.9 if present else 0.1, 1)


class FakeSpeaker:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> SpeakResult:
        self.spoken.append(text)
        return SpeakResult(True, 1, 0)


def test_generated_video_reaches_one_welcome_action(tmp_path: Path) -> None:
    async def scenario() -> None:
        storage = Storage(tmp_path / "app.db")
        storage.initialize()
        storage.set_rule_enabled("welcome_on_person_entry", True)
        source = FfmpegFrameSource("ignored", process_factory=generated_video_process)
        speaker = FakeSpeaker()
        worker = SpeakerWorker(speaker, storage)
        runtime = Runtime(
            source,
            BrightnessDetector(),
            PresenceTracker(),
            WelcomeRule(storage, chooser=lambda phrases: phrases[0]),
            worker,
            storage,
        )

        await runtime.start()
        deadline = time.monotonic() + 10
        while not speaker.spoken and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await runtime.stop()

        assert speaker.spoken == ["你好呀，欢迎回来。"]
        assert any(event.kind == "presence_changed" for event in storage.recent_events())

    asyncio.run(scenario())
```

这个测试使用 FFmpeg `lavfi` 生成前三帧黑色、后三帧白色的视频，以亮度检测器代替真实模型，
但经过真实 rawvideo 管道、最新帧读取、状态机、规则、队列、SQLite 和假音箱。真实模型另由
Task 7 的固定张量解析测试与容器模型加载/推理烟雾测试覆盖。

- [ ] **Step 5: 运行生成视频集成测试**

Run: `pytest tests/app/test_generated_video_pipeline.py -q`

Expected: `1 passed`，耗时约 5 至 8 秒；不能出现线程或 FFmpeg 资源警告。

- [ ] **Step 6: 在 runbook 开头更新当前范围说明**

在 `docs/poc-runbook.md` 标题后加入：

```markdown
> 本手册同时覆盖设备兼容性 PoC 和首个“人员进入欢迎”MVP。PoC 的 30 分钟摄像头与
> 30 次音箱稳定性步骤仍可稍后执行；已经确认 `xiaobai` 拉流和 L05C 直连播报可用时，
> 可以先按第 14 节启动 MVP。扩大日常使用范围前仍须补完第 6、8、10 和 11 节的稳定性验收。
```

并把第 3 节标题改成“启动 go2rtc 并配置摄像头”。在该节两台摄像头步骤前说明：当前 MVP
只要求 `xiaobai`；如果现在只接入一台摄像头，所有 `xiaobai_25k` 操作可以跳过，且不会阻塞
第 14 节。保留两摄像头 PoC 内容供后续恢复。

- [ ] **Step 7: 在 runbook 增加 Token 生命周期与首次认证章节**

在第 13 节之后、现有“产物隐私警告”之前追加以下完整章节：

````markdown
## 14. 启动“人员进入欢迎”MVP

### 14.1 创建私有状态目录和环境文件

MVP 继续使用第 3 节已经验证的 `xiaobai`。先创建不会进入 Git 或 Docker 构建上下文的状态
目录：

```bash
mkdir -p deploy/app/state deploy/miservice/state
chmod 700 deploy/app/state deploy/miservice/state
test -e .env.mvp || cp .env.mvp.example .env.mvp
chmod 600 .env.mvp
```

如果 `.env.mvp` 已存在，不要再次执行 `cp`。在服务器本地填写 `MI_USER`、`MI_PASS` 和
`MI_DID`；它们必须与 `.env.poc` 中已经通过直连播报的同一账号和 L05C 数值 DID 一致。
不要在终端输出这三个值。其余参数先保留默认值。

### 14.2 持久化 MiService 登录 Token

MiService 把登录状态写入 `$HOME/.mi.token`。Compose 已将 probe 和 app 的 `HOME` 都设为
`/var/lib/daihougou/mi`，并把宿主机 `deploy/miservice/state/` 挂载到该位置。这样
`docker compose run --rm` 删除临时容器时不会再删除 Token。

先重建包含该挂载的 probe，然后在有 TTY 的终端完成一次登录；小米要求时输入手机验证码：

```bash
docker compose -f compose.poc.yaml --profile tools build probe
docker compose -f compose.poc.yaml --profile tools run --rm \
  --entrypoint python probe -m miservice list
```

不要保存或粘贴设备列表。确认 Token 已持久化并收紧权限：

```bash
sudo test -s deploy/miservice/state/.mi.token
sudo chmod 600 deploy/miservice/state/.mi.token
sudo stat -c '%a %n' deploy/miservice/state deploy/miservice/state/.mi.token
```

预期两行权限分别为 `700` 和 `600`。随后再次运行同一条 `miservice list` 命令；正常情况下
不再要求手机验证码。若仍要求验证码，先执行下面的挂载检查，不要重复登录：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm \
  --entrypoint sh probe -c 'printf "HOME=%s\n" "$HOME"; test -s "$HOME/.mi.token"'
```

预期输出 `HOME=/var/lib/daihougou/mi` 且退出码为 `0`。不要执行 `cat .mi.token`。

### 14.3 构建和启动

```bash
docker compose -f compose.poc.yaml build app
docker compose -f compose.poc.yaml up -d go2rtc app
docker compose -f compose.poc.yaml ps go2rtc app
docker compose -f compose.poc.yaml logs --tail=50 app
```

app 首次加载模型和等待摄像头帧时可以短暂显示 `health: starting`。60 秒后检查：

```bash
curl --fail --silent --show-error http://127.0.0.1:8080/healthz \
  | python3 -m json.tool
```

摄像头在线时 `camera` 和 `detector` 应最终为 `ready`。摄像头关闭或临时断线时 HTTP 仍返回
成功，但 `status`/`camera` 为 `degraded`；这表示管理进程还活着，不应通过反复重启 app
来掩盖摄像头问题。

从局域网浏览器打开 `http://SERVER_IP:8080/`。确认显示的是 `xiaobai` 对应的运行状态，且
“人员进入欢迎”初始为“已关闭”。页面不需要 Home Assistant。

### 14.4 认证失效处理

常驻 app 不读取 stdin，也不会等待手机验证码。管理页显示音箱认证
`reauth_required` 时，使用 14.2 的一次性 probe 命令重新完成认证，然后执行：

```bash
sudo chmod 600 deploy/miservice/state/.mi.token
docker compose -f compose.poc.yaml restart app
```

不需要重新构建镜像。重启后等待健康检查，再从管理页继续操作。不要删除 Token 作为普通
排障手段；只有确认登录状态损坏并准备立即重新认证时，才人工备份后处理该文件。

### 14.5 验证重建后不再要求验证码

先在成年人在场时完成一次第 8.1 节的单次播报，再连续重建 probe 和 app 容器：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm \
  --entrypoint python probe -m miservice list >/dev/null
docker compose -f compose.poc.yaml up -d --force-recreate app
docker compose -f compose.poc.yaml ps app
```

第一条命令不应要求验证码；第二条完成后 Token 文件仍存在，app 最终恢复健康。设备列表被
重定向到 `/dev/null`，避免把家庭设备信息写进 shell 记录或报告。

### 14.6 功能验收

1. 保持规则关闭，先让画面稳定无人 10 秒，再有人进入；音箱不得播报。
2. 在管理页开启规则。若此时画面已经有人，启动校准不得立即播报。
3. 画面无人至少 10 秒，然后有人正常走入；从进入到听到欢迎语应不超过 5 秒，且只播一次。
4. 人持续留在画面 2 分钟；不得重复播报。
5. 人离开至少 10 秒，并等待上次播报 60 秒冷却结束；再次进入后应播报第二次。
6. 关闭摄像头；页面应在 30 秒后显示摄像头降级，音箱不得误播。重新打开摄像头后应自动恢复。
7. 检查应用状态目录只包含 SQLite 及其 WAL/SHM 文件，不得出现图片、视频或音频：

```bash
find deploy/app/state -maxdepth 1 -type f -printf '%f\n'
```

完成上述功能后保持 app 连续运行 30 分钟，期间至少完成三次“离开后再次进入”。记录是否有
漏报、误报、重复播报和超过 5 秒的响应。这个 30 分钟结果用于 MVP 开发验收，不替代第 6、
8 和 10 节尚未完成的长期稳定性测试。

### 14.7 停止与升级

停止 MVP 但保留 go2rtc：

```bash
docker compose -f compose.poc.yaml stop app
```

升级代码后只需重建 app；数据库和登录 Token 都保留在宿主机：

```bash
docker compose -f compose.poc.yaml build app
docker compose -f compose.poc.yaml up -d app
```
````

- [ ] **Step 8: 构建并在容器内运行无设备测试**

Run: `docker build -f docker/mvp.Dockerfile -t daihougou-mvp:test .`

Expected: 镜像构建成功且模型摘要检查为 `OK`。

Run:

```bash
docker run --rm \
  --mount type=bind,source="$PWD/.dockerignore",target=/workspace/.dockerignore,readonly \
  --mount type=bind,source="$PWD/compose.poc.yaml",target=/workspace/compose.poc.yaml,readonly \
  --entrypoint pytest daihougou-mvp:test -q
```

Expected: 全部测试 PASS；不得尝试访问实际摄像头或小米账号。

- [ ] **Step 9: 提交**

```bash
git add docker/mvp.Dockerfile docs/poc-runbook.md tests/app/test_mvp_container.py tests/app/test_generated_video_pipeline.py
git commit -m "docs: add mvp deployment and acceptance runbook"
```

### Task 12: 最终回归、界面验证和交付检查

**Files:**
- Modify only if a verification failure exposes a defect in files listed above.
- Do not commit screenshots, `.env` files, SQLite files, MiService Token, model downloads, or camera frames.

- [ ] **Step 1: 运行本机全量测试与代码检查**

Run: `pytest -q`

Expected: 全部测试 PASS。

Run: `ruff check src tests`

Expected: `All checks passed!`

- [ ] **Step 2: 运行 PoC 镜像回归**

Run: `docker build -f docker/poc.Dockerfile -t daihougou-poc:test .`

Expected: 构建成功。

Run:

```bash
docker run --rm \
  --mount type=bind,source="$PWD/.dockerignore",target=/workspace/.dockerignore,readonly \
  --mount type=bind,source="$PWD/compose.poc.yaml",target=/workspace/compose.poc.yaml,readonly \
  --entrypoint pytest daihougou-poc:test -q
```

Expected: MVP 与原 PoC 的全部测试 PASS，不再出现仓库根文件 `FileNotFoundError`。

- [ ] **Step 3: 验证 Compose 展开结果不泄密且只有一个 app 进程**

确保本地存在按 `.env.mvp.example` 创建的忽略文件后运行：

```bash
docker compose -f compose.poc.yaml config --quiet
docker compose -f compose.poc.yaml config --services
```

Expected: 解析成功，普通服务列表包含 `go2rtc` 和 `app`；不要把完整 `docker compose config`
输出保存或粘贴，因为展开结果会含真实环境变量。

- [ ] **Step 4: 在无真实设备模式启动管理页进行视觉验证**

用独立容器名和测试凭据启动，不挂载真实 MiService 状态：

```bash
docker run --rm -d --name daihougou-ui-check \
  -p 18080:8080 \
  -e MI_USER=ui-check \
  -e MI_PASS=ui-check \
  -e MI_DID=ui-check \
  -e STREAM_URL=rtsp://127.0.0.1:1/unavailable \
  daihougou-mvp:test
```

等待 `http://127.0.0.1:18080/healthz` 返回 HTTP 200。使用浏览器分别在 `1440x900` 和
`390x844` 查看 `http://127.0.0.1:18080/`，确认：

- 标题、六项状态、规则开关和最近记录均可见；
- 没有横向页面滚动、文本重叠、溢出或布局跳动；
- 移动端状态网格为两列，记录表只在自身容器内横向滚动；
- 开关可以用鼠标和键盘操作，焦点清晰；
- 网络请求中模板、CSS 和 `/healthz` 都返回成功。

保持该容器运行，并在交付时提供 `http://127.0.0.1:18080/` 供用户查看。用户确认页面后再运行
`docker stop daihougou-ui-check`；如果容器已意外停止，只记录分类后的错误，不粘贴可能含
环境或地址的原始输出。

- [ ] **Step 5: 在目标 Debian 服务器执行 runbook 14.1 至 14.6**

Expected:

- Token 在 probe/app 重建后保留且正常情况不再要求 OTP；
- 管理页默认关闭欢迎规则；
- `absent -> present` 在 5 秒内播报一次，持续在场不重复；
- 摄像头断线时管理页降级但进程不退出、不误播；
- 30 分钟功能验收完成，所有存储文件都不是媒体文件。

- [ ] **Step 6: 检查敏感文件和工作树**

Run:

```bash
git status --short
git ls-files | rg '(^|/)(\.env\.mvp|\.mi\.token|daihougou\.db|deploy/(app|miservice)/state)'
```

Expected: 第二条命令无输出；第一条只显示有意修改且准备提交的源代码、测试或文档。

- [ ] **Step 7: 确认没有未提交的验证修复**

Run: `git status --short`

Expected: 无输出。若前面的验证失败，不执行本步骤；回到拥有该行为的任务，先增加或收紧那个
任务列出的测试，完成修复并使用该任务列出的明确 `git add` 文件清单提交，然后从 Step 1
重新运行最终回归。不要使用 `git add .`。

## 完成定义

- 所有自动测试、Ruff、两个 Docker 构建和 Compose 解析通过。
- 真实 ONNX 模型在目标 CPU 镜像中可加载，只使用 OpenCV DNN CPU 后端且不依赖 OpenVINO。
- `xiaobai` 只保留最新帧，断线自动退避恢复，错误帧不推动“离开”状态。
- 欢迎规则默认关闭；开启后只在稳定的 `absent -> present` 且冷却结束时产生动作。
- 音箱调用不阻塞检测循环，执行前再次检查开关，OTP 失效只产生脱敏的
  `reauth_required` 状态。
- 管理页可在桌面和移动端操作，没有认证系统、实时视频或配置编辑器。
- MiService Token 和 SQLite 状态在容器重建后保留，但不进入 Git/Docker 构建上下文。
- 中文 runbook 能从首次认证引导到启动、恢复、升级和 30 分钟物理验收。
- 8 小时摄像头、100 次音箱与资源压力测试明确保留为扩大使用范围前的后续门槛。
