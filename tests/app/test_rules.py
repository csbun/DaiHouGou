from pathlib import Path

from daihougou.presence import PresenceEvent, PresenceEventKind
from daihougou.rules import (
    BUILTIN_WELCOME_PHRASES,
    WELCOME_RULE_ID,
    RuleCatalog,
    SpeechAction,
)
from daihougou.storage import Storage


def test_welcome_catalog_is_disabled_until_storage_enables_it(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    catalog = RuleCatalog(storage, now=lambda: 100.0, chooser=lambda phrases: phrases[1])
    event = PresenceEvent(PresenceEventKind.PERSON_ENTERED, 100.0)

    assert catalog.on_presence(event) is None

    storage.set_rule_enabled(WELCOME_RULE_ID, True)
    action = catalog.on_presence(event)

    assert action == SpeechAction(WELCOME_RULE_ID, "嗨，很高兴见到你。", 100.0)


def test_welcome_catalog_uses_all_builtin_phrases_and_cooldown_boundary(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.set_rule_enabled(WELCOME_RULE_ID, True)
    selected: list[tuple[str, ...]] = []
    current_time = [10.0]

    def chooser(phrases: tuple[str, ...]) -> str:
        selected.append(phrases)
        return phrases[0]

    catalog = RuleCatalog(storage, now=lambda: current_time[0], chooser=chooser, cooldown=60.0)
    entered = PresenceEvent(PresenceEventKind.PERSON_ENTERED, 0.0)

    assert catalog.on_presence(entered) == SpeechAction(WELCOME_RULE_ID, BUILTIN_WELCOME_PHRASES[0], 10.0)
    current_time[0] = 69.999
    assert catalog.on_presence(entered) is None
    current_time[0] = 70.0
    assert catalog.on_presence(entered) == SpeechAction(WELCOME_RULE_ID, BUILTIN_WELCOME_PHRASES[0], 70.0)
    assert selected == [BUILTIN_WELCOME_PHRASES, BUILTIN_WELCOME_PHRASES]


def test_welcome_catalog_ignores_other_presence_kinds(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.set_rule_enabled(WELCOME_RULE_ID, True)
    catalog = RuleCatalog(storage)

    assert catalog.on_presence(object()) is None
