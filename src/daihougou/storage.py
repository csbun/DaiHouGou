from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from daihougou.redaction import redact
from daihougou.rules import WELCOME_PHRASES, WELCOME_RULE_ID

SCHEMA_VERSION = 2
_SCHEMA_TABLES = frozenset({"camera_rules", "cameras", "events", "rule_configs"})
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
    speaker_id: str
    first_seen_at: str
    last_seen_at: str


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
                    """
                )
            elif schema_version != SCHEMA_VERSION or table_names != _SCHEMA_TABLES:
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
            self._prune(connection)

    def sync_cameras(
        self, stream_ids: Sequence[str], default_speaker_id: str
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
                    connection.execute(
                        """
                        INSERT INTO camera_rules(camera_id, rule_id, enabled, updated_at)
                        VALUES (?, ?, 0, ?)
                        """,
                        (stream_id, WELCOME_RULE_ID, timestamp),
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

    def set_camera_rule_enabled(
        self, camera_id: str, rule_id: str, enabled: bool
    ) -> None:
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

    def camera_speaker_id(self, camera_id: str) -> str:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT speaker_id FROM cameras WHERE stream_id = ?", (camera_id,)
            ).fetchone()
        if row is None:
            raise KeyError(camera_id)
        return str(row["speaker_id"])

    def set_camera_speaker(self, camera_id: str, speaker_id: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE cameras SET speaker_id = ? WHERE stream_id = ?",
                (speaker_id, camera_id),
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

    def latest_rule_trigger(
        self, rule_id: str, camera_id: str | None = None
    ) -> StoredEvent | None:
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

    def journal_mode(self) -> str:
        with self._connect() as connection:
            return str(connection.execute("PRAGMA journal_mode").fetchone()[0])

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
    def _list_cameras(connection: sqlite3.Connection) -> list[CameraConfig]:
        rows = connection.execute(
            """
            SELECT stream_id, speaker_id, first_seen_at, last_seen_at
            FROM cameras ORDER BY stream_id
            """
        ).fetchall()
        return [
            CameraConfig(
                stream_id=row["stream_id"],
                speaker_id=row["speaker_id"],
                first_seen_at=row["first_seen_at"],
                last_seen_at=row["last_seen_at"],
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
