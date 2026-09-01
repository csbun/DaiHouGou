import json
from pathlib import Path

import numpy as np
import pytest

from tools.object_detection_poc import (
    PageResult,
    evaluate,
    load_manifest,
    main,
    peak_rss_bytes,
)


def write_corpus(
    tmp_path: Path, *, expected: tuple[str, ...] = ("cat",), expected_on_all_pages: bool = False
) -> Path:
    corpus = tmp_path / "private-corpus"
    corpus.mkdir()
    pages = []
    for index in range(30):
        filename = f"private-page-{index:03}.jpg"
        (corpus / filename).write_bytes(b"image")
        pages.append(
            {
                "file": filename,
                "primary": "cat" if index < 20 else None,
                "expected": list(expected) if index < 20 or expected_on_all_pages else [],
            }
        )
    (corpus / "manifest.json").write_text(json.dumps({"pages": pages}), encoding="utf-8")
    return corpus


def test_manifest_rejects_fewer_than_thirty_pages_before_measurement(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"pages": []}), encoding="utf-8")

    with pytest.raises(ValueError, match="at least 30 pages"):
        load_manifest(path)


@pytest.mark.parametrize(
    ("page", "message"),
    (
        ({"file": "/private.jpg", "primary": None, "expected": []}, "relative"),
        ({"file": "../private.jpg", "primary": None, "expected": []}, "parent"),
        ({"file": "missing.jpg", "primary": None, "expected": []}, "missing"),
        ({"file": "page.jpg", "primary": "unicorn", "expected": ["unicorn"]}, "COCO"),
        ({"file": "page.jpg", "primary": "cat", "expected": []}, "primary"),
    ),
)
def test_manifest_rejects_invalid_page_data_that_could_escape_the_private_corpus(
    tmp_path: Path, page: dict[str, object], message: str
) -> None:
    (tmp_path / "page.jpg").write_bytes(b"image")
    pages = [page] * 30
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"pages": pages}), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_manifest(path)


def test_manifest_rejects_duplicate_files_and_fewer_than_twenty_primary_pages(tmp_path: Path) -> None:
    corpus = write_corpus(tmp_path, expected_on_all_pages=True)
    pages = json.loads((corpus / "manifest.json").read_text(encoding="utf-8"))["pages"]
    pages[1]["file"] = pages[0]["file"]
    (corpus / "manifest.json").write_text(json.dumps({"pages": pages}), encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate"):
        load_manifest(corpus / "manifest.json")

    for page in pages:
        page["file"] = f"page-{pages.index(page):03}.jpg"
        (corpus / page["file"]).write_bytes(b"image")
        page["primary"] = None
        page["expected"] = []
    (corpus / "manifest.json").write_text(json.dumps({"pages": pages}), encoding="utf-8")

    with pytest.raises(ValueError, match="at least 20"):
        load_manifest(corpus / "manifest.json")


def test_evaluate_computes_gates_with_aggregate_metrics_only() -> None:
    results = tuple(
        PageResult(
            file=f"private-page-{index}.jpg",
            primary="cat" if index < 20 else None,
            expected=("cat",) if index < 20 else (),
            predicted=("cat",) if index < 18 else (),
            latency_ms=200 + index,
        )
        for index in range(30)
    )

    report = evaluate(results, peak_rss_bytes=512 * 1024**2, cycle_ms=(500,) * 30)

    assert report == {
        "cycle_p95_ms": 500,
        "false_announcement_ratio": 0.0,
        "object_p95_ms": 228,
        "page_count": 30,
        "passed": True,
        "peak_rss_bytes": 512 * 1024**2,
        "primary_accuracy": 0.9,
    }
    serialized = json.dumps(report)
    assert "private-page" not in serialized
    assert '"cat"' not in serialized


def test_evaluate_returns_failed_gate_for_each_measured_threshold() -> None:
    results = tuple(
        PageResult(
            file=f"page-{index}.jpg",
            primary="cat" if index < 20 else None,
            expected=("cat",) if index < 20 else (),
            predicted=("dog",) if index == 0 else (("cat",) if index < 15 else ()),
            latency_ms=1001 if index == 29 else 1,
        )
        for index in range(30)
    )

    report = evaluate(results, peak_rss_bytes=1024**3 + 1, cycle_ms=(1001,) * 30)

    assert report["primary_accuracy"] == 0.7
    assert report["false_announcement_ratio"] == pytest.approx(1 / 30)
    assert report["object_p95_ms"] == 1
    assert report["passed"] is False


def test_peak_rss_converts_darwin_bytes_and_linux_kibibytes(monkeypatch: pytest.MonkeyPatch) -> None:
    class Usage:
        ru_maxrss = 321

    monkeypatch.setattr("tools.object_detection_poc.resource.getrusage", lambda _: Usage())
    monkeypatch.setattr("tools.object_detection_poc.platform.system", lambda: "Darwin")
    assert peak_rss_bytes() == 321

    monkeypatch.setattr("tools.object_detection_poc.platform.system", lambda: "Linux")
    assert peak_rss_bytes() == 321 * 1024


def test_main_returns_two_for_invalid_inputs_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing = tmp_path / "missing"

    assert main(["--corpus", str(missing), "--object-model", "missing", "--person-model", "missing"]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err


def test_main_rejects_corpus_outside_fixed_private_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = write_corpus(tmp_path)
    object_model = tmp_path / "object.onnx"
    person_model = tmp_path / "person.onnx"
    object_model.write_bytes(b"model")
    person_model.write_bytes(b"model")
    monkeypatch.setattr(
        "tools.object_detection_poc.load_manifest",
        lambda _: (_ for _ in ()).throw(AssertionError("manifest must not be read")),
    )

    assert main(
        [
            "--corpus",
            str(corpus),
            "--object-model",
            str(object_model),
            "--person-model",
            str(person_model),
        ]
    ) == 2

    assert capsys.readouterr().out == ""


def test_main_returns_two_when_a_manifest_image_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    corpus = write_corpus(tmp_path)
    monkeypatch.setattr("tools.object_detection_poc.VALIDATION_CORPUS", corpus.resolve())
    object_model = tmp_path / "object.onnx"
    person_model = tmp_path / "person.onnx"
    object_model.write_bytes(b"model")
    person_model.write_bytes(b"model")
    monkeypatch.setattr("tools.object_detection_poc.cv2.imread", lambda _: None)

    assert main(["--corpus", str(corpus), "--object-model", str(object_model), "--person-model", str(person_model)]) == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Traceback" not in captured.err


def test_main_warms_models_measures_pages_and_runs_serialized_two_camera_cycles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from daihougou.vision.object_detector import DetectedObject
    from daihougou.vision.person_detector import PersonDetection

    corpus = write_corpus(tmp_path, expected_on_all_pages=True)
    monkeypatch.setattr("tools.object_detection_poc.VALIDATION_CORPUS", corpus.resolve())
    object_model = tmp_path / "object.onnx"
    person_model = tmp_path / "person.onnx"
    output = tmp_path / "metrics.json"
    object_model.write_bytes(b"model")
    person_model.write_bytes(b"model")
    trace: list[str] = []
    selection_accesses: list[None] = []
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    class TracedObjectDetection:
        latency_ms = 7

        @property
        def objects(self) -> tuple[DetectedObject, ...]:
            selection_accesses.append(None)
            return (
                DetectedObject("person", 0.99, 0, 0, 10, 10),
                DetectedObject("book", 0.95, 0, 0, 100, 100),
                DetectedObject("cat", 0.90, 0, 0, 10, 10),
            )

    class FakeObjectDetector:
        def __init__(self, _: Path) -> None:
            pass

        def detect(self, _: np.ndarray) -> TracedObjectDetection:
            trace.append("object")
            return TracedObjectDetection()

    class FakePersonDetector:
        def __init__(self, _: Path) -> None:
            pass

        def detect(self, _: np.ndarray) -> PersonDetection:
            trace.append("person")
            return PersonDetection(False, 0.1, 3)

    monkeypatch.setattr("tools.object_detection_poc.cv2.imread", lambda _: frame)
    monkeypatch.setattr("tools.object_detection_poc.ObjectDetector", FakeObjectDetector)
    monkeypatch.setattr("tools.object_detection_poc.PersonDetector", FakePersonDetector)
    monkeypatch.setattr("tools.object_detection_poc.peak_rss_bytes", lambda: 123)

    assert main(
        [
            "--corpus",
            str(corpus),
            "--object-model",
            str(object_model),
            "--person-model",
            str(person_model),
            "--output",
            str(output),
        ]
    ) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["page_count"] == 30
    assert report["primary_accuracy"] == 1.0
    assert report["false_announcement_ratio"] == 0.0
    assert report["peak_rss_bytes"] == 123
    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert trace[:6] == ["object", "object", "object", "person", "person", "person"]
    assert trace[6:36] == ["object"] * 30
    assert len(trace) == 156
    assert all(trace[index : index + 4] == ["person", "object", "person", "object"] for index in range(36, 156, 4))
    assert len(selection_accesses) == 93


def test_main_returns_one_for_a_measured_gate_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from daihougou.vision.object_detector import ObjectDetection
    from daihougou.vision.person_detector import PersonDetection

    corpus = write_corpus(tmp_path)
    monkeypatch.setattr("tools.object_detection_poc.VALIDATION_CORPUS", corpus.resolve())
    object_model = tmp_path / "object.onnx"
    person_model = tmp_path / "person.onnx"
    object_model.write_bytes(b"model")
    person_model.write_bytes(b"model")
    frame = np.zeros((100, 100, 3), dtype=np.uint8)

    class FakeObjectDetector:
        def __init__(self, _: Path) -> None:
            pass

        def detect(self, _: np.ndarray) -> ObjectDetection:
            return ObjectDetection((), latency_ms=1001)

    class FakePersonDetector:
        def __init__(self, _: Path) -> None:
            pass

        def detect(self, _: np.ndarray) -> PersonDetection:
            return PersonDetection(False, 0.1, 3)

    monkeypatch.setattr("tools.object_detection_poc.cv2.imread", lambda _: frame)
    monkeypatch.setattr("tools.object_detection_poc.ObjectDetector", FakeObjectDetector)
    monkeypatch.setattr("tools.object_detection_poc.PersonDetector", FakePersonDetector)
    monkeypatch.setattr("tools.object_detection_poc.peak_rss_bytes", lambda: 1)

    assert main(["--corpus", str(corpus), "--object-model", str(object_model), "--person-model", str(person_model)]) == 1

    assert json.loads(capsys.readouterr().out)["passed"] is False
