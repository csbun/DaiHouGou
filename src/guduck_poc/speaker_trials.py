import time
from uuid import uuid4

from guduck_poc.events import ProbeEvent
from guduck_poc.report import JsonlReport
from guduck_poc.speakers.base import Speaker


def run_trials(
    backend: str,
    speaker: Speaker,
    report: JsonlReport,
    count: int,
    interval_seconds: float,
    campaign_id: str = "",
    inventory_sha256: str = "",
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
                campaign_id=campaign_id,
                inventory_sha256=inventory_sha256,
                latency_ms=result.latency_ms,
                details={"trial": trial, "code": result.code, "error": result.error},
            )
        )
        if trial < count:
            time.sleep(interval_seconds)
    return run_id


def annotate_audible(
    report: JsonlReport,
    run_id: str,
    count: int,
    missed: set[int],
    campaign_id: str = "",
    inventory_sha256: str = "",
) -> None:
    invalid = {number for number in missed if number < 1 or number > count}
    if invalid:
        raise ValueError(f"missed trial numbers out of range: {sorted(invalid)}")
    audible_successes = count - len(missed)
    threshold = 29 if count == 30 else 98 if count == 100 else count
    report.append(
        ProbeEvent.create(
            component="speaker.manual",
            operation="audible_annotation",
            success=audible_successes >= threshold,
            correlation_id=run_id,
            campaign_id=campaign_id,
            inventory_sha256=inventory_sha256,
            details={
                "count": count,
                "missed": sorted(missed),
                "audible_successes": audible_successes,
            },
        )
    )
