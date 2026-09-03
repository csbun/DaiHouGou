import argparse
import json
import os
from pathlib import Path

from guduck_poc.camera import decode, wait_for_snapshot
from guduck_poc.events import ProbeEvent
from guduck_poc.gate import inventory_fingerprint, render_gate_report
from guduck_poc.report import JsonlReport
from guduck_poc.settings import Settings
from guduck_poc.speaker_trials import annotate_audible, run_trials
from guduck_poc.speakers.base import Speaker
from guduck_poc.speakers.direct import DirectSpeaker
from guduck_poc.speakers.home_assistant import HomeAssistantSpeaker


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
    parser = argparse.ArgumentParser(prog="guduck-poc")
    commands = parser.add_subparsers(dest="group", required=True)
    commands.add_parser("inventory")

    report = commands.add_parser("report")
    report_commands = report.add_subparsers(dest="report_command", required=True)
    gate = report_commands.add_parser("gate")
    gate.add_argument("--inventory", required=True)
    gate.add_argument("--host-stats", required=True)

    camera = commands.add_parser("camera")
    camera_commands = camera.add_subparsers(dest="camera_command", required=True)

    camera_decode = camera_commands.add_parser("decode")
    camera_decode.add_argument("--stream", required=True)
    camera_decode.add_argument("--duration-seconds", type=_positive_int, required=True)

    camera_wait = camera_commands.add_parser("wait")
    camera_wait.add_argument("--stream", required=True)
    camera_wait.add_argument("--max-seconds", type=_positive_int, required=True)
    camera_wait.add_argument("--restart-id", required=True)

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

    validate_ha = speaker_commands.add_parser("validate-ha")
    validate_ha.add_argument("--restart-id", required=True)
    validate_ha.add_argument("--non-admin-confirmed", action="store_true", required=True)
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


def _probe_context(settings: Settings) -> tuple[str, str]:
    inventory = json.loads(settings.inventory_path.read_text(encoding="utf-8"))
    if not isinstance(inventory, dict):
        raise TypeError("inventory must contain a JSON object")
    return settings.campaign_id, inventory_fingerprint(inventory)


def _missed_numbers(value: str) -> set[int]:
    if not value.strip():
        return set()
    return {int(number.strip()) for number in value.split(",")}


def _run_camera_command(
    args: argparse.Namespace,
    settings: Settings,
    report: JsonlReport,
    campaign_id: str,
    inventory_sha256: str,
) -> bool:
    if args.camera_command == "decode":
        rtsp_url = f"{settings.go2rtc_rtsp_base}/{args.stream}"
        success, elapsed, error = decode(rtsp_url, args.duration_seconds)
        report.append(
            ProbeEvent.create(
                component=f"camera.{args.stream}",
                operation="decode",
                success=success,
                campaign_id=campaign_id,
                inventory_sha256=inventory_sha256,
                details={
                    "duration_requested": args.duration_seconds,
                    "duration_actual": elapsed,
                    "error": error[-2000:],
                },
            )
        )
        return success

    recovery_seconds = wait_for_snapshot(
        settings.go2rtc_api_url, args.stream, args.max_seconds
    )
    success = recovery_seconds is not None
    report.append(
        ProbeEvent.create(
            component=f"camera.{args.stream}",
            operation="recovery",
            success=success,
            campaign_id=campaign_id,
            inventory_sha256=inventory_sha256,
            details={"recovery_seconds": recovery_seconds, "restart_id": args.restart_id},
        )
    )
    return success


def _run_gate_command(args: argparse.Namespace, settings: Settings, events: JsonlReport) -> bool:
    inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
    if not isinstance(inventory, dict):
        raise TypeError("inventory must contain a JSON object")
    host_stats = Path(args.host_stats).read_text(encoding="utf-8")
    rendered = render_gate_report(events.read(), inventory, host_stats, settings.campaign_id)
    output_path = settings.artifact_dir / "gate.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered + "\n", encoding="utf-8")
    print(output_path)
    return "## Stop Or Continue Decision\n- **PASS**" in rendered


def main() -> None:
    args = build_parser().parse_args()
    if args.group not in {"camera", "report", "speaker"}:
        return

    settings = Settings.from_mapping(dict(os.environ))
    report = _event_report(settings)
    if args.group == "report":
        if not _run_gate_command(args, settings, report):
            raise SystemExit(2)
        return

    if args.group == "camera":
        campaign_id, inventory_sha256 = _probe_context(settings)
        if not _run_camera_command(
            args, settings, report, campaign_id, inventory_sha256
        ):
            raise SystemExit(1)
        return

    campaign_id, inventory_sha256 = _probe_context(settings)
    if args.speaker_command == "run":
        speaker = _direct_speaker(settings) if args.backend == "direct" else _ha_speaker(settings)
        run_id = run_trials(
            args.backend,
            speaker,
            report,
            args.count,
            args.interval_seconds,
            campaign_id,
            inventory_sha256,
        )
        print(run_id)
        return

    if args.speaker_command == "validate-ha":
        result = _ha_speaker(settings).speak("Home Assistant restart validation")
        report.append(
            ProbeEvent.create(
                component="speaker.ha",
                operation="ha_restart_validation",
                success=result.success and args.non_admin_confirmed,
                campaign_id=campaign_id,
                inventory_sha256=inventory_sha256,
                latency_ms=result.latency_ms,
                details={
                    "code": result.code,
                    "error": result.error,
                    "restart_id": args.restart_id,
                    "non_admin_confirmed": args.non_admin_confirmed,
                },
            )
        )
        if not result.success:
            raise SystemExit(1)
        return

    annotate_audible(
        report,
        args.run_id,
        args.count,
        _missed_numbers(args.missed),
        campaign_id,
        inventory_sha256,
    )
