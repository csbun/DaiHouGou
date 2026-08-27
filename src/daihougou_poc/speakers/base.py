from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SpeakResult:
    success: bool
    latency_ms: int
    code: int | str | None
    error: str = ""


class Speaker(Protocol):
    def speak(self, text: str) -> SpeakResult: ...
