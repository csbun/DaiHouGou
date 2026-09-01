import argparse
import json
import math
import platform
import resource
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from daihougou.object_selection import select_announced_objects
from daihougou.vision.object_detector import COCO_CATEGORIES, ObjectDetector
from daihougou.vision.person_detector import PersonDetector

PRIMARY_ACCURACY_GATE = 0.80
FALSE_ANNOUNCEMENT_GATE = 0.05
OBJECT_P95_GATE_MS = 1000
PEAK_RSS_GATE_BYTES = 1024**3
CYCLE_P95_GATE_MS = 1000
VALIDATION_CORPUS = Path("/tmp/daihougou-object-validation").resolve()


@dataclass(frozen=True)
class ManifestPage:
    file: str
    primary: str | None
    expected: tuple[str, ...]


@dataclass(frozen=True)
class PageResult:
    file: str
    primary: str | None
    expected: tuple[str, ...]
    predicted: tuple[str, ...]
    latency_ms: int


def load_manifest(path: Path) -> tuple[ManifestPage, ...]:
    """Load a local corpus manifest without allowing any path to escape it."""
    if not path.is_file():
        raise ValueError("manifest is missing")
    try:
        contents = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("manifest is not valid JSON") from error
    if not isinstance(contents, dict) or not isinstance(contents.get("pages"), list):
        raise TypeError("manifest must contain pages")

    pages: list[ManifestPage] = []
    seen_files: set[str] = set()
    corpus_root = path.parent.resolve()
    for value in contents["pages"]:
        if not isinstance(value, dict):
            raise TypeError("manifest page must be an object")
        file = value.get("file")
        primary = value.get("primary")
        expected = value.get("expected")
        if not isinstance(file, str) or not file:
            raise ValueError("manifest page file is required")
        relative_file = Path(file)
        if relative_file.is_absolute():
            raise ValueError("manifest file must be relative")
        if ".." in relative_file.parts:
            raise ValueError("manifest file must not contain parent traversal")
        if file in seen_files:
            raise ValueError("manifest contains duplicate files")
        seen_files.add(file)
        image_path = (path.parent / relative_file).resolve()
        if not image_path.is_relative_to(corpus_root) or not image_path.is_file():
            raise ValueError("manifest image is missing")
        if primary is not None and (not isinstance(primary, str) or primary not in COCO_CATEGORIES):
            raise ValueError("manifest primary must be a COCO category")
        if not isinstance(expected, list) or any(
            not isinstance(label, str) or label not in COCO_CATEGORIES for label in expected
        ):
            raise ValueError("manifest expected labels must be COCO categories")
        expected_labels = tuple(expected)
        if primary is not None and primary not in expected_labels:
            raise ValueError("manifest primary must be included in expected labels")
        pages.append(ManifestPage(file=file, primary=primary, expected=expected_labels))

    if len(pages) < 30:
        raise ValueError("manifest requires at least 30 pages")
    if sum(page.primary is not None for page in pages) < 20:
        raise ValueError("manifest requires at least 20 primary pages")
    return tuple(pages)


def _p95(values: tuple[int, ...]) -> int:
    if not values:
        raise ValueError("metrics require at least one value")
    rank = math.ceil(0.95 * len(values)) - 1
    return sorted(values)[rank]


def evaluate(
    results: tuple[PageResult, ...],
    *,
    peak_rss_bytes: int,
    cycle_ms: tuple[int, ...] | None = None,
) -> dict[str, object]:
    """Compute aggregate gates; omit cycle metrics when running object-only."""
    if not results:
        raise ValueError("metrics require at least one page")
    if cycle_ms is not None and len(cycle_ms) != len(results):
        raise ValueError("cycle count must match page count")
    primary_results = tuple(result for result in results if result.primary is not None)
    if not primary_results:
        raise ValueError("metrics require primary pages")
    primary_accuracy = sum(
        result.primary in result.predicted for result in primary_results
    ) / len(primary_results)
    false_announcement_ratio = sum(
        any(label not in result.expected for label in result.predicted) for result in results
    ) / len(results)
    object_p95_ms = _p95(tuple(result.latency_ms for result in results))
    passed = (
        primary_accuracy >= PRIMARY_ACCURACY_GATE
        and false_announcement_ratio < FALSE_ANNOUNCEMENT_GATE
        and object_p95_ms <= OBJECT_P95_GATE_MS
        and peak_rss_bytes <= PEAK_RSS_GATE_BYTES
    )
    if cycle_ms is not None:
        passed = passed and _p95(cycle_ms) <= CYCLE_P95_GATE_MS
    report = {
        "false_announcement_ratio": false_announcement_ratio,
        "object_p95_ms": object_p95_ms,
        "page_count": len(results),
        "passed": passed,
        "peak_rss_bytes": peak_rss_bytes,
        "primary_accuracy": primary_accuracy,
        "primary_page_count": len(primary_results),
    }
    if cycle_ms is not None:
        report["cycle_p95_ms"] = _p95(cycle_ms)
    return report


def peak_rss_bytes() -> int:
    maximum = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return maximum if platform.system() == "Darwin" else maximum * 1024


def _parse_args(argv: list[str] | None) -> argparse.Namespace | None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--object-model", type=Path, required=True)
    parser.add_argument("--person-model", type=Path)
    parser.add_argument("--camera-count", type=int, default=1, choices=(1, 2))
    parser.add_argument("--output", type=Path)
    try:
        return parser.parse_args(argv)
    except SystemExit as error:
        if error.code == 0:
            return None
        raise ValueError("invalid command arguments") from error


def _read_frame(corpus: Path, page: ManifestPage) -> np.ndarray[Any, Any]:
    frame = cv2.imread(str(corpus / page.file))
    if frame is None or frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("manifest image is unreadable")
    return frame


def _run_benchmark(args: argparse.Namespace) -> dict[str, object]:
    corpus = args.corpus.resolve()
    if corpus != VALIDATION_CORPUS:
        raise ValueError("corpus must use the fixed private validation directory")
    manifest_path = corpus / "manifest.json"
    pages = load_manifest(manifest_path)
    if not args.object_model.is_file():
        raise ValueError("model is missing")
    if args.camera_count == 2 and args.person_model is None:
        raise ValueError("camera-count 2 requires a person model")
    if args.person_model is not None and not args.person_model.is_file():
        raise ValueError("model is missing")
    object_detector = ObjectDetector(args.object_model)
    person_detector = (
        PersonDetector(args.person_model) if args.person_model is not None else None
    )

    warmup_frame = _read_frame(corpus, pages[0])
    for _ in range(3):
        detected = object_detector.detect(warmup_frame)
        select_announced_objects(
            detected, frame_width=warmup_frame.shape[1], frame_height=warmup_frame.shape[0]
        )
    if person_detector is not None:
        for _ in range(3):
            person_detector.detect(warmup_frame)
    del warmup_frame

    results: list[PageResult] = []
    for page in pages:
        frame = _read_frame(corpus, page)
        detected = object_detector.detect(frame)
        selected = select_announced_objects(
            detected, frame_width=frame.shape[1], frame_height=frame.shape[0]
        )
        results.append(
            PageResult(
                file=page.file,
                primary=page.primary,
                expected=page.expected,
                predicted=tuple(item.label for item in selected),
                latency_ms=detected.latency_ms,
            )
        )

    cycles: list[int] | None = None
    if person_detector is not None:
        cycles = []
        for page in pages:
            frame = _read_frame(corpus, page)
            started = time.monotonic()
            for _ in range(args.camera_count):
                person_detector.detect(frame)
                detected = object_detector.detect(frame)
                select_announced_objects(
                    detected, frame_width=frame.shape[1], frame_height=frame.shape[0]
                )
            cycles.append(round((time.monotonic() - started) * 1000))
    return evaluate(
        tuple(results), peak_rss_bytes=peak_rss_bytes(), cycle_ms=None if cycles is None else tuple(cycles)
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        if args is None:
            return 0
        report = _run_benchmark(args)
        serialized = json.dumps(report, sort_keys=True)
        if args.output is not None:
            args.output.write_text(serialized + "\n", encoding="utf-8")
        print(serialized)
        return 0 if report["passed"] else 1
    except (OSError, TypeError, ValueError, cv2.error):
        print("invalid input", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
