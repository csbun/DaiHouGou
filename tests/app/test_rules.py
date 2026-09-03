from guduck.presence import PresenceEvent, PresenceEventKind
from guduck.rules import WELCOME_RULE_ID, WelcomeRule


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
    action = WelcomeRule("front", state, chooser=lambda phrases: phrases[0]).handle(
        event(10)
    )

    assert action is not None
    assert action.camera_id == "front"
    assert action.rule_id == WELCOME_RULE_ID
    assert action.speaker_id == "living_room"
    assert action.text == "第一句"
    assert action.created_monotonic == 10


def test_rule_reads_latest_global_phrases_for_each_new_action() -> None:
    state = RuleState()
    rule = WelcomeRule(
        "front", state, cooldown_seconds=1, chooser=lambda phrases: phrases[0]
    )

    first = rule.handle(event(1))
    assert first is not None
    assert first.text == "第一句"
    state.phrases = ("更新后",)
    second = rule.handle(event(2))
    assert second is not None
    assert second.text == "更新后"


def test_disabled_camera_does_not_affect_other_camera() -> None:
    state = RuleState()

    assert WelcomeRule("back", state).handle(event(1)) is None
    assert WelcomeRule("front", state).handle(event(1)) is not None


def test_cooldown_is_scoped_to_one_rule_instance() -> None:
    state = RuleState()
    rule = WelcomeRule("front", state, cooldown_seconds=60)

    assert rule.handle(event(10)) is not None
    assert rule.handle(event(69.9)) is None
    assert rule.handle(event(70)) is not None
