import asyncio
import json
import os
import sqlite3
from pathlib import Path

import pytest

from guduck.detection_region import FULL_FRAME_REGION, DetectionRegion
from guduck.rules import (
    BUILTIN_RULE_IDS,
    BUILTIN_RULE_NAMES,
    OBJECT_RULE_ID,
    WELCOME_PHRASES,
    WELCOME_RULE_ID,
)
from guduck.settings import ObjectDetectorAdapter
from guduck.storage import (
    SCHEMA_VERSION,
    DatabaseTokenStore,
    DiscoveredSpeaker,
    EventRecord,
    IncompatibleSchemaError,
    Storage,
)

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


def create_v2_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
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
        connection.execute(
            "INSERT INTO cameras VALUES (?, ?, ?, ?)",
            ("front", "bedroom", "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO camera_rules VALUES (?, ?, ?, ?)",
            ("front", WELCOME_RULE_ID, 1, "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO rule_configs VALUES (?, ?, ?)",
            (WELCOME_RULE_ID, json.dumps(("Custom welcome",)), "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            """
            INSERT INTO events(
                occurred_at, kind, rule_id, camera_id, speaker_id, success,
                latency_ms, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-01-01T00:00:00+00:00",
                "existing_event",
                WELCOME_RULE_ID,
                "front",
                "bedroom",
                1,
                10,
                "{}",
            ),
        )


def create_v3_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE cameras (
                stream_id TEXT PRIMARY KEY,
                speaker_id TEXT NOT NULL,
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
            PRAGMA user_version = 3;
            """
        )
        connection.execute(
            "INSERT INTO cameras VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "front",
                "legacy-living-room",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
                0.1,
                0.2,
                0.7,
                0.6,
            ),
        )
        connection.execute(
            "INSERT INTO camera_rules VALUES (?, ?, ?, ?)",
            ("front", WELCOME_RULE_ID, 1, "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            "INSERT INTO rule_configs VALUES (?, ?, ?)",
            (WELCOME_RULE_ID, json.dumps(("Custom welcome",)), "2026-01-01T00:00:00+00:00"),
        )
        connection.execute(
            """
            INSERT INTO events(
                occurred_at, kind, rule_id, camera_id, speaker_id, success,
                latency_ms, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "2026-01-01T00:00:00+00:00",
                "existing_event",
                WELCOME_RULE_ID,
                "front",
                "legacy-living-room",
                1,
                10,
                "{}",
            ),
        )


def test_new_database_has_v4_schema_and_default_welcome_phrases(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()

    with sqlite3.connect(tmp_path / "app.db") as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    assert version == SCHEMA_VERSION == 4
    assert storage.welcome_phrases() == EXPECTED_ENGLISH_WELCOME_PHRASES
    assert WELCOME_PHRASES == EXPECTED_ENGLISH_WELCOME_PHRASES


def test_object_detector_adapter_defaults_to_nanodet_and_persists(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    storage = Storage(path)
    storage.initialize()

    assert storage.object_detector_adapter() is ObjectDetectorAdapter.NANODET
    storage.set_object_detector_adapter(ObjectDetectorAdapter.OBJECTS365)

    reopened = Storage(path)
    reopened.initialize()
    assert reopened.object_detector_adapter() is ObjectDetectorAdapter.OBJECTS365


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
        assert (
            connection.execute("SELECT name FROM sqlite_master WHERE name = 'rules'").fetchone()
            is not None
        )


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


def test_new_camera_uses_full_frame_detection_region(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.sync_cameras(["front"], "living_room")

    assert storage.list_cameras()[0].detection_region == FULL_FRAME_REGION


def test_camera_detection_region_round_trips_and_later_save_overwrites(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.sync_cameras(["front"], "living_room")

    storage.set_camera_detection_region("front", DetectionRegion(0.1, 0.2, 0.7, 0.6))
    storage.set_camera_detection_region("front", DetectionRegion(0.2, 0.3, 0.5, 0.4))

    assert storage.list_cameras()[0].detection_region == DetectionRegion(0.2, 0.3, 0.5, 0.4)


def test_set_camera_detection_region_rejects_unknown_camera(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()

    with pytest.raises(KeyError, match="missing"):
        storage.set_camera_detection_region("missing", FULL_FRAME_REGION)


def test_initialize_migrates_v2_database_without_losing_data(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    create_v2_database(path)

    storage = Storage(path)
    storage.initialize()

    camera = storage.list_cameras()[0]
    assert SCHEMA_VERSION == 4
    assert camera.detection_region == FULL_FRAME_REGION
    assert camera.speaker_id == "bedroom"
    assert storage.camera_rule_enabled("front", WELCOME_RULE_ID) is True
    assert storage.welcome_phrases() == ("Custom welcome",)
    assert storage.recent_events()[0].kind == "existing_event"


def test_initialize_rejects_malformed_v2_tables_without_adding_region_columns(
    tmp_path: Path,
) -> None:
    path = tmp_path / "app.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE cameras (stream_id TEXT PRIMARY KEY);
            CREATE TABLE unexpected (id INTEGER PRIMARY KEY);
            PRAGMA user_version = 2;
            """
        )

    with pytest.raises(IncompatibleSchemaError, match="reset the database"):
        Storage(path).initialize()

    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(cameras)").fetchall()}
    assert "region_x" not in columns


def test_initialize_rejects_malformed_non_camera_v2_table_without_migration(
    tmp_path: Path,
) -> None:
    path = tmp_path / "app.db"
    create_v2_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE events RENAME TO events_backup")
        connection.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, kind TEXT NOT NULL)")
        connection.execute("PRAGMA user_version = 2")

    with pytest.raises(IncompatibleSchemaError, match="reset the database"):
        Storage(path).initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        columns = {row[1] for row in connection.execute("PRAGMA table_info(cameras)")}
    assert "region_x" not in columns


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


def test_initialize_backfills_disabled_object_rule_without_changing_welcome(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.sync_cameras(["front"], "living_room")
    storage.set_camera_rule_enabled("front", WELCOME_RULE_ID, True)
    with sqlite3.connect(tmp_path / "app.db") as connection:
        connection.execute(
            "DELETE FROM camera_rules WHERE camera_id = ? AND rule_id = ?",
            ("front", OBJECT_RULE_ID),
        )

    storage.initialize()

    assert storage.camera_rule_enabled("front", WELCOME_RULE_ID) is True
    assert storage.camera_rule_enabled("front", OBJECT_RULE_ID) is False
    assert BUILTIN_RULE_IDS == (WELCOME_RULE_ID, OBJECT_RULE_ID)
    assert BUILTIN_RULE_NAMES[OBJECT_RULE_ID] == "绘本物体播报"


def test_new_camera_gets_every_builtin_rule_disabled(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.sync_cameras(["front"], "living_room")

    assert {
        rule_id: storage.camera_rule_enabled("front", rule_id) for rule_id in BUILTIN_RULE_IDS
    } == {
        WELCOME_RULE_ID: False,
        OBJECT_RULE_ID: False,
    }


def test_new_database_has_xiaomi_tables_nullable_speaker_and_private_permissions(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir(mode=0o755)
    path = data_dir / "app.db"

    Storage(path).initialize()

    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        speaker_column = next(
            row for row in connection.execute("PRAGMA table_info(cameras)") if row[1] == "speaker_id"
        )
    assert tables == {
        "cameras",
        "camera_rules",
        "rule_configs",
        "events",
        "xiaomi_account",
        "speaker_bindings",
    }
    assert speaker_column[3] == 0
    assert os.stat(data_dir).st_mode & 0o777 == 0o700
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_initialize_migrates_v3_and_preserves_legacy_data(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    create_v3_database(path)

    storage = Storage(path)
    storage.initialize()

    camera = storage.list_cameras()[0]
    assert camera.speaker_id == "legacy-living-room"
    assert camera.detection_region == DetectionRegion(0.1, 0.2, 0.7, 0.6)
    assert storage.camera_rule_enabled("front", WELCOME_RULE_ID) is True
    assert storage.welcome_phrases() == ("Custom welcome",)
    assert storage.recent_events()[0].speaker_id == "legacy-living-room"
    assert storage.list_speaker_bindings() == []
    assert storage.get_xiaomi_account() is None


def test_database_token_store_uses_username_and_supports_save_load_clear(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    store = DatabaseTokenStore(storage, " owner@example.test ")
    token = {"userId": "private", "micoapi": ["security", "service-token"]}

    asyncio.run(store.save_token(token))

    account = storage.get_xiaomi_account()
    assert account is not None
    assert account.username == "owner@example.test"
    assert asyncio.run(store.load_token()) == token

    asyncio.run(store.save_token())
    assert storage.get_xiaomi_account() is None
    assert asyncio.run(store.load_token()) is None
    with pytest.raises(ValueError, match="username"):
        DatabaseTokenStore(storage, " ")


def test_discovery_preserves_binding_identity_and_custom_name_and_marks_missing(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    original = storage.upsert_discovered_speakers(
        [
            DiscoveredSpeaker("device-a", "客厅", "L05C", "miot-a"),
            DiscoveredSpeaker("device-b", "卧室", "LX04", "miot-b"),
        ]
    )
    by_device = {binding.device_id: binding for binding in original}
    device_a_binding_id = by_device["device-a"].binding_id
    storage.set_speaker_display_name(device_a_binding_id, "门口音箱")
    storage.save_speaker_bindings([device_a_binding_id])

    refreshed = storage.upsert_discovered_speakers(
        [DiscoveredSpeaker("device-a", "客厅新名称", "L05C", "miot-a-new")]
    )
    by_device = {binding.device_id: binding for binding in refreshed}

    assert by_device["device-a"].binding_id == device_a_binding_id
    assert by_device["device-a"].display_name == "门口音箱"
    assert by_device["device-a"].mina_name == "客厅新名称"
    assert by_device["device-a"].miot_did == "miot-a-new"
    assert by_device["device-a"].bound is True
    assert by_device["device-a"].available is True
    assert by_device["device-b"].available is False


def test_binding_save_requires_scoped_exact_one_time_confirmation(tmp_path: Path) -> None:
    now = [100.0]
    storage = Storage(tmp_path / "app.db", clock=lambda: now[0])
    storage.initialize()
    bindings = storage.upsert_discovered_speakers(
        [
            DiscoveredSpeaker("device-a", "客厅", "L05C", None),
            DiscoveredSpeaker("device-b", "卧室", "LX04", None),
        ]
    )
    first, second = bindings
    storage.save_speaker_bindings([first.binding_id, second.binding_id])
    storage.sync_cameras(["back", "front"])
    storage.set_camera_speaker("front", first.binding_id)
    storage.set_camera_speaker("back", second.binding_id)

    preview = storage.save_speaker_bindings([second.binding_id])
    repeated = storage.save_speaker_bindings([second.binding_id])

    assert preview.saved is False
    assert preview.confirmation_id is not None
    assert repeated.confirmation_id == preview.confirmation_id
    assert preview.affected_camera_ids == ("front",)
    assert storage.camera_speaker_id("front") == first.binding_id

    with pytest.raises(ValueError, match="confirmation"):
        storage.save_speaker_bindings([second.binding_id], "arbitrary")
    confirmed = storage.save_speaker_bindings(
        [second.binding_id],
        preview.confirmation_id,
    )

    assert confirmed.saved is True
    assert confirmed.confirmation_id is None
    assert confirmed.affected_camera_ids == ("front",)
    assert storage.camera_speaker_id("front") == first.binding_id
    assert storage.camera_speaker_id("back") == second.binding_id
    after = {binding.binding_id: binding for binding in storage.list_speaker_bindings()}
    assert after[first.binding_id].bound is False
    assert after[first.binding_id].available is False
    assert after[second.binding_id].bound is True

    storage.upsert_discovered_speakers(
        [
            DiscoveredSpeaker(
                after[first.binding_id].device_id,
                after[first.binding_id].mina_name,
                after[first.binding_id].hardware,
                None,
            )
        ]
    )
    rebound = storage.save_speaker_bindings([first.binding_id, second.binding_id])

    assert rebound.saved is True
    assert storage.camera_speaker_id("front") == first.binding_id
    restored = {
        binding.binding_id: binding for binding in storage.list_speaker_bindings()
    }
    assert restored[first.binding_id].bound is True
    assert restored[first.binding_id].available is True
    with pytest.raises(ValueError, match="confirmation"):
        storage.save_speaker_bindings(
            [first.binding_id, second.binding_id], preview.confirmation_id
        )


def test_binding_confirmation_expires_and_unknown_binding_is_rejected(tmp_path: Path) -> None:
    now = [100.0]
    storage = Storage(
        tmp_path / "app.db",
        clock=lambda: now[0],
        confirmation_ttl_seconds=10,
    )
    storage.initialize()
    binding = storage.upsert_discovered_speakers(
        [DiscoveredSpeaker("device-a", "客厅", "L05C", None)]
    )[0]
    storage.save_speaker_bindings([binding.binding_id])
    storage.sync_cameras(["front"])
    storage.set_camera_speaker("front", binding.binding_id)
    preview = storage.save_speaker_bindings([])
    now[0] = 111.0

    with pytest.raises(ValueError, match="confirmation"):
        storage.save_speaker_bindings([], preview.confirmation_id)
    assert storage.camera_speaker_id("front") == binding.binding_id
    with pytest.raises(ValueError, match="unknown speaker binding"):
        storage.save_speaker_bindings(["missing"])


def test_unbinding_unreferenced_discovered_speaker_keeps_discovery_availability(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    binding = storage.upsert_discovered_speakers(
        [DiscoveredSpeaker("device-a", "客厅", "L05C", None)]
    )[0]
    storage.save_speaker_bindings([binding.binding_id])

    result = storage.save_speaker_bindings([])

    assert result.saved is True
    assert result.confirmation_id is None
    assert result.affected_camera_ids == ()
    saved = storage.list_speaker_bindings()[0]
    assert saved.bound is False
    assert saved.available is True


def test_account_replacement_and_bindings_commit_atomically_after_confirmation(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.save_xiaomi_account("old-owner", '{"token":"old"}')
    old = storage.upsert_discovered_speakers(
        [DiscoveredSpeaker("old-device", "旧音箱", "L05C", None)]
    )[0]
    storage.save_speaker_bindings([old.binding_id])
    storage.sync_cameras(["front"])
    storage.set_camera_speaker("front", old.binding_id)
    new_binding_id = "new-safe-binding-id"
    new_device = DiscoveredSpeaker(
        "new-private-device",
        "新音箱",
        "LX04",
        None,
        new_binding_id,
    )

    preview = storage.replace_xiaomi_configuration(
        "new-owner",
        '{"token":"new"}',
        [new_device],
        [new_binding_id],
    )

    assert preview.saved is False
    unchanged_account = storage.get_xiaomi_account()
    assert unchanged_account is not None
    assert unchanged_account.username == "old-owner"
    assert [binding.binding_id for binding in storage.list_speaker_bindings()] == [
        old.binding_id
    ]
    assert storage.camera_speaker_id("front") == old.binding_id

    result = storage.replace_xiaomi_configuration(
        "new-owner",
        '{"token":"new"}',
        [new_device],
        [new_binding_id],
        confirmation_id=preview.confirmation_id,
    )

    assert result.saved is True
    account = storage.get_xiaomi_account()
    assert account is not None
    assert (account.username, account.token_json) == ("new-owner", '{"token":"new"}')
    bindings = {binding.binding_id: binding for binding in storage.list_speaker_bindings()}
    assert bindings[old.binding_id].bound is False
    assert bindings[new_binding_id].bound is True
    assert storage.camera_speaker_id("front") == old.binding_id


@pytest.mark.parametrize("kind", ["speaker_unavailable", "speaker_reconfigured"])
def test_latest_rule_trigger_includes_dynamic_speaker_events(
    tmp_path: Path,
    kind: str,
) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.record_event(
        EventRecord(
            kind,
            False,
            rule_id=WELCOME_RULE_ID,
            camera_id="front",
            speaker_id="binding-id",
        )
    )

    event = storage.latest_rule_trigger(WELCOME_RULE_ID, "front")

    assert event is not None
    assert event.kind == kind
