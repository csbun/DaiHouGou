from pathlib import Path

import numpy as np
import pytest

from guduck.vision.person_detector import PersonDetector


class FakeNet:
    def __init__(self, outputs: tuple[np.ndarray, np.ndarray]) -> None:
        self.outputs = outputs
        self.inputs: list[np.ndarray] = []
        self.requested_outputs: list[tuple[str, ...]] = []

    def setInput(self, blob: np.ndarray) -> None:
        self.inputs.append(blob)

    def getUnconnectedOutLayersNames(self) -> tuple[str, ...]:
        return ("Identity:0", "Identity_1:0")

    def forward(self, output_names: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray]:
        self.requested_outputs.append(output_names)
        return self.outputs


def model_outputs(*logits: float) -> tuple[np.ndarray, np.ndarray]:
    boxes = np.zeros((1, len(logits), 12), dtype=np.float32)
    scores = np.array([[[logit] for logit in logits]], dtype=np.float32)
    return boxes, scores


def test_detector_returns_highest_person_confidence() -> None:
    net = FakeNet(model_outputs(-1000.0, 1.0, 0.5))
    detector = PersonDetector(Path("model.onnx"), 0.55, net=net)
    frame = np.zeros((100, 222, 3), dtype=np.uint8)
    frame[:, :, 2] = 255

    with np.errstate(over="raise"):
        result = detector.detect(frame)

    assert result.person_present is True
    assert result.confidence == pytest.approx(1 / (1 + np.exp(-1.0)))
    assert result.latency_ms >= 0
    assert net.inputs[0].shape == (1, 3, 224, 224)
    assert net.inputs[0].dtype == np.float32
    assert net.inputs[0][0, :, 0, 0] == pytest.approx([0.0, 0.0, 0.0])
    assert net.inputs[0][0, :, 61, 112] == pytest.approx([0.0, 0.0, 0.0])
    assert net.inputs[0][0, :, 62, 112] == pytest.approx([1.0, -1.0, -1.0])
    assert net.inputs[0][0, :, 112, 112] == pytest.approx([1.0, -1.0, -1.0])
    assert net.requested_outputs == [("Identity:0", "Identity_1:0")]


def test_detector_reports_absent_below_threshold() -> None:
    net = FakeNet(model_outputs(0.1))
    detector = PersonDetector(Path("model.onnx"), 0.55, net=net)

    result = detector.detect(np.zeros((256, 256, 3), dtype=np.uint8))

    assert result.person_present is False
    assert result.confidence == pytest.approx(1 / (1 + np.exp(-0.1)))
