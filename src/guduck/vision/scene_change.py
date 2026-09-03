from __future__ import annotations

import cv2
import numpy as np
import numpy.typing as npt

SCENE_SAMPLE_SIZE = 64
SCENE_PIXEL_DELTA = 25
SCENE_CHANGED_RATIO = 0.20


class SceneChangeGate:
    def __init__(self) -> None:
        self._reference: npt.NDArray[np.uint8] | None = None

    def changed(self, frame: npt.NDArray[np.uint8]) -> bool:
        reduced = cv2.resize(frame, (SCENE_SAMPLE_SIZE, SCENE_SAMPLE_SIZE))
        gray = cv2.cvtColor(reduced, cv2.COLOR_BGR2GRAY)
        reference = self._reference
        self._reference = gray
        if reference is None:
            return True
        changed_pixels = np.count_nonzero(
            cv2.absdiff(reference, gray) >= SCENE_PIXEL_DELTA
        )
        return float(changed_pixels) / gray.size >= SCENE_CHANGED_RATIO

    def invalidate(self) -> None:
        self._reference = None
