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


def test_enabled_rule_uses_catalog_and_event_time_for_cooldown() -> None:
    rule = WelcomeRule(RuleState(True), cooldown_seconds=60, chooser=lambda phrases: phrases[-1])

    first = rule.handle(event(10))

    assert first is not None
    assert first.text == WELCOME_PHRASES[-1]
    assert first.created_monotonic == 10
    assert rule.handle(event(69.9)) is None
    assert rule.handle(event(70)) is not None
