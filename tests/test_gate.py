from guduck_poc.gate import (
    choose_speaker_backend,
    host_resources_pass,
    inventory_fingerprint,
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
    one_sample = (
        "HOST_STATS_SESSION_START campaign=campaign-1 "
        "timestamp=2026-08-27T00:00:00+00:00 interval=60\n"
        "2026-08-27T00:00:00+00:00\nMemAvailable: 800000 kB\n"
    )
    full_session = (
        "HOST_STATS_SESSION_START campaign=campaign-1 "
        "timestamp=2026-08-27T00:00:00+00:00 interval=60\n"
        + "".join(
            f"2026-08-27T{hour // 60:02d}:{hour % 60:02d}:00+00:00\n"
            "MemAvailable: 800000 kB\n"
            for hour in range(481)
        )
    )

    assert host_resources_pass(one_sample, "campaign-1") is False
    assert host_resources_pass(full_session, "campaign-1") is True
    assert host_resources_pass(full_session, "other-campaign") is False
    assert host_resources_pass(full_session + "\nOOM_CHECK_UNAVAILABLE", "campaign-1") is False


def test_host_gate_rejects_sparse_samples() -> None:
    sparse = (
        "HOST_STATS_SESSION_START campaign=campaign-1 "
        "timestamp=2026-08-27T00:00:00+00:00 interval=60\n"
        "2026-08-27T00:00:00+00:00\nMemAvailable: 800000 kB\n"
        "2026-08-27T08:00:00+00:00\nMemAvailable: 800000 kB\n"
    )

    assert host_resources_pass(sparse, "campaign-1") is False


def _speaker_run(
    backend: str,
    run_id: str,
    count: int,
    api_successes: int,
    audible_successes: int,
    campaign_id: str = "campaign-1",
    inventory_sha256: str = "inventory-sha",
) -> list[dict[str, object]]:
    events: list[dict[str, object]] = [
        {
            "component": f"speaker.{backend}",
            "operation": "speak",
            "correlation_id": run_id,
            "campaign_id": campaign_id,
            "inventory_sha256": inventory_sha256,
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
            "campaign_id": campaign_id,
            "inventory_sha256": inventory_sha256,
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
    inventory = {
        "cameras": [{"name": "xiaobai"}, {"name": "xiaobai_25k"}],
        "speaker": {"name": "xiaomi_play_enhanced"},
        "primary_camera": "xiaobai",
    }
    fingerprint = inventory_fingerprint(inventory)
    events = _speaker_run(
        "direct", "run-100", 100, 100, 100, inventory_sha256=fingerprint
    )
    events += [
        {
            "component": "camera.xiaobai",
            "operation": "decode",
            "correlation_id": "camera-1",
            "campaign_id": "campaign-1",
            "inventory_sha256": fingerprint,
            "success": True,
            "latency_ms": None,
            "details": {"duration_requested": 1800, "duration_actual": 1800},
        },
        {
            "component": "camera.xiaobai",
            "operation": "decode",
            "correlation_id": "camera-2",
            "campaign_id": "campaign-1",
            "inventory_sha256": fingerprint,
            "success": True,
            "latency_ms": None,
            "details": {"duration_requested": 1800, "duration_actual": 1800},
        },
    ]
    report = render_gate_report(events, inventory, "MemAvailable: 800000 kB\n", "campaign-1")

    assert "## Camera 30-Minute Results\n- **FAIL**" in report
    assert "## Stop Or Continue Decision\n- **FAIL**" in report


def test_report_passes_only_after_all_required_gates() -> None:
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
    fingerprint = inventory_fingerprint(inventory)
    events = _speaker_run("direct", "run-30", 30, 30, 29, inventory_sha256=fingerprint)
    events += _speaker_run("direct", "run-100", 100, 99, 98, inventory_sha256=fingerprint)
    events += [
        {
            "component": f"camera.{name}",
            "operation": "decode",
            "correlation_id": f"{name}-30m",
            "campaign_id": "campaign-1",
            "inventory_sha256": fingerprint,
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
            "campaign_id": "campaign-1",
            "inventory_sha256": fingerprint,
            "timestamp": "2026-08-27T08:00:00+00:00",
            "success": True,
            "latency_ms": None,
            "details": {"duration_requested": 28800, "duration_actual": 28800},
        },
        {
            "component": "camera.xiaobai_25k",
            "operation": "recovery",
            "correlation_id": "primary-recovery",
            "campaign_id": "campaign-1",
            "inventory_sha256": fingerprint,
            "success": True,
            "latency_ms": None,
            "details": {"recovery_seconds": 42, "restart_id": "go2rtc-restart-1"},
        },
    ]

    host_stats = (
        "HOST_STATS_SESSION_START campaign=campaign-1 "
        "timestamp=2026-08-27T00:00:00+00:00 interval=60\n"
        + "".join(
            f"2026-08-27T{hour // 60:02d}:{hour % 60:02d}:00+00:00\n"
            "MemAvailable: 800000 kB\n"
            for hour in range(481)
        )
    )
    report = render_gate_report(events, inventory, host_stats, "campaign-1")

    assert "## Selected Speaker Backend\n- `direct`" in report
    assert "API 99/100; audible 98/100" in report
    assert "## Stop Or Continue Decision\n- **PASS**" in report


def test_report_rejects_historical_or_changed_inventory_evidence() -> None:
    inventory = {
        "cameras": [
            {"name": "xiaobai", "miot_model": "one", "firmware": "1", "codec": "H264"},
            {
                "name": "xiaobai_25k",
                "miot_model": "two",
                "firmware": "2",
                "codec": "H264",
            },
        ],
        "speaker": {"name": "speaker", "miot_model": "l05c", "firmware": "3"},
        "primary_camera": "xiaobai_25k",
    }
    events = _speaker_run(
        "direct",
        "old-run",
        100,
        100,
        100,
        campaign_id="old-campaign",
        inventory_sha256=inventory_fingerprint(inventory),
    )

    report = render_gate_report(events, inventory, "", "campaign-1")

    assert "## Speaker 100-Trial Results\n- **NOT RUN**" in report
    assert "## Stop Or Continue Decision\n- **FAIL**" in report


def test_inventory_requires_both_expected_unique_cameras() -> None:
    inventory = {
        "cameras": [
            {"name": "xiaobai", "miot_model": "one", "firmware": "1", "codec": "H264"}
        ],
        "speaker": {"name": "speaker", "miot_model": "l05c", "firmware": "3"},
        "primary_camera": "xiaobai",
    }

    report = render_gate_report([], inventory, "", "campaign-1")

    assert "## Inventory\n- **FAIL**" in report


def test_camera_gate_uses_latest_attempt_in_campaign() -> None:
    inventory = {
        "cameras": [
            {"name": "xiaobai", "miot_model": "one", "firmware": "1", "codec": "H264"},
            {
                "name": "xiaobai_25k",
                "miot_model": "two",
                "firmware": "2",
                "codec": "H264",
            },
        ],
        "speaker": {"name": "speaker", "miot_model": "l05c", "firmware": "3"},
        "primary_camera": "xiaobai_25k",
    }
    fingerprint = inventory_fingerprint(inventory)
    events = [
        {
            "component": f"camera.{name}",
            "operation": "decode",
            "correlation_id": f"{name}-pass",
            "campaign_id": "campaign-1",
            "inventory_sha256": fingerprint,
            "success": True,
            "latency_ms": None,
            "details": {"duration_requested": 1800, "duration_actual": 1800},
        }
        for name in ("xiaobai", "xiaobai_25k")
    ]
    events.append(
        {
            "component": "camera.xiaobai",
            "operation": "decode",
            "correlation_id": "xiaobai-latest-failure",
            "campaign_id": "campaign-1",
            "inventory_sha256": fingerprint,
            "success": False,
            "latency_ms": None,
            "details": {"duration_requested": 1800, "duration_actual": 1200},
        }
    )

    report = render_gate_report(events, inventory, "", "campaign-1")

    assert "## Camera 30-Minute Results\n- **FAIL**" in report


def test_ha_selection_requires_non_admin_post_restart_validation() -> None:
    inventory = {
        "cameras": [
            {"name": "xiaobai", "miot_model": "one", "firmware": "1", "codec": "H264"},
            {
                "name": "xiaobai_25k",
                "miot_model": "two",
                "firmware": "2",
                "codec": "H264",
            },
        ],
        "speaker": {"name": "speaker", "miot_model": "l05c", "firmware": "3"},
        "primary_camera": "xiaobai_25k",
    }
    fingerprint = inventory_fingerprint(inventory)
    events = _speaker_run("ha", "ha-30", 30, 30, 30, inventory_sha256=fingerprint)
    events += _speaker_run("ha", "ha-100", 100, 100, 100, inventory_sha256=fingerprint)

    report = render_gate_report(events, inventory, "", "campaign-1")

    assert "## Home Assistant Restart Validation\n- **NOT RUN**" in report
    assert "## Selected Speaker Backend\n- `none`" in report


def test_ha_can_be_selected_after_non_admin_post_restart_validation() -> None:
    inventory = {
        "cameras": [
            {"name": "xiaobai", "miot_model": "one", "firmware": "1", "codec": "H264"},
            {
                "name": "xiaobai_25k",
                "miot_model": "two",
                "firmware": "2",
                "codec": "H264",
            },
        ],
        "speaker": {"name": "speaker", "miot_model": "l05c", "firmware": "3"},
        "primary_camera": "xiaobai_25k",
    }
    fingerprint = inventory_fingerprint(inventory)
    events = _speaker_run("ha", "ha-30", 30, 30, 30, inventory_sha256=fingerprint)
    events += _speaker_run("ha", "ha-100", 100, 100, 100, inventory_sha256=fingerprint)
    events.append(
        {
            "component": "speaker.ha",
            "operation": "ha_restart_validation",
            "correlation_id": "ha-validation",
            "campaign_id": "campaign-1",
            "inventory_sha256": fingerprint,
            "success": True,
            "latency_ms": 100,
            "details": {
                "restart_id": "ha-restart-1",
                "non_admin_confirmed": True,
            },
        }
    )

    report = render_gate_report(events, inventory, "", "campaign-1")

    assert "## Home Assistant Restart Validation\n- **PASS**" in report
    assert "## Selected Speaker Backend\n- `ha`" in report
