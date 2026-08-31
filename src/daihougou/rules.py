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
    def camera_rule_enabled(self, camera_id: str, rule_id: str) -> bool: ...

    def camera_speaker_id(self, camera_id: str) -> str: ...

    def welcome_phrases(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class SpeechAction:
    camera_id: str
    rule_id: str
    speaker_id: str
    text: str
    created_monotonic: float


class WelcomeRule:
    def __init__(
        self,
        camera_id: str,
        state: RuleState,
        cooldown_seconds: float = 60,
        chooser: Callable[[Sequence[str]], str] = random.choice,
    ) -> None:
        self._camera_id = camera_id
        self._state = state
        self._cooldown_seconds = cooldown_seconds
        self._chooser = chooser
        self._last_action_at: float | None = None

    def handle(self, event: PresenceEvent) -> SpeechAction | None:
        if event.kind is not PresenceEventKind.PERSON_ENTERED:
            return None
        if not self._state.camera_rule_enabled(self._camera_id, WELCOME_RULE_ID):
            return None
        if (
            self._last_action_at is not None
            and event.occurred_monotonic - self._last_action_at < self._cooldown_seconds
        ):
            return None
        self._last_action_at = event.occurred_monotonic
        return SpeechAction(
            camera_id=self._camera_id,
            rule_id=WELCOME_RULE_ID,
            speaker_id=self._state.camera_speaker_id(self._camera_id),
            text=self._chooser(self._state.welcome_phrases()),
            created_monotonic=event.occurred_monotonic,
        )
