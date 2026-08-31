import json
import sqlite3
from pathlib import Path

import pytest

from daihougou.rules import WELCOME_PHRASES, WELCOME_RULE_ID
from daihougou.storage import SCHEMA_VERSION, EventRecord, IncompatibleSchemaError, Storage

EXPECTED_ENGLISH_WELCOME_PHRASES = (
    "Welcome home!",
    "Hi there, welcome back!",
    "It's great to see you!",
    "Hello! We're happy you're here.",
    "Welcome! Hope you're having a great day.",
    "Hi! Nice to see you again.",
    "You're home! Welcome back.",
    "Hello there! Make yourself at home.",
    "Welcome back! We missed you.",
    "Hey! So good to see you.",
)
LEGACY_WELCOME_PHRASES = (
    "你好呀，欢迎回来。",
    "嗨，很高兴见到你。",
    "欢迎你，今天也要开心呀。",
)


def test_new_database_has_v2_schema_and_default_welcome_phrases(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()

    with sqlite3.connect(tmp_path / "app.db") as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION == 2
    assert storage.welcome_phrases() == EXPECTED_ENGLISH_WELCOME_PHRASES
    assert WELCOME_PHRASES == EXPECTED_ENGLISH_WELCOME_PHRASES


def test_initialize_upgrades_only_legacy_default_welcome_phrases(tmp_path: Path) -> None:
    legacy = Storage(tmp_path / "legacy.db")
    legacy.initialize()
    legacy.set_welcome_phrases(LEGACY_WELCOME_PHRASES)

    customized = Storage(tmp_path / "customized.db")
    customized.initialize()
    customized.set_welcome_phrases(("Our own welcome!",))

    legacy.initialize()
    customized.initialize()

    assert legacy.welcome_phrases() == EXPECTED_ENGLISH_WELCOME_PHRASES
    assert customized.welcome_phrases() == ("Our own welcome!",)


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


def test_sync_creates_disabled_camera_rules_and_preserves_missing_camera(
    tmp_path: Path,
) -> None:
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
        "stream_url": "[REDACTED]",
        "token": "[REDACTED]",
    }


def test_storage_enables_wal(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()

    assert storage.journal_mode() == "wal"
