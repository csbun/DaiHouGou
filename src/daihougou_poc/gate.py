from typing import TypedDict


class BackendSummary(TypedDict):
    api_successes: int
    audible_successes: int
    count: int
    p95_ms: int


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
    if not isinstance(cameras, list) or not cameras or not isinstance(speaker, dict):
        return False
    required_camera_fields = ("name", "miot_model", "firmware", "codec")
    return all(
        isinstance(camera, dict) and all(camera.get(field) for field in required_camera_fields)
        for camera in cameras
    ) and all(speaker.get(field) for field in ("name", "miot_model", "firmware"))


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


def render_gate_report(
    events: list[dict[str, object]], inventory: dict[str, object], host_stats: str
) -> str:
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
    camera_30_pass = bool(camera_names) and all(
        any(
            event.get("component") == f"camera.{name}"
            and _camera_event_passes(event, 1800)
            for event in camera_30_events
        )
        for name in camera_names
    )

    primary = str(inventory.get("primary_camera", ""))
    primary_8h_events = [
        event
        for event in camera_events
        if event.get("component") == f"camera.{primary}"
        and event.get("operation") == "decode"
        and _details(event).get("duration_requested") == 28800
    ]
    primary_8h_pass = any(_camera_event_passes(event, 28800) for event in primary_8h_events)

    recovery_events = [
        event
        for event in camera_events
        if event.get("component") == f"camera.{primary}"
        and event.get("operation") == "recovery"
    ]
    recovery_pass = any(
        event.get("success")
        and _details(event).get("recovery_seconds") is not None
        and int(_details(event)["recovery_seconds"]) <= 60
        for event in recovery_events
    )

    memory_samples = [
        int(line.split()[1])
        for line in host_stats.splitlines()
        if line.startswith("MemAvailable:") and len(line.split()) >= 2
    ]
    no_oom = not any(
        marker in host_stats.lower() for marker in ("out of memory", "killed process")
    )
    memory_floor_pass = bool(memory_samples) and min(memory_samples) >= 768000 and no_oom
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
            "## Host Resource Floor",
            f"- **{_status(bool(memory_samples), memory_floor_pass)}**",
            "## Selected Speaker Backend",
            f"- `{selected or 'none'}`",
            "## Stop Or Continue Decision",
            f"- **{'PASS' if passed else 'FAIL'}**",
        ]
    )
