import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
import numpy.typing as npt


class Network(Protocol):
    def setInput(self, blob: npt.NDArray[np.float32]) -> None: ...

    def getUnconnectedOutLayersNames(self) -> tuple[str, ...]: ...

    def forward(
        self, output_names: tuple[str, ...]
    ) -> tuple[npt.NDArray[np.float32], ...]: ...


@dataclass(frozen=True)
class PersonDetection:
    person_present: bool
    confidence: float
    latency_ms: int


class PersonDetector:
    def __init__(
        self,
        model: Path,
        threshold: float = 0.55,
        net: Network | None = None,
    ) -> None:
        if not 0 < threshold < 1:
            raise ValueError("threshold must be between 0 and 1")
        self._threshold = threshold
        self._net = net if net is not None else cv2.dnn.readNetFromONNX(str(model))
        if net is None:
            self._net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            self._net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self._output_names = tuple(self._net.getUnconnectedOutLayersNames())

    def detect(self, frame: npt.NDArray[np.uint8]) -> PersonDetection:
        started = time.monotonic()
        height, width = frame.shape[:2]
        scale = min(224 / width, 224 / height)
        resized_width = max(1, int(width * scale))
        resized_height = max(1, int(height * scale))
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        normalized = rgb.astype(np.float32) / 255.0 * 2.0 - 1.0
        resized = cv2.resize(normalized, (resized_width, resized_height))
        horizontal_padding = 224 - resized_width
        vertical_padding = 224 - resized_height
        padded = cv2.copyMakeBorder(
            resized,
            vertical_padding // 2,
            vertical_padding - vertical_padding // 2,
            horizontal_padding // 2,
            horizontal_padding - horizontal_padding // 2,
            cv2.BORDER_CONSTANT,
            value=(0, 0, 0),
        )
        blob = np.ascontiguousarray(padded.transpose(2, 0, 1)[None])
        self._net.setInput(blob)
        _, score_logits = self._net.forward(self._output_names)
        logits = np.asarray(score_logits, dtype=np.float64)
        probabilities = 1.0 / (1.0 + np.exp(-np.clip(logits, -100.0, 100.0)))
        confidence = float(probabilities.max()) if probabilities.size else 0.0
        return PersonDetection(
            person_present=confidence >= self._threshold,
            confidence=confidence,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
