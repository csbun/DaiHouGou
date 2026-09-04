from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from guduck.detection_region import DetectionRegion
from guduck.redaction import redact
from guduck.rules import (
    BUILTIN_RULE_IDS,
    OBJECT_RULE_ID,
    WELCOME_PHRASES,
    WELCOME_RULE_ID,
)
from guduck.settings import ObjectDetectorAdapter

SCHEMA_VERSION = 4
_SCHEMA_TABLES = frozenset({"camera_rules", "cameras", "events", "rule_configs", "xiaomi_account", "speaker_bindings"})
_CAMERA_COLUMNS = frozenset(
    {
        "stream_id",
        "speaker_id",
        "first_seen_at",
        "last_seen_at",
        "region_x",
        "region_y",
        "region_width",
        "region_height",
    }
)
_V2_TABLE_DEFINITIONS = {
    "cameras": "CREATE TABLE cameras ( stream_id TEXT PRIMARY KEY, speaker_id TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL )",
    "camera_rules": "CREATE TABLE camera_rules ( camera_id TEXT NOT NULL REFERENCES cameras(stream_id), rule_id TEXT NOT NULL, enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)), updated_at TEXT NOT NULL, PRIMARY KEY (camera_id, rule_id) )",
    "rule_configs": "CREATE TABLE rule_configs ( rule_id TEXT PRIMARY KEY, config_json TEXT NOT NULL, updated_at TEXT NOT NULL )",
    "events": "CREATE TABLE events ( id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at TEXT NOT NULL, kind TEXT NOT NULL, rule_id TEXT, camera_id TEXT, speaker_id TEXT, success INTEGER NOT NULL CHECK (success IN (0, 1)), latency_ms INTEGER, details_json TEXT NOT NULL )",
}
_V3_TABLE_DEFINITIONS = {
    **_V2_TABLE_DEFINITIONS,
    "cameras": "CREATE TABLE cameras ( stream_id TEXT PRIMARY KEY, speaker_id TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, region_x REAL NOT NULL DEFAULT 0, region_y REAL NOT NULL DEFAULT 0, region_width REAL NOT NULL DEFAULT 1, region_height REAL NOT NULL DEFAULT 1 )",
}
_V4_TABLE_DEFINITIONS = {
    **_V3_TABLE_DEFINITIONS,
    "cameras": "CREATE TABLE cameras ( stream_id TEXT PRIMARY KEY, speaker_id TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, region_x REAL NOT NULL DEFAULT 0, region_y REAL NOT NULL DEFAULT 0, region_width REAL NOT NULL DEFAULT 1, region_height REAL NOT NULL DEFAULT 1 )",
    "xiaomi_account": "CREATE TABLE xiaomi_account ( id INTEGER PRIMARY KEY CHECK (id = 1), username TEXT NOT NULL, token_json TEXT NOT NULL, updated_at TEXT NOT NULL )",
    "speaker_bindings": "CREATE TABLE speaker_bindings ( binding_id TEXT PRIMARY KEY, device_id TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL, mina_name TEXT NOT NULL, hardware TEXT NOT NULL, miot_did TEXT, last_seen_at TEXT, bound INTEGER NOT NULL CHECK (bound IN (0, 1)), available INTEGER NOT NULL CHECK (available IN (0, 1)), test_status TEXT NOT NULL, updated_at TEXT NOT NULL )",
}
_LEGACY_WELCOME_PHRASES = (
    "你好呀，欢迎回来。",
    "嗨，很高兴见到你。",
    "欢迎你，今天也要开心呀。",
)


class IncompatibleSchemaError(RuntimeError):
    pass


@dataclass(frozen=True)
class CameraConfig:
    stream_id: str
    speaker_id: str | None
    first_seen_at: str
    last_seen_at: str
    detection_region: DetectionRegion


@dataclass(frozen=True)
class EventRecord:
    kind: str
    success: bool
    rule_id: str | None = None
    camera_id: str | None = None
    speaker_id: str | None = None
    latency_ms: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StoredEvent:
    id: int
    occurred_at: str
    kind: str
    rule_id: str | None
    camera_id: str | None
    speaker_id: str | None
    success: bool
    latency_ms: int | None
    details_json: str

    @property
    def details(self) -> dict[str, Any]:
        try:
            details = json.loads(self.details_json)
        except (TypeError, json.JSONDecodeError):
            return {}
        return details if isinstance(details, dict) else {}

    @property
    def spoken_text(self) -> str | None:
        text = self.details.get("text")
        return text if isinstance(text, str) and text else None

    @property
    def details_display(self) -> str:
        if not self.details:
            return ""
        return json.dumps(self.details, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class XiaomiAccount:
    id: int
    username: str
    token_json: str
    updated_at: str

@dataclass(frozen=True)
class DiscoveredSpeaker:
    device_id: str
    mina_name: str
    hardware: str
    miot_did: str | None

@dataclass(frozen=True)
class SpeakerBinding:
    binding_id: str
    device_id: str
    display_name: str
    mina_name: str
    hardware: str
    miot_did: str | None
    last_seen_at: str | None
    bound: bool
    available: bool
    test_status: str
    updated_at: str

@dataclass(frozen=True)
class BindingSaveResult:
    saved: bool
    confirmation_id: str | None
    affected_camera_ids: tuple[str, ...]

@dataclass(frozen=True)
class TestResult:
    success: bool


class DatabaseTokenStore:
    def __init__(self, storage: "Storage") -> None:
        self._storage = storage

    async def load(self) -> dict[str, object] | None:
        account = self._storage.get_xiaomi_account()
        if account is None:
            return None
        value = json.loads(account.token_json)
        return value if isinstance(value, dict) else None

    async def save(self, token: Mapping[str, object]) -> None:
        account = self._storage.get_xiaomi_account()
        self._storage.save_xiaomi_account(account.username if account else "", json.dumps(dict(token), ensure_ascii=False))


def normalize_welcome_phrases(lines: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    phrases = tuple(dict.fromkeys(line.strip() for line in lines if line.strip()))
    if not phrases:
        raise ValueError("welcome phrases must contain at least one phrase")
    if len(phrases) > 20:
        raise ValueError("welcome phrases must contain at most 20 phrases")
    if any(len(phrase) > 100 for phrase in phrases):
        raise ValueError("each welcome phrase must contain at most 100 characters")
    return phrases


class Storage:
    def __init__(self, path: Path, max_events: int = 5000) -> None:
        self._path = path
        self._max_events = max_events

    def _connect(self, *, enable_wal: bool = True) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        if enable_wal:
            connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def initialize(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect(enable_wal=False) as connection:
            schema_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            table_names = {
                str(row["name"])
                for row in connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                    """
                )
            }
            if not table_names and schema_version == 0:
                connection.executescript(
                    """
                    CREATE TABLE cameras (
                        stream_id TEXT PRIMARY KEY,
                        speaker_id TEXT,
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        region_x REAL NOT NULL DEFAULT 0,
                        region_y REAL NOT NULL DEFAULT 0,
                        region_width REAL NOT NULL DEFAULT 1,
                        region_height REAL NOT NULL DEFAULT 1
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
                    CREATE TABLE xiaomi_account (
                        id INTEGER PRIMARY KEY CHECK (id = 1), username TEXT NOT NULL,
                        token_json TEXT NOT NULL, updated_at TEXT NOT NULL
                    );
                    CREATE TABLE speaker_bindings (
                        binding_id TEXT PRIMARY KEY, device_id TEXT NOT NULL UNIQUE,
                        display_name TEXT NOT NULL, mina_name TEXT NOT NULL, hardware TEXT NOT NULL,
                        miot_did TEXT, last_seen_at TEXT, bound INTEGER NOT NULL CHECK (bound IN (0, 1)),
                        available INTEGER NOT NULL CHECK (available IN (0, 1)), test_status TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    PRAGMA user_version = 4;
                    """
                )
                schema_version = 4
                table_names = _SCHEMA_TABLES
            elif schema_version == 2 and table_names == frozenset({"camera_rules", "cameras", "events", "rule_configs"}):
                if not self._schema_matches(connection, _V2_TABLE_DEFINITIONS):
                    raise IncompatibleSchemaError(
                        "incompatible database; reset the database using the runbook"
                    )
                connection.execute("BEGIN")
                try:
                    connection.execute(
                        "ALTER TABLE cameras ADD COLUMN region_x REAL NOT NULL DEFAULT 0"
                    )
                    connection.execute(
                        "ALTER TABLE cameras ADD COLUMN region_y REAL NOT NULL DEFAULT 0"
                    )
                    connection.execute(
                        "ALTER TABLE cameras ADD COLUMN region_width REAL NOT NULL DEFAULT 1"
                    )
                    connection.execute(
                        "ALTER TABLE cameras ADD COLUMN region_height REAL NOT NULL DEFAULT 1"
                    )
                    connection.execute("PRAGMA user_version = 3")
                    schema_version = 3
                except Exception:
                    connection.rollback()
                    raise
            if schema_version == 3:
                if table_names != frozenset({"camera_rules", "cameras", "events", "rule_configs"}) or not self._schema_matches(connection, _V3_TABLE_DEFINITIONS):
                    raise IncompatibleSchemaError("incompatible database; reset the database using the runbook")
                connection.commit()
                connection.execute("BEGIN")
                try:
                    connection.execute("ALTER TABLE cameras RENAME TO cameras_old")
                    connection.execute("ALTER TABLE camera_rules RENAME TO camera_rules_old")
                    connection.execute("""CREATE TABLE cameras (stream_id TEXT PRIMARY KEY, speaker_id TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, region_x REAL NOT NULL DEFAULT 0, region_y REAL NOT NULL DEFAULT 0, region_width REAL NOT NULL DEFAULT 1, region_height REAL NOT NULL DEFAULT 1)""")
                    connection.execute("INSERT INTO cameras SELECT stream_id,speaker_id,first_seen_at,last_seen_at,region_x,region_y,region_width,region_height FROM cameras_old")
                    connection.execute("""CREATE TABLE camera_rules (camera_id TEXT NOT NULL REFERENCES cameras(stream_id), rule_id TEXT NOT NULL, enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)), updated_at TEXT NOT NULL, PRIMARY KEY (camera_id, rule_id))""")
                    connection.execute("INSERT INTO camera_rules SELECT * FROM camera_rules_old")
                    connection.execute("DROP TABLE camera_rules_old")
                    connection.execute("DROP TABLE cameras_old")
                    connection.execute("""CREATE TABLE xiaomi_account (id INTEGER PRIMARY KEY CHECK (id = 1), username TEXT NOT NULL, token_json TEXT NOT NULL, updated_at TEXT NOT NULL)""")
                    connection.execute("""CREATE TABLE speaker_bindings (binding_id TEXT PRIMARY KEY, device_id TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL, mina_name TEXT NOT NULL, hardware TEXT NOT NULL, miot_did TEXT, last_seen_at TEXT, bound INTEGER NOT NULL CHECK (bound IN (0, 1)), available INTEGER NOT NULL CHECK (available IN (0, 1)), test_status TEXT NOT NULL, updated_at TEXT NOT NULL)""")
                    connection.execute("PRAGMA user_version = 4")
                except Exception:
                    connection.rollback(); raise
                table_names = _SCHEMA_TABLES
                schema_version = 4
            if schema_version != SCHEMA_VERSION or table_names != _SCHEMA_TABLES:
                raise IncompatibleSchemaError(
                    "incompatible database; reset the database using the runbook"
                )
            else:
                if not self._schema_matches(connection, _V4_TABLE_DEFINITIONS):
                    raise IncompatibleSchemaError(
                        "incompatible database; reset the database using the runbook"
                    )

            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute(
                """
                INSERT OR IGNORE INTO rule_configs(rule_id, config_json, updated_at)
                VALUES (?, ?, ?)
                """,
                (WELCOME_RULE_ID, json.dumps(WELCOME_PHRASES, ensure_ascii=False), self._now()),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO rule_configs(rule_id, config_json, updated_at)
                VALUES (?, ?, ?)
                """,
                (
                    OBJECT_RULE_ID,
                    json.dumps({"detector_adapter": ObjectDetectorAdapter.NANODET}),
                    self._now(),
                ),
            )
            row = connection.execute(
                "SELECT config_json FROM rule_configs WHERE rule_id = ?",
                (WELCOME_RULE_ID,),
            ).fetchone()
            if row is not None and tuple(json.loads(str(row["config_json"]))) == (
                _LEGACY_WELCOME_PHRASES
            ):
                connection.execute(
                    """
                    UPDATE rule_configs SET config_json = ?, updated_at = ?
                    WHERE rule_id = ?
                    """,
                    (
                        json.dumps(WELCOME_PHRASES, ensure_ascii=False),
                        self._now(),
                        WELCOME_RULE_ID,
                    ),
                )
            connection.executemany(
                """
                INSERT OR IGNORE INTO camera_rules(camera_id, rule_id, enabled, updated_at)
                SELECT stream_id, ?, 0, ? FROM cameras
                """,
                [(rule_id, self._now()) for rule_id in BUILTIN_RULE_IDS],
            )
            self._prune(connection)

    def sync_cameras(
        self, stream_ids: Sequence[str], default_speaker_id: str | None = None
    ) -> list[CameraConfig]:
        discovered_ids = sorted(set(stream_ids))
        timestamp = self._now()
        with self._connect() as connection:
            for stream_id in discovered_ids:
                inserted = connection.execute(
                    """
                    INSERT OR IGNORE INTO cameras(
                        stream_id, speaker_id, first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (stream_id, default_speaker_id, timestamp, timestamp),
                ).rowcount
                if inserted:
                    connection.executemany(
                        """
                        INSERT INTO camera_rules(camera_id, rule_id, enabled, updated_at)
                        VALUES (?, ?, 0, ?)
                        """,
                        [(stream_id, rule_id, timestamp) for rule_id in BUILTIN_RULE_IDS],
                    )
                else:
                    connection.execute(
                        "UPDATE cameras SET last_seen_at = ? WHERE stream_id = ?",
                        (timestamp, stream_id),
                    )
            return self._list_cameras(connection)

    def list_cameras(self) -> list[CameraConfig]:
        with self._connect() as connection:
            return self._list_cameras(connection)

    def camera_rule_enabled(self, camera_id: str, rule_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT enabled FROM camera_rules WHERE camera_id = ? AND rule_id = ?",
                (camera_id, rule_id),
            ).fetchone()
        if row is None:
            raise KeyError((camera_id, rule_id))
        return bool(row["enabled"])

    def set_camera_rule_enabled(self, camera_id: str, rule_id: str, enabled: bool) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE camera_rules SET enabled = ?, updated_at = ?
                WHERE camera_id = ? AND rule_id = ?
                """,
                (int(enabled), self._now(), camera_id, rule_id),
            )
            if cursor.rowcount != 1:
                raise KeyError((camera_id, rule_id))

    def camera_speaker_id(self, camera_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT speaker_id FROM cameras WHERE stream_id = ?", (camera_id,)
            ).fetchone()
        if row is None:
            raise KeyError(camera_id)
        return row["speaker_id"]

    def set_camera_speaker(self, camera_id: str, speaker_id: str | None) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE cameras SET speaker_id = ? WHERE stream_id = ?",
                (speaker_id, camera_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(camera_id)

    def set_camera_detection_region(self, camera_id: str, region: DetectionRegion) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE cameras
                SET region_x = ?, region_y = ?, region_width = ?, region_height = ?
                WHERE stream_id = ?
                """,
                (region.x, region.y, region.width, region.height, camera_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(camera_id)

    def welcome_phrases(self) -> tuple[str, ...]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT config_json FROM rule_configs WHERE rule_id = ?",
                (WELCOME_RULE_ID,),
            ).fetchone()
        if row is None:
            raise KeyError(WELCOME_RULE_ID)
        return normalize_welcome_phrases(tuple(json.loads(str(row["config_json"]))))

    def set_welcome_phrases(self, lines: Sequence[str]) -> tuple[str, ...]:
        phrases = normalize_welcome_phrases(list(lines))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO rule_configs(rule_id, config_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(rule_id) DO UPDATE SET
                    config_json = excluded.config_json,
                    updated_at = excluded.updated_at
                """,
                (WELCOME_RULE_ID, json.dumps(phrases, ensure_ascii=False), self._now()),
            )
        return phrases

    def object_detector_adapter(self) -> ObjectDetectorAdapter:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT config_json FROM rule_configs WHERE rule_id = ?",
                (OBJECT_RULE_ID,),
            ).fetchone()
        if row is None:
            raise KeyError(OBJECT_RULE_ID)
        config = json.loads(str(row["config_json"]))
        return ObjectDetectorAdapter(config["detector_adapter"])

    def set_object_detector_adapter(self, adapter: ObjectDetectorAdapter) -> ObjectDetectorAdapter:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO rule_configs(rule_id, config_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(rule_id) DO UPDATE SET
                    config_json = excluded.config_json,
                    updated_at = excluded.updated_at
                """,
                (
                    OBJECT_RULE_ID,
                    json.dumps({"detector_adapter": adapter}),
                    self._now(),
                ),
            )
        return adapter

    def record_event(self, event: EventRecord) -> None:
        details_json = json.dumps(redact(event.details), ensure_ascii=False, sort_keys=True)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO events(
                    occurred_at, kind, rule_id, camera_id, speaker_id, success,
                    latency_ms, details_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now(),
                    event.kind,
                    event.rule_id,
                    event.camera_id,
                    event.speaker_id,
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
        return [self._stored_event(row) for row in rows]

    def latest_rule_trigger(self, rule_id: str, camera_id: str | None = None) -> StoredEvent | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM events
                WHERE rule_id = ?
                  AND (? IS NULL OR camera_id = ?)
                  AND kind IN ('speaker_completed', 'speaker_queue_full',
                               'speaker_reauth_required', 'speaker_skipped_disabled',
                               'speaker_skipped_pairing_changed', 'speaker_unknown')
                ORDER BY id DESC
                LIMIT 1
                """,
                (rule_id, camera_id, camera_id),
            ).fetchone()
        return None if row is None else self._stored_event(row)

    def get_xiaomi_account(self) -> XiaomiAccount | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM xiaomi_account WHERE id = 1").fetchone()
        return None if row is None else XiaomiAccount(row["id"], row["username"], row["token_json"], row["updated_at"])

    def save_xiaomi_account(self, username: str, token_json: str) -> None:
        with self._connect() as connection:
            connection.execute("INSERT INTO xiaomi_account(id,username,token_json,updated_at) VALUES (1,?,?,?) ON CONFLICT(id) DO UPDATE SET username=excluded.username,token_json=excluded.token_json,updated_at=excluded.updated_at", (username, token_json, self._now()))

    def clear_xiaomi_account(self) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM xiaomi_account WHERE id = 1")

    def list_speaker_bindings(self) -> list[SpeakerBinding]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM speaker_bindings ORDER BY binding_id").fetchall()
        return [self._speaker_binding(row) for row in rows]

    def upsert_discovered_speakers(self, devices: Sequence[DiscoveredSpeaker]) -> list[SpeakerBinding]:
        now = self._now()
        with self._connect() as connection:
            seen = set()
            for device in devices:
                seen.add(device.device_id)
                row = connection.execute("SELECT binding_id FROM speaker_bindings WHERE device_id = ?", (device.device_id,)).fetchone()
                if row:
                    connection.execute("UPDATE speaker_bindings SET mina_name=?,hardware=?,miot_did=?,last_seen_at=?,available=1,updated_at=? WHERE device_id=?", (device.mina_name, device.hardware, device.miot_did, now, now, device.device_id))
                else:
                    connection.execute("INSERT INTO speaker_bindings VALUES (?,?,?,?,?,?,?,?,?,?,?)", (str(uuid.uuid4()), device.device_id, device.mina_name, device.mina_name, device.hardware, device.miot_did, now, 0, 1, "unknown", now))
            if seen:
                connection.execute("UPDATE speaker_bindings SET available=0,updated_at=? WHERE bound=1 AND device_id NOT IN (%s)" % ",".join("?" * len(seen)), (now, *seen))
            else:
                connection.execute("UPDATE speaker_bindings SET available=0,updated_at=? WHERE bound=1", (now,))
        return self.list_speaker_bindings()

    def save_speaker_bindings(self, selected_binding_ids: Sequence[str], confirmation_id: str | None = None) -> BindingSaveResult:
        selected = set(selected_binding_ids)
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM speaker_bindings").fetchall()
            known = {str(row["binding_id"]): row for row in rows}
            if not selected <= known.keys():
                raise ValueError("unknown speaker binding")
            affected = tuple(sorted(str(row["stream_id"]) for row in connection.execute("SELECT stream_id FROM cameras WHERE speaker_id IN (SELECT device_id FROM speaker_bindings WHERE bound=1)").fetchall()))
            unbinding = any(bool(row["bound"]) and bid not in selected for bid, row in known.items())
            if unbinding and confirmation_id is None:
                return BindingSaveResult(False, uuid.uuid4().hex, affected)
            connection.execute("UPDATE speaker_bindings SET bound=0,updated_at=?", (self._now(),))
            if selected:
                connection.execute("UPDATE speaker_bindings SET bound=1,updated_at=? WHERE binding_id IN (%s)" % ",".join("?" * len(selected)), (self._now(), *selected))
            if unbinding:
                connection.execute("UPDATE cameras SET speaker_id=NULL WHERE speaker_id IN (SELECT device_id FROM speaker_bindings WHERE bound=0)")
            return BindingSaveResult(True, confirmation_id, affected if unbinding else ())

    def journal_mode(self) -> str:
        with self._connect() as connection:
            return str(connection.execute("PRAGMA journal_mode").fetchone()[0])

    @staticmethod
    def _schema_matches(connection: sqlite3.Connection, expected: dict[str, str]) -> bool:
        rows = connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        actual = {str(row["name"]): "".join(str(row["sql"]).lower().split()) for row in rows}
        normalized_expected = {name: "".join(sql.lower().split()) for name, sql in expected.items()}
        return actual == normalized_expected

    @staticmethod
    def _now() -> str:
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _stored_event(row: sqlite3.Row) -> StoredEvent:
        return StoredEvent(
            id=row["id"],
            occurred_at=row["occurred_at"],
            kind=row["kind"],
            rule_id=row["rule_id"],
            camera_id=row["camera_id"],
            speaker_id=row["speaker_id"],
            success=bool(row["success"]),
            latency_ms=row["latency_ms"],
            details_json=row["details_json"],
        )

    @staticmethod
    def _speaker_binding(row: sqlite3.Row) -> SpeakerBinding:
        return SpeakerBinding(
            binding_id=row["binding_id"], device_id=row["device_id"], display_name=row["display_name"],
            mina_name=row["mina_name"], hardware=row["hardware"], miot_did=row["miot_did"],
            last_seen_at=row["last_seen_at"], bound=bool(row["bound"]), available=bool(row["available"]),
            test_status=row["test_status"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _list_cameras(connection: sqlite3.Connection) -> list[CameraConfig]:
        rows = connection.execute(
            """
            SELECT stream_id, speaker_id, first_seen_at, last_seen_at,
                   region_x, region_y, region_width, region_height
            FROM cameras ORDER BY stream_id
            """
        ).fetchall()
        return [
            CameraConfig(
                stream_id=row["stream_id"],
                speaker_id=row["speaker_id"],
                first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"],
                detection_region=DetectionRegion(
                    row["region_x"], row["region_y"], row["region_width"], row["region_height"]
                ),
            )
            for row in rows
        ]

    def _prune(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            DELETE FROM events
            WHERE id NOT IN (SELECT id FROM events ORDER BY id DESC LIMIT ?)
            """,
            (self._max_events,),
        )
