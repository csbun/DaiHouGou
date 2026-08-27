import time
from uuid import uuid4

from daihougou_poc.events import ProbeEvent
from daihougou_poc.report import JsonlReport
from daihougou_poc.speakers.base import Speaker


def run_trials(
    backend: str,
    speaker: Speaker,
    report: JsonlReport,
    count: int,
    interval_seconds: float,
) -> str:
    run_id = str(uuid4())
    for trial in range(1, count + 1):
        result = speaker.speak(f"播报测试 {trial}")
        report.append(
            ProbeEvent.create(
                component=f"speaker.{backend}",
                operation="speak",
                success=result.success,
                correlation_id=run_id,
                latency_ms=result.latency_ms,
                details={"trial": trial, "code": result.code, "error": result.error},
            )
        )
        if trial < count:
            time.sleep(interval_seconds)
    return run_id


def annotate_audible(report: JsonlReport, run_id: str, count: int, missed: set[int]) -> None:
    invalid = {number for number in missed if number < 1 or number > count}
    if invalid:
        raise ValueError(f"missed trial numbers out of range: {sorted(invalid)}")
    report.append(
        ProbeEvent.create(
            component="speaker.manual",
            operation="audible_annotation",
            success=(count - len(missed)) / count >= 0.98,
            correlation_id=run_id,
            details={
                "count": count,
                "missed": sorted(missed),
                "audible_successes": count - len(missed),
            },
        )
    )
