import hashlib
import json
from datetime import datetime, timedelta
from itertools import pairwise
from typing import TypedDict


class BackendSummary(TypedDict):
    api_successes: int
    audible_successes: int
    count: int
    p95_ms: int


EXPECTED_CAMERA_NAMES = {"xiaobai", "xiaobai_25k"}


def inventory_fingerprint(inventory: dict[str, object]) -> str:
    encoded = json.dumps(inventory, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def choose_speaker_backend(backends: dict[str, BackendSummary]) -> str | None:
    passing = {
        name: result
        for name, result in backends.items()
        if result["count"] == 100
        and result["api_successes"] >= 99
        and result["audible_successes"] >= 98
    }
    if not passing:
        return None
    return min(
        passing,
        key=lambda name: (
            -passing[name]["audible_successes"],
            -passing[name]["api_successes"],
            passing[name]["p95_ms"],
            name,
        ),
    )


def _details(event: dict[str, object]) -> dict[str, object]:
    details = event.get("details")
    return details if isinstance(details, dict) else {}


def _speaker_run_summaries(
    events: list[dict[str, object]], backend: str
) -> list[tuple[str, BackendSummary]]:
    trials_by_run: dict[str, list[dict[str, object]]] = {}
    for event in events:
        if event.get("component") != f"speaker.{backend}" or event.get("operation") != "speak":
            continue
        run_id = str(event.get("correlation_id", ""))
        trials_by_run.setdefault(run_id, []).append(event)

    annotations: dict[str, dict[str, object]] = {}
    for event in events:
        if (
            event.get("component") == "speaker.manual"
            and event.get("operation") == "audible_annotation"
        ):
            annotations[str(event.get("correlation_id", ""))] = event

    summaries: list[tuple[str, BackendSummary]] = []
    for run_id, trials in trials_by_run.items():
        latencies = sorted(
            int(event["latency_ms"])
            for event in trials
            if event.get("latency_ms") is not None
        )
        p95_index = max(0, min(len(latencies) - 1, (len(latencies) * 95 + 99) // 100 - 1))
        annotation_details = _details(annotations.get(run_id, {}))
        annotation_count = int(annotation_details.get("count", 0))
        audible_successes = (
            int(annotation_details.get("audible_successes", 0))
            if annotation_count == len(trials)
            else 0
        )
        summaries.append(
            (
                run_id,
                {
                    "api_successes": sum(bool(event.get("success")) for event in trials),
                    "audible_successes": audible_successes,
                    "count": len(trials),
                    "p95_ms": latencies[p95_index] if latencies else 0,
                },
            )
        )
    return summaries


def summarize_speaker_events(
    events: list[dict[str, object]], backend: str
) -> BackendSummary:
    summaries = _speaker_run_summaries(events, backend)
    if not summaries:
        return {"api_successes": 0, "audible_successes": 0, "count": 0, "p95_ms": 0}
    _, summary = max(enumerate(summaries), key=lambda item: (item[1][1]["count"], item[0]))
    return summary[1]


def _latest_run(
    events: list[dict[str, object]], backend: str, count: int
) -> BackendSummary | None:
    matching = [
        summary
        for _, summary in _speaker_run_summaries(events, backend)
        if summary["count"] == count
    ]
    return matching[-1] if matching else None


def _speaker_30_pass(summary: BackendSummary | None) -> bool:
    return bool(
        summary
        and summary["api_successes"] == 30
        and summary["audible_successes"] >= 29
    )


def _status(was_run: bool, passed: bool) -> str:
    if not was_run:
        return "NOT RUN"
    return "PASS" if passed else "FAIL"


def _inventory_complete(inventory: dict[str, object]) -> bool:
    cameras = inventory.get("cameras")
    speaker = inventory.get("speaker")
    if not isinstance(cameras, list) or len(cameras) != 2 or not isinstance(speaker, dict):
        return False
    required_camera_fields = ("name", "miot_model", "firmware", "codec")
    camera_names = _camera_names(inventory)
    primary = inventory.get("primary_camera")
    return (
        set(camera_names) == EXPECTED_CAMERA_NAMES
        and len(camera_names) == len(set(camera_names))
        and primary in EXPECTED_CAMERA_NAMES
        and all(
            isinstance(camera, dict)
            and all(camera.get(field) for field in required_camera_fields)
            for camera in cameras
        )
        and all(speaker.get(field) for field in ("name", "miot_model", "firmware"))
    )


def _camera_names(inventory: dict[str, object]) -> list[str]:
    cameras = inventory.get("cameras")
    if not isinstance(cameras, list):
        return []
    return [
        str(camera["name"])
        for camera in cameras
        if isinstance(camera, dict) and camera.get("name")
    ]


def _camera_event_passes(event: dict[str, object], requested: int) -> bool:
    details = _details(event)
    return bool(
        event.get("success")
        and details.get("duration_requested") == requested
        and int(details.get("duration_actual", 0)) >= requested
    )


def _format_speaker_summary(name: str, summary: BackendSummary) -> str:
    return (
        f"- {name}: API {summary['api_successes']}/{summary['count']}; "
        f"audible {summary['audible_successes']}/{summary['count']}; "
        f"p95 {summary['p95_ms']} ms"
    )


def _latest_host_session(host_stats: str, campaign_id: str) -> str:
    matching = [
        section
        for section in host_stats.split("HOST_STATS_SESSION_START ")
        if section.startswith(f"campaign={campaign_id} ")
    ]
    return "HOST_STATS_SESSION_START " + matching[-1] if matching else ""


def _memory_samples(host_stats: str) -> list[int]:
    return [
        int(line.split()[1])
        for line in host_stats.splitlines()
        if line.startswith("MemAvailable:") and len(line.split()) >= 2
    ]


def _host_samples(session: str) -> list[tuple[datetime, int]]:
    samples: list[tuple[datetime, int]] = []
    timestamp: datetime | None = None
    for line in session.splitlines():
        try:
            timestamp = datetime.fromisoformat(line)
        except ValueError:
            if line.startswith("MemAvailable:") and timestamp is not None:
                samples.append((timestamp, int(line.split()[1])))
                timestamp = None
    return samples


def host_resources_pass(
    host_stats: str,
    campaign_id: str,
    required_start: datetime | None = None,
    required_end: datetime | None = None,
) -> bool:
    session = _latest_host_session(host_stats, campaign_id)
    samples = _host_samples(session)
    if len(samples) < 2:
        return False
    timestamps = [timestamp for timestamp, _ in samples]
    memory_values = [memory for _, memory in samples]
    duration_seconds = (timestamps[-1] - timestamps[0]).total_seconds()
    maximum_gap = max(
        (current - previous).total_seconds()
        for previous, current in pairwise(timestamps)
    )
    oom_evidence_available = "OOM_CHECK_UNAVAILABLE" not in session
    no_oom = not any(marker in session.lower() for marker in ("out of memory", "killed process"))
    overlaps_required_window = True
    if required_start is not None and required_end is not None:
        tolerance = timedelta(seconds=90)
        overlaps_required_window = (
            timestamps[0] <= required_start + tolerance
            and timestamps[-1] >= required_end - tolerance
        )
    return (
        min(memory_values) >= 768000
        and duration_seconds >= 28700
        and maximum_gap <= 90
        and oom_evidence_available
        and no_oom
        and overlaps_required_window
    )


def render_gate_report(
    events: list[dict[str, object]],
    inventory: dict[str, object],
    host_stats: str,
    campaign_id: str,
) -> str:
    expected_inventory_sha256 = inventory_fingerprint(inventory)
    events = [
        event
        for event in events
        if event.get("campaign_id") == campaign_id
        and event.get("inventory_sha256") == expected_inventory_sha256
    ]
    ha_validation_events = [
        event
        for event in events
        if event.get("component") == "speaker.ha"
        and event.get("operation") == "ha_restart_validation"
    ]
    ha_validation_pass = any(
        event.get("success")
        and bool(_details(event).get("restart_id"))
        and _details(event).get("non_admin_confirmed") is True
        for event in ha_validation_events
    )
    backend_names = [
        backend
        for backend in ("direct", "ha")
        if any(event.get("component") == f"speaker.{backend}" for event in events)
    ]
    runs_30 = {backend: _latest_run(events, backend, 30) for backend in backend_names}
    runs_100 = {backend: _latest_run(events, backend, 100) for backend in backend_names}
    selectable = {
        backend: summary
        for backend, summary in runs_100.items()
        if summary is not None and _speaker_30_pass(runs_30.get(backend))
        and (backend != "ha" or ha_validation_pass)
    }
    selected = choose_speaker_backend(selectable)

    camera_events = [
        event for event in events if str(event.get("component", "")).startswith("camera.")
    ]
    camera_names = _camera_names(inventory)
    camera_30_events = [
        event
        for event in camera_events
        if event.get("operation") == "decode"
        and _details(event).get("duration_requested") == 1800
    ]
    latest_camera_30 = {
        name: next(
            (
                event
                for event in reversed(camera_30_events)
                if event.get("component") == f"camera.{name}"
            ),
            None,
        )
        for name in camera_names
    }
    camera_30_pass = bool(camera_names) and all(
        event is not None and _camera_event_passes(event, 1800)
        for event in latest_camera_30.values()
    )

    primary = str(inventory.get("primary_camera", ""))
    primary_8h_events = [
        event
        for event in camera_events
        if event.get("component") == f"camera.{primary}"
        and event.get("operation") == "decode"
        and _details(event).get("duration_requested") == 28800
    ]
    primary_8h_event = primary_8h_events[-1] if primary_8h_events else None
    primary_8h_pass = bool(
        primary_8h_event and _camera_event_passes(primary_8h_event, 28800)
    )

    recovery_events = [
        event
        for event in camera_events
        if event.get("component") == f"camera.{primary}"
        and event.get("operation") == "recovery"
    ]
    recovery_event = recovery_events[-1] if recovery_events else None
    recovery_pass = bool(
        recovery_event
        and recovery_event.get("success")
        and _details(recovery_event).get("recovery_seconds") is not None
        and int(_details(recovery_event)["recovery_seconds"]) <= 60
        and bool(_details(recovery_event).get("restart_id"))
    )

    latest_host_session = _latest_host_session(host_stats, campaign_id)
    memory_samples = _memory_samples(latest_host_session)
    primary_decode_event = primary_8h_event if primary_8h_pass else None
    decode_end: datetime | None = None
    decode_start: datetime | None = None
    if primary_decode_event is not None:
        try:
            decode_end = datetime.fromisoformat(str(primary_decode_event.get("timestamp", "")))
            decode_start = decode_end - timedelta(
                seconds=int(_details(primary_decode_event).get("duration_actual", 0))
            )
        except ValueError:
            pass
    memory_floor_pass = host_resources_pass(
        host_stats, campaign_id, decode_start, decode_end
    ) if decode_start is not None and decode_end is not None else False
    inventory_pass = _inventory_complete(inventory)
    speaker_30_pass = bool(selected) and _speaker_30_pass(runs_30.get(selected))
    passed = all(
        (
            inventory_pass,
            camera_30_pass,
            primary_8h_pass,
            recovery_pass,
            speaker_30_pass,
            selected is not None,
            memory_floor_pass,
        )
    )

    speaker_30_lines = [
        _format_speaker_summary(backend, summary)
        for backend, summary in runs_30.items()
        if summary is not None
    ] or ["- **NOT RUN**"]
    speaker_100_lines = [
        _format_speaker_summary(backend, summary)
        for backend, summary in runs_100.items()
        if summary is not None
    ] or ["- **NOT RUN**"]
    return "\n".join(
        [
            "# Device Compatibility Gate",
            "## Inventory",
            f"- **{'PASS' if inventory_pass else 'FAIL'}**",
            f"- Cameras: `{inventory.get('cameras', [])}`",
            f"- Speaker: `{inventory.get('speaker', {})}`",
            "## Camera 30-Minute Results",
            f"- **{_status(bool(camera_30_events), camera_30_pass)}**",
            "## Primary Camera 8-Hour Result",
            f"- **{_status(bool(primary_8h_events), primary_8h_pass)}**",
            "## Recovery Result",
            f"- **{_status(bool(recovery_events), recovery_pass)}**",
            "## Speaker 30-Trial Results",
            *speaker_30_lines,
            "## Speaker 100-Trial Results",
            *speaker_100_lines,
            "## Home Assistant Restart Validation",
            f"- **{_status(bool(ha_validation_events), ha_validation_pass)}**",
            "## Host Resource Floor",
            f"- **{_status(bool(memory_samples), memory_floor_pass)}**",
            "## Selected Speaker Backend",
            f"- `{selected or 'none'}`",
            "## Stop Or Continue Decision",
            f"- **{'PASS' if passed else 'FAIL'}**",
        ]
    )
