from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class DetectionRegion:
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self) -> None:
        try:
            values = tuple(round(float(value), 6) for value in (self.x, self.y, self.width, self.height))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError("invalid detection region") from exc
        object.__setattr__(self, "x", values[0])
        object.__setattr__(self, "y", values[1])
        object.__setattr__(self, "width", values[2])
        object.__setattr__(self, "height", values[3])
        if (
            not all(math.isfinite(value) for value in values)
            or values[0] < 0
            or values[1] < 0
            or values[2] < 0.02
            or values[3] < 0.02
            or values[0] + values[2] > 1
            or values[1] + values[3] > 1
        ):
            raise ValueError("invalid detection region")

    @property
    def is_full_frame(self) -> bool:
        return (self.x, self.y, self.width, self.height) == (0.0, 0.0, 1.0, 1.0)


FULL_FRAME_REGION = DetectionRegion(0.0, 0.0, 1.0, 1.0)
