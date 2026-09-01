import cv2
import numpy as np
import pytest

from daihougou.vision.object_detector import ObjectDetector


class FakeNet:
    def __init__(self, outputs: tuple[np.ndarray, ...]) -> None:
        self.outputs = outputs
        self.input: np.ndarray | None = None
        self.backend_ids: list[int] = []
        self.target_ids: list[int] = []

    def setInput(self, blob: np.ndarray) -> None:
        self.input = blob

    def setPreferableBackend(self, backend_id: int) -> None:
        self.backend_ids.append(backend_id)

    def setPreferableTarget(self, target_id: int) -> None:
        self.target_ids.append(target_id)

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
            scores[0, 13 * 52, 15] = 0.9  # COCO cat, below the top letterbox padding
        outputs.extend((scores, boxes))
    return tuple(outputs)


def three_level_nanodet_outputs() -> tuple[np.ndarray, ...]:
    outputs: list[np.ndarray] = []
    for stride in (8, 16, 32):
        positions = (416 // stride) ** 2
        scores = np.zeros((1, positions, 80), dtype=np.float32)
        boxes = np.zeros((1, positions, 32), dtype=np.float32)
        if stride == 8:
            scores[0, 13 * 52, 15] = 0.9
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


def test_detector_accepts_the_three_levels_returned_by_the_pinned_zoo_model() -> None:
    result = ObjectDetector(model=None, net=FakeNet(three_level_nanodet_outputs())).detect(
        np.zeros((416, 416, 3), dtype=np.uint8)
    )

    assert tuple(item.label for item in result.objects) == ("cat",)


def test_detector_discards_box_that_maps_only_to_letterbox_padding() -> None:
    outputs = list(nanodet_outputs())
    outputs[0] = outputs[0].copy()
    outputs[0][0, 13 * 52, 15] = 0.0
    outputs[0][0, 0, 15] = 0.9

    result = ObjectDetector(model=None, net=FakeNet(tuple(outputs))).detect(
        np.zeros((208, 416, 3), dtype=np.uint8)
    )

    assert result.objects == ()


def test_detector_normalizes_rgb_pixels_before_network_inference() -> None:
    net = FakeNet(nanodet_outputs())
    frame = np.zeros((416, 416, 3), dtype=np.uint8)
    frame[:, :] = (0, 0, 255)  # BGR red

    ObjectDetector(model=None, net=net).detect(frame)

    assert net.input is not None
    assert net.input[0, :, 208, 208] == pytest.approx((2.64, -2.035714, -2.117904))


def test_detector_suppresses_overlapping_boxes_with_nms() -> None:
    outputs = list(nanodet_outputs())
    scores = outputs[0].copy()
    boxes = outputs[1].copy()
    scores[0, :, :] = 0.0
    scores[0, :2, 15] = 0.9
    boxes[0, :2, :] = 0.0
    boxes[0, :2, [7, 15, 23, 31]] = 20.0
    outputs[0] = scores
    outputs[1] = boxes

    result = ObjectDetector(model=None, net=FakeNet(tuple(outputs))).detect(
        np.zeros((416, 416, 3), dtype=np.uint8)
    )

    assert len(result.objects) == 1
    assert result.objects[0].label == "cat"


def test_detector_keeps_only_top_thousand_candidates_per_level_before_nms() -> None:
    outputs = list(nanodet_outputs())
    scores = outputs[0].copy()
    boxes = outputs[1].copy()
    scores[0, :, :] = 0.0
    scores[0, :1000, 15] = 0.9
    scores[0, 1000, 16] = 0.89
    boxes[0, :, :] = 0.0
    boxes[0, :, [1, 9, 17, 25]] = 20.0
    outputs[0] = scores
    outputs[1] = boxes

    result = ObjectDetector(model=None, net=FakeNet(tuple(outputs))).detect(
        np.zeros((416, 416, 3), dtype=np.uint8)
    )

    assert "dog" not in tuple(item.label for item in result.objects)


def test_detector_configures_injected_network_for_opencv_cpu() -> None:
    net = FakeNet(nanodet_outputs())

    ObjectDetector(model=None, net=net)

    assert net.backend_ids == [cv2.dnn.DNN_BACKEND_OPENCV]
    assert net.target_ids == [cv2.dnn.DNN_TARGET_CPU]
