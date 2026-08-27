import json
from pathlib import Path

from daihougou_poc.events import ProbeEvent
from daihougou_poc.report import JsonlReport


def test_report_redacts_secret_fields(tmp_path: Path) -> None:
    path = tmp_path / "events.jsonl"
    report = JsonlReport(path)
    report.append(
        ProbeEvent.create(
            component="speaker.direct",
            operation="speak",
            success=False,
            details={"token": "secret", "password": "secret", "code": 401},
        )
    )
    data = json.loads(path.read_text().strip())
    assert data["details"] == {
        "token": "[REDACTED]",
        "password": "[REDACTED]",
        "code": 401,
    }
