from collections import deque
from dataclasses import dataclass
from enum import StrEnum


class PresenceState(StrEnum):
    UNKNOWN = "unknown"
    ABSENT = "absent"
    PRESENT = "present"


class PresenceEventKind(StrEnum):
    PERSON_ENTERED = "person_entered"


@dataclass(frozen=True)
class PresenceEvent:
    kind: PresenceEventKind
    occurred_monotonic: float


class PresenceTracker:
    def __init__(self, leave_seconds: float = 10.0) -> None:
        self.state = PresenceState.UNKNOWN
        self._leave_seconds = leave_seconds
        self._recent: deque[bool] = deque(maxlen=3)
        self._last_observed_at: float | None = None
        self._last_person_at: float | None = None

    def observe(self, at: float, person_present: bool) -> PresenceEvent | None:
        if self._last_observed_at is not None and at < self._last_observed_at:
            raise ValueError("observation time must be monotonic")
        self._last_observed_at = at
        self._recent.append(person_present)
        if person_present:
            self._last_person_at = at

        stable_present = len(self._recent) == 3 and sum(self._recent) >= 2
        if self.state is PresenceState.UNKNOWN and len(self._recent) == 3:
            self.state = PresenceState.PRESENT if stable_present else PresenceState.ABSENT
            return None

        if self.state is PresenceState.ABSENT and stable_present:
            self.state = PresenceState.PRESENT
            return PresenceEvent(PresenceEventKind.PERSON_ENTERED, at)

        if (
            self.state is PresenceState.PRESENT
            and not person_present
            and self._last_person_at is not None
            and at - self._last_person_at >= self._leave_seconds
        ):
            self.state = PresenceState.ABSENT
            self._recent.clear()
            self._recent.append(False)
        return None
