import json
from pathlib import Path

import pytest

from daihougou.storage import EventRecord, Storage


def test_storage_starts_welcome_rule_disabled_and_updates_it(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()

    assert storage.rule_enabled("welcome_on_person_entry") is False
    storage.set_rule_enabled("welcome_on_person_entry", True)
    assert storage.rule_enabled("welcome_on_person_entry") is True


def test_storage_rejects_unknown_rule(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()

    with pytest.raises(KeyError, match="unknown"):
        storage.set_rule_enabled("unknown", True)


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
