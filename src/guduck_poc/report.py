import json
from pathlib import Path

from guduck_poc.events import ProbeEvent


class JsonlReport:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, event: ProbeEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=True) + "\n")

    def read(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text().splitlines() if line]
