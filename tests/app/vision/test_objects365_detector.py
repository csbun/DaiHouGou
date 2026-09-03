import hashlib

import numpy as np
import pytest

from daihougou.vision.objects365_detector import (
    OBJECTS365_CATEGORIES,
    Objects365ObjectDetector,
)


class FakeNet:
    def __init__(self, output: np.ndarray) -> None:
        self.output = output
        self.input: np.ndarray | None = None
        self.backend: int | None = None
        self.target: int | None = None

    def setInput(self, blob: np.ndarray) -> None:
        self.input = blob

    def getUnconnectedOutLayersNames(self) -> tuple[str, ...]:
        return ("output0",)

    def forward(self, output_names: tuple[str, ...]) -> tuple[np.ndarray, ...]:
        assert output_names == ("output0",)
        return (self.output,)

    def setPreferableBackend(self, backend_id: int) -> None:
        self.backend = backend_id

    def setPreferableTarget(self, target_id: int) -> None:
        self.target = target_id


def test_objects365_detector_returns_common_detection_with_original_coordinates() -> None:
    # 200x100 -> 416x208 with 104px top padding. The model-space box maps to
    # (50, 25, 150, 75) in the source image.
    output = np.array(
        [[[208.0], [208.0], [208.0], [104.0], [0.01], [0.90], [0.02]]],
        dtype=np.float32,
    )
    net = FakeNet(output)
    detector = Objects365ObjectDetector(
        model=None,
        categories=("person", "rabbit", "book"),
        net=net,
    )

    result = detector.detect(np.zeros((100, 200, 3), dtype=np.uint8))

    assert len(result.objects) == 1
    detected = result.objects[0]
    assert detected.label == "rabbit"
    assert detected.confidence == 0.9
    assert (detected.left, detected.top, detected.right, detected.bottom) == (
        50,
        25,
        150,
        75,
    )
    assert result.latency_ms >= 0
    assert net.input is not None
    assert net.input.shape == (1, 3, 416, 416)
    np.testing.assert_allclose(net.input[0, :, 0, 0], 114 / 255, rtol=1e-5)


def test_objects365_detector_uses_the_fixed_model_vocabulary_by_default() -> None:
    assert len(OBJECTS365_CATEGORIES) == 365
    assert len(set(OBJECTS365_CATEGORIES)) == 365
    assert hashlib.sha256("\0".join(OBJECTS365_CATEGORIES).encode()).hexdigest() == (
        "45e5576d343c31af8646a0d10daaaeb43449ab323ccf9ab9ddfd42269c0845bf"
    )
    assert OBJECTS365_CATEGORIES[0] == "person"
    assert OBJECTS365_CATEGORIES[97] == "dog"
    assert OBJECTS365_CATEGORIES[146] == "cat"
    assert OBJECTS365_CATEGORIES[334] == "lion"
    assert OBJECTS365_CATEGORIES[353] == "rabbit"

    output = np.zeros((1, 369, 1), dtype=np.float32)
    output[0, :4, 0] = (208, 208, 100, 100)
    output[0, 4 + 353, 0] = 0.9
    detector = Objects365ObjectDetector(model=None, net=FakeNet(output))

    result = detector.detect(np.zeros((416, 416, 3), dtype=np.uint8))

    assert tuple(item.label for item in result.objects) == ("rabbit",)


def test_objects365_detector_applies_nms_per_class_and_drops_low_scores() -> None:
    output = np.zeros((1, 7, 4), dtype=np.float32)
    output[0, :4, :] = np.array(
        [
            [100, 100, 80, 80],
            [100, 100, 80, 80],
            [100, 100, 80, 80],
            [300, 300, 20, 20],
        ],
        dtype=np.float32,
    ).T
    output[0, 5, 0] = 0.90  # rabbit
    output[0, 5, 1] = 0.80  # overlapping rabbit, suppressed
    output[0, 6, 2] = 0.85  # overlapping book, retained
    output[0, 6, 3] = 0.20  # low-confidence book, dropped
    detector = Objects365ObjectDetector(
        model=None,
        categories=("person", "rabbit", "book"),
        net=FakeNet(output),
    )

    result = detector.detect(np.zeros((416, 416, 3), dtype=np.uint8))

    assert tuple(item.label for item in result.objects) == ("rabbit", "book")


def test_objects365_detector_rejects_end_to_end_or_unknown_output_shapes() -> None:
    detector = Objects365ObjectDetector(
        model=None,
        categories=("person", "rabbit", "book"),
        net=FakeNet(np.zeros((1, 300, 6), dtype=np.float32)),
    )

    with pytest.raises(
        ValueError,
        match="^Objects365 model must use a one-to-many export$",
    ):
        detector.detect(np.zeros((416, 416, 3), dtype=np.uint8))
