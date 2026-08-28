import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from daihougou.presence import PresenceEvent, PresenceEventKind
from daihougou.storage import Storage

WELCOME_RULE_ID = "welcome_on_person_entry"
BUILTIN_WELCOME_PHRASES = (
    "你好呀，欢迎回来。",
    "嗨，很高兴见到你。",
    "欢迎你，今天也要开心呀。",
)


@dataclass(frozen=True)
class SpeechAction:
    rule_id: str
    text: str
    created_monotonic: float


class RuleCatalog:
    def __init__(
        self,
        storage: Storage,
        now: Callable[[], float] | None = None,
        chooser: Callable[[tuple[str, ...]], str] = random.choice,
        cooldown: float = 60.0,
        *,
        clock: Callable[[], float] | None = None,
        cooldown_seconds: float | None = None,
    ) -> None:
        if now is not None and clock is not None:
            raise ValueError("pass only one of now or clock")
        if cooldown_seconds is not None:
            if cooldown != 60.0:
                raise ValueError("pass only one of cooldown or cooldown_seconds")
            cooldown = cooldown_seconds
        if cooldown < 0:
            raise ValueError("cooldown must not be negative")
        self._storage = storage
        self._now = now or clock or time.monotonic
        self._chooser = chooser
        self._cooldown = cooldown
        self._last_created: float | None = None

    def on_presence(self, event: PresenceEvent) -> SpeechAction | None:
        if getattr(event, "kind", None) != PresenceEventKind.PERSON_ENTERED:
            return None
        if not self._storage.rule_enabled(WELCOME_RULE_ID):
            return None
        created = self._now()
        if self._last_created is not None and created - self._last_created < self._cooldown:
            return None
        self._last_created = created
        return SpeechAction(WELCOME_RULE_ID, self._chooser(BUILTIN_WELCOME_PHRASES), created)

    def on_event(self, event: Any) -> SpeechAction | None:
        return self.on_presence(event)

    def handle(self, event: Any) -> SpeechAction | None:
        return self.on_presence(event)


WelcomeRule = RuleCatalog
