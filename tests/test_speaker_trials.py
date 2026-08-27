from pathlib import Path

from daihougou_poc.report import JsonlReport
from daihougou_poc.speaker_trials import annotate_audible, run_trials
from daihougou_poc.speakers.base import SpeakResult


class FakeSpeaker:
    def speak(self, text: str) -> SpeakResult:
        return SpeakResult(True, 12, 0)


def test_trials_are_numbered_and_manual_misses_are_separate(tmp_path: Path) -> None:
    report = JsonlReport(tmp_path / "events.jsonl")
    run_id = run_trials("direct", FakeSpeaker(), report, count=3, interval_seconds=0)
    annotate_audible(report, run_id=run_id, count=3, missed={2})
    events = report.read()
    assert [event["details"]["trial"] for event in events[:3]] == [1, 2, 3]
    assert events[-1]["details"]["audible_successes"] == 2
