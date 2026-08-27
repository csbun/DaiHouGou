import argparse
import json
import os

from daihougou_poc.report import JsonlReport
from daihougou_poc.settings import Settings
from daihougou_poc.speaker_trials import annotate_audible, run_trials
from daihougou_poc.speakers.base import Speaker
from daihougou_poc.speakers.direct import DirectSpeaker
from daihougou_poc.speakers.home_assistant import HomeAssistantSpeaker


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _non_negative_float(value: str) -> float:
    parsed = float(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="daihougou-poc")
    commands = parser.add_subparsers(dest="group", required=True)
    for name in ("inventory", "camera", "report"):
        commands.add_parser(name)

    speaker = commands.add_parser("speaker")
    speaker_commands = speaker.add_subparsers(dest="speaker_command", required=True)

    run = speaker_commands.add_parser("run")
    run.add_argument("--backend", choices=("direct", "ha"), required=True)
    run.add_argument("--count", type=_positive_int, required=True)
    run.add_argument("--interval-seconds", type=_non_negative_float, required=True)

    annotate = speaker_commands.add_parser("annotate")
    annotate.add_argument("--run-id", required=True)
    annotate.add_argument("--count", type=_positive_int, required=True)
    annotate.add_argument("--missed", required=True)
    return parser


def _direct_speaker(settings: Settings) -> Speaker:
    return DirectSpeaker(settings.mi_user, settings.mi_pass, settings.mi_did)


def _ha_speaker(settings: Settings) -> Speaker:
    extra_data = json.loads(settings.ha_extra_data_json)
    if not isinstance(extra_data, dict):
        raise TypeError("HA_EXTRA_DATA_JSON must contain a JSON object")
    return HomeAssistantSpeaker(
        base_url=settings.ha_base_url,
        token=settings.ha_access_token,
        service=settings.ha_speaker_service,
        entity_id=settings.ha_speaker_entity,
        text_field=settings.ha_text_field,
        extra_data=extra_data,
    )


def _event_report(settings: Settings) -> JsonlReport:
    return JsonlReport(settings.artifact_dir / "events.jsonl")


def _missed_numbers(value: str) -> set[int]:
    if not value.strip():
        return set()
    return {int(number.strip()) for number in value.split(",")}


def main() -> None:
    args = build_parser().parse_args()
    if args.group != "speaker":
        return

    settings = Settings.from_mapping(dict(os.environ))
    report = _event_report(settings)
    if args.speaker_command == "run":
        speaker = _direct_speaker(settings) if args.backend == "direct" else _ha_speaker(settings)
        run_id = run_trials(args.backend, speaker, report, args.count, args.interval_seconds)
        print(run_id)
        return

    annotate_audible(report, args.run_id, args.count, _missed_numbers(args.missed))
