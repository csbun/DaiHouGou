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
