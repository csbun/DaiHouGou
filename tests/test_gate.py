from daihougou_poc.gate import (
    choose_speaker_backend,
    host_resources_pass,
    render_gate_report,
    summarize_speaker_events,
)


def test_gate_prefers_more_reliable_backend_then_lower_p95() -> None:
    direct = {"api_successes": 100, "audible_successes": 98, "count": 100, "p95_ms": 1200}
    ha = {"api_successes": 100, "audible_successes": 99, "count": 100, "p95_ms": 1800}
    assert choose_speaker_backend({"direct": direct, "ha": ha}) == "ha"


def test_gate_rejects_backends_below_minimum() -> None:
    direct = {"api_successes": 98, "audible_successes": 98, "count": 100, "p95_ms": 900}
    assert choose_speaker_backend({"direct": direct}) is None


def test_host_gate_requires_a_full_session_and_oom_visibility() -> None:
    one_sample = "HOST_STATS_SESSION_START 2026-08-27T00:00:00+00:00\nMemAvailable: 800000 kB\n"
    full_session = (
        "HOST_STATS_SESSION_START 2026-08-27T00:00:00+00:00\n"
        "2026-08-27T00:00:00+00:00\n"
        "MemAvailable: 800000 kB\n"
        "2026-08-27T08:00:00+00:00\n"
        "MemAvailable: 790000 kB"
    )

    assert host_resources_pass(one_sample) is False
    assert host_resources_pass(full_session) is True
    assert host_resources_pass(full_session + "\nOOM_CHECK_UNAVAILABLE") is False


def _speaker_run(
    backend: str,
    run_id: str,
    count: int,
    api_successes: int,
    audible_successes: int,
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = [
        {
            "component": f"speaker.{backend}",
            "operation": "speak",
            "correlation_id": run_id,
            "success": trial <= api_successes,
            "latency_ms": trial,
            "details": {"trial": trial},
        }
        for trial in range(1, count + 1)
    ]
    events.append(
        {
            "component": "speaker.manual",
            "operation": "audible_annotation",
            "correlation_id": run_id,
            "success": True,
            "latency_ms": None,
            "details": {"count": count, "audible_successes": audible_successes},
        }
    )
    return events


def test_speaker_summary_does_not_combine_separate_runs() -> None:
    events = _speaker_run("direct", "run-30", 30, 30, 29)
    events += _speaker_run("direct", "run-100", 100, 98, 98)

    assert summarize_speaker_events(events, "direct") == {
        "api_successes": 98,
        "audible_successes": 98,
        "count": 100,
        "p95_ms": 95,
    }


def test_report_requires_each_inventory_camera() -> None:
    events = _speaker_run("direct", "run-100", 100, 100, 100)
    events += [
        {
            "component": "camera.xiaobai",
            "operation": "decode",
            "correlation_id": "camera-1",
            "success": True,
            "latency_ms": None,
            "details": {"duration_requested": 1800, "duration_actual": 1800},
        },
        {
            "component": "camera.xiaobai",
            "operation": "decode",
            "correlation_id": "camera-2",
            "success": True,
            "latency_ms": None,
            "details": {"duration_requested": 1800, "duration_actual": 1800},
        },
    ]
    inventory = {
        "cameras": [{"name": "xiaobai"}, {"name": "xiaobai_25k"}],
        "speaker": {"name": "xiaomi_play_enhanced"},
        "primary_camera": "xiaobai",
    }

    report = render_gate_report(events, inventory, "MemAvailable: 800000 kB\n")

    assert "## Camera 30-Minute Results\n- **FAIL**" in report
    assert "## Stop Or Continue Decision\n- **FAIL**" in report


def test_report_passes_only_after_all_required_gates() -> None:
    events = _speaker_run("direct", "run-30", 30, 30, 29)
    events += _speaker_run("direct", "run-100", 100, 99, 98)
    events += [
        {
            "component": f"camera.{name}",
            "operation": "decode",
            "correlation_id": f"{name}-30m",
            "success": True,
            "latency_ms": None,
            "details": {"duration_requested": 1800, "duration_actual": 1800},
        }
        for name in ("xiaobai", "xiaobai_25k")
    ]
    events += [
        {
            "component": "camera.xiaobai_25k",
            "operation": "decode",
            "correlation_id": "primary-8h",
            "success": True,
            "latency_ms": None,
            "details": {"duration_requested": 28800, "duration_actual": 28800},
        },
        {
            "component": "camera.xiaobai_25k",
            "operation": "recovery",
            "correlation_id": "primary-recovery",
            "success": True,
            "latency_ms": None,
            "details": {"recovery_seconds": 42},
        },
    ]
    inventory = {
        "cameras": [
            {
                "name": "xiaobai",
                "miot_model": "camera.model.one",
                "firmware": "1.0.0",
                "codec": "H264",
            },
            {
                "name": "xiaobai_25k",
                "miot_model": "camera.model.two",
                "firmware": "2.0.0",
                "codec": "H264",
            },
        ],
        "speaker": {
            "name": "xiaomi_play_enhanced",
            "miot_model": "xiaomi.wifispeaker.l05c",
            "firmware": "3.0.0",
        },
        "primary_camera": "xiaobai_25k",
    }

    host_stats = (
        "HOST_STATS_SESSION_START 2026-08-27T00:00:00+00:00\n"
        "2026-08-27T00:00:00+00:00\n"
        "MemAvailable: 800000 kB\n"
        "2026-08-27T08:00:00+00:00\n"
        "MemAvailable: 790000 kB"
    )
    report = render_gate_report(events, inventory, host_stats)

    assert "## Selected Speaker Backend\n- `direct`" in report
    assert "API 99/100; audible 98/100" in report
    assert "## Stop Or Continue Decision\n- **PASS**" in report
