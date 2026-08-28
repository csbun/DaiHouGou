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

    def latest_rule_trigger(self, rule_id: str) -> StoredEvent | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM events
                WHERE rule_id = ?
                  AND kind IN ('speaker_completed', 'speaker_queue_full',
                               'speaker_reauth_required', 'speaker_skipped_disabled')
                ORDER BY id DESC
                LIMIT 1
                """,
                (rule_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredEvent(
            id=row["id"],
            occurred_at=row["occurred_at"],
            kind=row["kind"],
            rule_id=row["rule_id"],
            success=bool(row["success"]),
            latency_ms=row["latency_ms"],
            details_json=row["details_json"],
        )

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
