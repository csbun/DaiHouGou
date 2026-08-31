import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
import numpy.typing as npt

COCO_CATEGORIES = (
    "person",
    "bicycle",
    "car",
    "motorcycle",
    "airplane",
    "bus",
    "train",
    "truck",
    "boat",
    "traffic light",
    "fire hydrant",
    "stop sign",
    "parking meter",
    "bench",
    "bird",
    "cat",
    "dog",
    "horse",
    "sheep",
    "cow",
    "elephant",
    "bear",
    "zebra",
    "giraffe",
    "backpack",
    "umbrella",
    "handbag",
    "tie",
    "suitcase",
    "frisbee",
    "skis",
    "snowboard",
    "sports ball",
    "kite",
    "baseball bat",
    "baseball glove",
    "skateboard",
    "surfboard",
    "tennis racket",
    "bottle",
    "wine glass",
    "cup",
    "fork",
    "knife",
    "spoon",
    "bowl",
    "banana",
    "apple",
    "sandwich",
    "orange",
    "broccoli",
    "carrot",
    "hot dog",
    "pizza",
    "donut",
    "cake",
    "chair",
    "couch",
    "potted plant",
    "bed",
    "dining table",
    "toilet",
    "tv",
    "laptop",
    "mouse",
    "remote",
    "keyboard",
    "cell phone",
    "microwave",
    "oven",
    "toaster",
    "sink",
    "refrigerator",
    "book",
    "clock",
    "vase",
    "scissors",
    "teddy bear",
    "hair drier",
    "toothbrush",
)


class Network(Protocol):
    def setInput(self, blob: npt.NDArray[np.float32]) -> None: ...

    def getUnconnectedOutLayersNames(self) -> tuple[str, ...]: ...

    def forward(
        self, output_names: tuple[str, ...]
    ) -> tuple[npt.NDArray[np.float32], ...]: ...

    def setPreferableBackend(self, backend_id: int) -> None: ...

    def setPreferableTarget(self, target_id: int) -> None: ...


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

    def __init__(
        self,
        model: Path | None,
        probability_threshold: float = 0.35,
        iou_threshold: float = 0.60,
        net: Network | None = None,
    ) -> None:
        self._probability_threshold = probability_threshold
        self._iou_threshold = iou_threshold
        self._net = net if net is not None else cv2.dnn.readNetFromONNX(str(model))
        if net is None:
            self._net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self._net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self._output_names = tuple(self._net.getUnconnectedOutLayersNames())
        self._project = np.arange(self.reg_max + 1, dtype=np.float32)
        self._mean = np.array([103.53, 116.28, 123.675], dtype=np.float32).reshape(1, 1, 3)
        self._std = np.array([57.375, 57.12, 58.395], dtype=np.float32).reshape(1, 1, 3)
        self._anchors = tuple(self._anchors_for_stride(stride) for stride in self.strides)

    def detect(self, frame: npt.NDArray[np.uint8]) -> ObjectDetection:
        started = time.monotonic()
        original_height, original_width = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        letterboxed, letterbox_scale = self._letterbox(rgb)
        normalized = (letterboxed.astype(np.float32) - self._mean) / self._std
        blob = cv2.dnn.blobFromImage(normalized)
        self._net.setInput(blob)
        outputs = self._net.forward(self._output_names)
        objects = self._decode(outputs, original_width, original_height, letterbox_scale)
        return ObjectDetection(
            objects=objects,
            latency_ms=round((time.monotonic() - started) * 1000),
        )

    def _anchors_for_stride(self, stride: int) -> npt.NDArray[np.float32]:
        feature_size = self.image_size // stride
        shift_x = np.arange(feature_size, dtype=np.float32) * stride
        shift_y = np.arange(feature_size, dtype=np.float32) * stride
        x_values, y_values = np.meshgrid(shift_x, shift_y)
        return np.column_stack(
            (x_values.ravel() + 0.5 * (stride - 1), y_values.ravel() + 0.5 * (stride - 1))
        )

    def _letterbox(
        self, image: npt.NDArray[np.uint8]
    ) -> tuple[npt.NDArray[np.uint8], tuple[int, int, int, int]]:
        height, width = image.shape[:2]
        top = 0
        left = 0
        new_height = self.image_size
        new_width = self.image_size
        if height != width:
            scale = height / width
            if scale > 1:
                new_width = int(self.image_size / scale)
                resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)
                left = int((self.image_size - new_width) * 0.5)
                letterboxed = cv2.copyMakeBorder(
                    resized,
                    0,
                    0,
                    left,
                    self.image_size - new_width - left,
                    cv2.BORDER_CONSTANT,
                    value=0,
                )
            else:
                new_height = int(self.image_size * scale)
                resized = cv2.resize(image, (new_width, new_height), interpolation=cv2.INTER_AREA)
                top = int((self.image_size - new_height) * 0.5)
                letterboxed = cv2.copyMakeBorder(
                    resized,
                    top,
                    self.image_size - new_height - top,
                    0,
                    0,
                    cv2.BORDER_CONSTANT,
                    value=0,
                )
        else:
            letterboxed = cv2.resize(
                image, (self.image_size, self.image_size), interpolation=cv2.INTER_AREA
            )
        return letterboxed, (top, left, new_height, new_width)

    def _decode(
        self,
        outputs: tuple[npt.NDArray[np.float32], ...],
        original_width: int,
        original_height: int,
        letterbox_scale: tuple[int, int, int, int],
    ) -> tuple[DetectedObject, ...]:
        boxes_by_level: list[npt.NDArray[np.float32]] = []
        scores_by_level: list[npt.NDArray[np.float32]] = []
        class_scores, box_distributions = outputs[::2], outputs[1::2]
        for stride, scores, distributions, anchors in zip(
            self.strides, class_scores, box_distributions, self._anchors, strict=True
        ):
            scores = np.squeeze(scores, axis=0) if scores.ndim == 3 else scores
            distributions = (
                np.squeeze(distributions, axis=0) if distributions.ndim == 3 else distributions
            )
            probabilities = np.exp(distributions.reshape(-1, self.reg_max + 1))
            probabilities /= probabilities.sum(axis=1, keepdims=True)
            distances = probabilities.dot(self._project).reshape(-1, 4) * stride
            x1 = np.clip(anchors[:, 0] - distances[:, 0], 0, self.image_size)
            y1 = np.clip(anchors[:, 1] - distances[:, 1], 0, self.image_size)
            x2 = np.clip(anchors[:, 0] + distances[:, 2], 0, self.image_size)
            y2 = np.clip(anchors[:, 1] + distances[:, 3], 0, self.image_size)
            boxes_by_level.append(np.column_stack((x1, y1, x2, y2)))
            scores_by_level.append(scores)

        boxes = np.concatenate(boxes_by_level)
        scores = np.concatenate(scores_by_level)
        class_ids = np.argmax(scores, axis=1)
        confidences = np.max(scores, axis=1)
        boxes_xywh = boxes.copy()
        boxes_xywh[:, 2:] -= boxes_xywh[:, :2]
        indices = cv2.dnn.NMSBoxes(
            boxes_xywh.tolist(),
            confidences.tolist(),
            self._probability_threshold,
            self._iou_threshold,
        )
        if len(indices) == 0:
            return ()

        selected_boxes = boxes[np.asarray(indices).reshape(-1)]
        selected_confidences = confidences[np.asarray(indices).reshape(-1)]
        selected_class_ids = class_ids[np.asarray(indices).reshape(-1)]
        detected: list[DetectedObject] = []
        for box, confidence, class_id in zip(
            selected_boxes, selected_confidences, selected_class_ids, strict=True
        ):
            left, top, right, bottom = self._unletterbox(
                box, original_width, original_height, letterbox_scale
            )
            if left >= right or top >= bottom:
                continue
            detected.append(
                DetectedObject(
                    COCO_CATEGORIES[class_id],
                    round(float(confidence), 6),
                    left,
                    top,
                    right,
                    bottom,
                )
            )
        return tuple(detected)

    def _unletterbox(
        self,
        box: npt.NDArray[np.float32],
        original_width: int,
        original_height: int,
        letterbox_scale: tuple[int, int, int, int],
    ) -> tuple[int, int, int, int]:
        top, left, new_height, new_width = letterbox_scale
        ret = box.copy()
        if original_height == original_width:
            ret *= original_height / new_height
        else:
            ret[0] = (ret[0] - left) * original_width / new_width
            ret[1] = (ret[1] - top) * original_height / new_height
            ret[2] = (ret[2] - left) * original_width / new_width
            ret[3] = (ret[3] - top) * original_height / new_height
        ret[[0, 2]] = np.clip(ret[[0, 2]], 0, original_width)
        ret[[1, 3]] = np.clip(ret[[1, 3]], 0, original_height)
        return tuple(int(coordinate) for coordinate in ret)
