from daihougou_poc.cli import build_parser


def test_cli_lists_required_command_groups() -> None:
    parser = build_parser()
    help_text = parser.format_help()
    assert "inventory" in help_text
    assert "camera" in help_text
    assert "speaker" in help_text
    assert "report" in help_text


def test_cli_parses_speaker_trial_commands() -> None:
    parser = build_parser()
    run = parser.parse_args(
        [
            "speaker",
            "run",
            "--backend",
            "direct",
            "--count",
            "30",
            "--interval-seconds",
            "8",
        ]
    )
    annotate = parser.parse_args(
        [
            "speaker",
            "annotate",
            "--run-id",
            "run-1",
            "--count",
            "30",
            "--missed",
            "2,7",
        ]
    )

    assert (run.speaker_command, run.backend, run.count, run.interval_seconds) == (
        "run",
        "direct",
        30,
        8,
    )
    assert (annotate.speaker_command, annotate.run_id, annotate.count, annotate.missed) == (
        "annotate",
        "run-1",
        30,
        "2,7",
    )


def test_cli_parses_camera_probe_commands() -> None:
    parser = build_parser()
    decode = parser.parse_args(
        ["camera", "decode", "--stream", "xiaobai", "--duration-seconds", "1800"]
    )
    wait = parser.parse_args(
        [
            "camera",
            "wait",
            "--stream",
            "xiaobai_25k",
            "--max-seconds",
            "60",
            "--restart-id",
            "restart-1",
        ]
    )

    assert (decode.camera_command, decode.stream, decode.duration_seconds) == (
        "decode",
        "xiaobai",
        1800,
    )
    assert (wait.camera_command, wait.stream, wait.max_seconds, wait.restart_id) == (
        "wait",
        "xiaobai_25k",
        60,
        "restart-1",
    )


def test_cli_parses_ha_restart_validation() -> None:
    args = build_parser().parse_args(
        [
            "speaker",
            "validate-ha",
            "--restart-id",
            "ha-restart-1",
            "--non-admin-confirmed",
        ]
    )

    assert (args.speaker_command, args.restart_id, args.non_admin_confirmed) == (
        "validate-ha",
        "ha-restart-1",
        True,
    )


def test_cli_parses_gate_report_command() -> None:
    args = build_parser().parse_args(
        [
            "report",
            "gate",
            "--inventory",
            "/workspace/config/poc-devices.json",
            "--host-stats",
            "/workspace/artifacts/poc/host-stats-8h.log",
        ]
    )

    assert (args.report_command, args.inventory, args.host_stats) == (
        "gate",
        "/workspace/config/poc-devices.json",
        "/workspace/artifacts/poc/host-stats-8h.log",
    )
