# Object Category Detection PoC Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove or reject NanoDet object-category detection on the target i3-3217U server and a representative local picture-book corpus before production integration.

**Architecture:** Port the fixed OpenCV Zoo NanoDet preprocessing and decoding into a small tested adapter, then drive that adapter and the existing person detector from a local-only benchmark CLI. The CLI emits metrics only; it never writes annotated images. A failing gate ends execution and sends the design back to model/category selection.

**Tech Stack:** Python 3.12, NumPy 2.5, OpenCV DNN 4.14, pytest 9, JSON

**Spec:** `docs/superpowers/specs/2026-08-31-object-category-announcement-design.md`

## Global Constraints

- Run on the target i3-3217U / 4 GiB / CPU-only server.
- Use NanoDet FP32 from OpenCV Zoo commit `47534e27c9851bb1128ccc0102f1145e27f23f98`.
- Verify SHA384 `84ee6a6dd605f7019f25a81615a8fff886b235e8d3924930ca367c6e239a8c6d9c14a7e60b8bae54edca040cbf7b86e7` before inference.
- Read validation images only from `/tmp/daihougou-object-validation`; never commit, upload, copy, or annotate them.
- Require at least 30 manifest pages and at least 20 pages with a non-null `primary` label.
- Pass only with primary accuracy >= 0.80, false-announcement page ratio < 0.05, object p95 <= 1000 ms, dual-model peak RSS <= 1 GiB, and two-camera p95 cycle <= 1000 ms.
- Stop this plan after Task 3 if any gate fails. Do not start the production implementation plan.

---

### Task 1: Tested NanoDet Adapter

**Files:**
- Create: `src/daihougou/vision/object_detector.py`
- Create: `src/daihougou/object_selection.py`
- Create: `tests/app/vision/test_object_detector.py`
- Create: `tests/app/test_object_selection.py`

**Interfaces:**
- Consumes: BGR `numpy.ndarray` frames and an OpenCV-compatible network.
- Produces: `DetectedObject`, `ObjectDetection`, `COCO_CATEGORIES`, `ObjectDetector.detect(frame) -> ObjectDetection`, and `select_announced_objects`.

- [ ] **Step 1: Write the failing adapter tests**

```python
import numpy as np

from daihougou.vision.object_detector import ObjectDetector


class FakeNet:
    def __init__(self, outputs: tuple[np.ndarray, ...]) -> None:
        self.outputs = outputs
        self.input: np.ndarray | None = None

    def setInput(self, blob: np.ndarray) -> None:
        self.input = blob

    def getUnconnectedOutLayersNames(self) -> tuple[str, ...]:
        return tuple(str(index) for index in range(8))

    def forward(self, names: tuple[str, ...]) -> tuple[np.ndarray, ...]:
        return self.outputs


def nanodet_outputs() -> tuple[np.ndarray, ...]:
    outputs: list[np.ndarray] = []
    for stride in (8, 16, 32, 64):
        positions = (416 // stride) ** 2
        scores = np.zeros((1, positions, 80), dtype=np.float32)
        boxes = np.zeros((1, positions, 32), dtype=np.float32)
        if stride == 8:
            scores[0, 0, 15] = 0.9  # COCO cat
        outputs.extend((scores, boxes))
    return tuple(outputs)


def test_detector_decodes_cat_and_reports_original_frame_coordinates() -> None:
    net = FakeNet(nanodet_outputs())
    detector = ObjectDetector(model=None, net=net)

    result = detector.detect(np.zeros((208, 416, 3), dtype=np.uint8))

    assert len(result.objects) == 1
    detected = result.objects[0]
    assert detected.label == "cat"
    assert detected.confidence == 0.9
    assert 0 <= detected.left < detected.right <= 416
    assert 0 <= detected.top < detected.bottom <= 208
    assert net.input is not None
    assert net.input.shape == (1, 3, 416, 416)


def test_detector_returns_empty_tuple_when_every_score_is_below_threshold() -> None:
    outputs = tuple(np.zeros_like(output) for output in nanodet_outputs())

    result = ObjectDetector(model=None, net=FakeNet(outputs)).detect(
        np.zeros((416, 416, 3), dtype=np.uint8)
    )

    assert result.objects == ()
```

Create `tests/app/test_object_selection.py` with the domain filtering contract:

```python
from daihougou.object_selection import select_announced_objects
from daihougou.vision.object_detector import DetectedObject, ObjectDetection


def obj(label: str, confidence: float, box: tuple[int, int, int, int]) -> DetectedObject:
    return DetectedObject(label, confidence, *box)


def test_selection_filters_person_and_large_book_then_deduplicates_top_three() -> None:
    result = ObjectDetection(
        objects=(
            obj("person", 0.99, (0, 0, 20, 20)),
            obj("book", 0.95, (0, 0, 416, 300)),
            obj("dog", 0.90, (0, 0, 20, 20)),
            obj("sports ball", 0.85, (0, 0, 20, 20)),
            obj("cat", 0.80, (0, 0, 20, 20)),
            obj("cat", 0.70, (0, 0, 20, 20)),
            obj("bird", 0.60, (0, 0, 20, 20)),
        ),
        latency_ms=200,
    )

    selected = select_announced_objects(result, frame_width=416, frame_height=416)

    assert tuple(item.label for item in selected) == ("dog", "sports ball", "cat")
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `pytest tests/app/vision/test_object_detector.py -v`

Expected: collection fails with `ModuleNotFoundError: No module named 'daihougou.vision.object_detector'`.

- [ ] **Step 3: Implement the minimal adapter**

```python
@dataclass(frozen=True)
class DetectedObject:
    label: str
    confidence: float
    left: int
    top: int
    right: int
    bottom: int

    def area_ratio(self, frame_width: int, frame_height: int) -> float:
        area = max(0, self.right - self.left) * max(0, self.bottom - self.top)
        return area / (frame_width * frame_height)


@dataclass(frozen=True)
class ObjectDetection:
    objects: tuple[DetectedObject, ...]
    latency_ms: int


class ObjectDetector:
    image_size = 416
    strides = (8, 16, 32, 64)
    reg_max = 7
```

Give `ObjectDetector.__init__` the exact parameters `model: Path | None`, `probability_threshold: float = 0.35`, `iou_threshold: float = 0.60`, and `net: Network | None = None`. Give `detect` the exact signature `detect(frame: npt.NDArray[np.uint8]) -> ObjectDetection`. Port `letterbox`, anchor generation, distribution decoding, and `cv2.dnn.NMSBoxes` from the fixed OpenCV Zoo implementation. Convert BGR to RGB before letterboxing, map boxes back to original-frame coordinates, set OpenCV CPU backend/target for a real network, use the exact 80 labels from the spec source, and round confidence to six decimal places. Do not add drawing, saving, camera capture, CLI, or mutable global network state.

Implement `select_announced_objects(result: ObjectDetection, *, frame_width: int, frame_height: int) -> tuple[DetectedObject, ...]` in `object_selection.py`. Filter `person`; filter `book` when `area_ratio(frame_width, frame_height) >= 0.50`; retain the highest-confidence object per label; sort by `(-confidence, label)`; return the first three.

- [ ] **Step 4: Run focused and existing vision tests**

Run: `pytest tests/app/vision/test_object_detector.py tests/app/test_object_selection.py tests/app/vision/test_person_detector.py -v`

Expected: all tests pass.

- [ ] **Step 5: Run lint on the adapter**

Run: `ruff check src/daihougou/vision/object_detector.py src/daihougou/object_selection.py tests/app/vision/test_object_detector.py tests/app/test_object_selection.py`

Expected: exit 0 with no diagnostics.

- [ ] **Step 6: Commit**

```bash
git add src/daihougou/vision/object_detector.py src/daihougou/object_selection.py tests/app/vision/test_object_detector.py tests/app/test_object_selection.py
git commit -m "feat: add NanoDet object detector adapter"
```

### Task 2: Local-Only Validation CLI

**Files:**
- Create: `tools/__init__.py`
- Create: `tools/object_detection_poc.py`
- Create: `tests/tools/__init__.py`
- Create: `tests/tools/test_object_detection_poc.py`

**Interfaces:**
- Consumes: `<corpus>/manifest.json`, page images, NanoDet model, existing person model.
- Produces: stdout JSON and optional metrics-only JSON at `--output`; exit 0 for a passed gate, exit 1 for a measured gate failure, exit 2 for invalid input.

- [ ] **Step 1: Write failing manifest and metric tests**

```python
import json
from pathlib import Path

import pytest

from tools.object_detection_poc import PageResult, evaluate, load_manifest


def test_manifest_requires_thirty_pages_and_twenty_primary_labels(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"pages": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="at least 30 pages"):
        load_manifest(path)


def test_evaluate_computes_gate_metrics_without_image_details() -> None:
    results = tuple(
        PageResult(
            file=f"page-{index}.jpg",
            primary="cat" if index < 20 else None,
            expected=("cat",) if index < 20 else (),
            predicted=("cat",) if index < 18 else (),
            latency_ms=200 + index,
        )
        for index in range(30)
    )

    report = evaluate(results, peak_rss_bytes=512 * 1024**2, cycle_ms=(500,) * 30)

    assert report["page_count"] == 30
    assert report["primary_accuracy"] == 0.9
    assert report["false_announcement_ratio"] == 0.0
    assert report["object_p95_ms"] == 228
    assert report["peak_rss_bytes"] == 512 * 1024**2
    assert report["passed"] is True
    assert "files" not in report
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `pytest tests/tools/test_object_detection_poc.py -v`

Expected: collection fails because `tools.object_detection_poc` does not exist.

- [ ] **Step 3: Implement manifest validation and pure metrics**

```python
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


PRIMARY_ACCURACY_GATE = 0.80
FALSE_ANNOUNCEMENT_GATE = 0.05
OBJECT_P95_GATE_MS = 1000
PEAK_RSS_GATE_BYTES = 1024**3
CYCLE_P95_GATE_MS = 1000
```

Implement `load_manifest(path: Path) -> tuple[ManifestPage, ...]` and `evaluate(results: tuple[PageResult, ...], *, peak_rss_bytes: int, cycle_ms: tuple[int, ...]) -> dict[str, object]`. Reject absolute file names, `..`, missing files, labels outside `COCO_CATEGORIES`, fewer than 30 pages, fewer than 20 primary pages, duplicate manifest file names, and primary labels absent from `expected`. Compute p95 using nearest-rank `ceil(0.95 * count) - 1`. Emit aggregate values only; never include file names, expected labels, predictions, paths, boxes, or images in the report.

- [ ] **Step 4: Add the benchmark command**

```python
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--object-model", type=Path, required=True)
    parser.add_argument("--person-model", type=Path, required=True)
    parser.add_argument("--camera-count", type=int, default=2, choices=(2,))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    # Load validated pages, warm each model three times, measure all pages,
    # then run a two-camera alternating person/object cycle for every page.
```

Use `cv2.imread` and fail closed on unreadable images. Pass every detector result through `select_announced_objects`; do not duplicate filtering in the CLI. Warm up both models three times before measuring. Measure process peak RSS with `resource.getrusage`, normalizing macOS bytes and Linux KiB to bytes. Print `json.dumps(report, sort_keys=True)` and write the same aggregate JSON only when `--output` is present.

- [ ] **Step 5: Run focused tests and lint**

Run: `pytest tests/tools/test_object_detection_poc.py -v`

Expected: all tests pass.

Run: `ruff check tools/object_detection_poc.py tests/tools/test_object_detection_poc.py`

Expected: exit 0 with no diagnostics.

- [ ] **Step 6: Commit**

```bash
git add tools/__init__.py tools/object_detection_poc.py tests/tools/__init__.py tests/tools/test_object_detection_poc.py
git commit -m "test: add local object detection acceptance harness"
```

### Task 3: Execute the Target-Hardware Gate

**Files:**
- Create on pass: `docs/validation/object-category-announcement-poc.md`
- Do not create in Git: `$OBJECT_VALIDATION_DIR/**`

**Interfaces:**
- Consumes: target server, validated local corpus, fixed model artifacts.
- Produces: a pass/fail aggregate report and an explicit go/no-go decision.

- [ ] **Step 1: Validate the private corpus location**

Run:

```bash
test -d /tmp/daihougou-object-validation
test -f /tmp/daihougou-object-validation/manifest.json
test "$(realpath /tmp/daihougou-object-validation)" = /tmp/daihougou-object-validation
```

Expected: all commands exit 0. The last line requires the corpus to be outside the repository or explicitly ignored.

- [ ] **Step 2: Download and verify NanoDet on the target server**

```bash
curl --fail --show-error --location \
  https://media.githubusercontent.com/media/opencv/opencv_zoo/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_nanodet/object_detection_nanodet_2022nov.onnx \
  --output /tmp/object_detection_nanodet_2022nov.onnx
printf '%s  %s\n' \
  84ee6a6dd605f7019f25a81615a8fff886b235e8d3924930ca367c6e239a8c6d9c14a7e60b8bae54edca040cbf7b86e7 \
  /tmp/object_detection_nanodet_2022nov.onnx | sha384sum --check
```

Expected: checksum reports `OK`.

- [ ] **Step 3: Run the full PoC gate**

```bash
python tools/object_detection_poc.py \
  --corpus /tmp/daihougou-object-validation \
  --object-model /tmp/object_detection_nanodet_2022nov.onnx \
  --person-model /opt/daihougou/models/person_detection_mediapipe_2023mar.onnx \
  --camera-count 2 \
  --output /tmp/object-category-announcement-poc.json
```

Expected: exit 0 with JSON field `"passed": true`, or exit 1 with `"passed": false`. Both measured outcomes continue only to the report and cleanup steps. An exit 2 means invalid input and must be corrected before the measurement is accepted.

- [ ] **Step 4: Write the passing validation report**

Create `docs/validation/object-category-announcement-poc.md` with the exact hardware CPU/RAM/OS, model commit/SHA384, corpus counts, every aggregate metric from the JSON output, and candidate thresholds `0.35`, `0.60`, and `0.50`. End a passing report with `Decision: proceed with production implementation`; end a failing report with `Decision: do not proceed; return to model or category selection`. Do not include corpus paths, file names, images, book titles, or raw predictions.

- [ ] **Step 5: Delete private and temporary validation artifacts**

```bash
rm /tmp/object_detection_nanodet_2022nov.onnx
rm /tmp/object-category-announcement-poc.json
rm -r /tmp/daihougou-object-validation
```

Expected: the model, aggregate temporary JSON, and validation corpus are removed. Do not run these commands until the report has been reviewed for completeness.

- [ ] **Step 6: Commit the metrics-only report**

```bash
git add docs/validation/object-category-announcement-poc.md
git commit -m "docs: record object detection PoC result"
```

If the committed report says `Decision: do not proceed`, stop here and do not execute the production implementation plan.

- [ ] **Step 7: Run the PoC plan verification**

Run: `pytest tests/app/vision/test_object_detector.py tests/app/test_object_selection.py tests/tools/test_object_detection_poc.py -v`

Expected: all tests pass.

Run: `ruff check src/daihougou/vision/object_detector.py src/daihougou/object_selection.py tools/object_detection_poc.py tests/app/vision/test_object_detector.py tests/app/test_object_selection.py tests/tools/test_object_detection_poc.py`

Expected: exit 0 with no diagnostics.
