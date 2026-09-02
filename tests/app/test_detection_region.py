import math

import pytest

from daihougou.detection_region import FULL_FRAME_REGION, DetectionRegion


def test_detection_region_rounds_to_six_decimals() -> None:
    region = DetectionRegion(0.12345649, 0.2, 0.30000049, 0.4)
    assert region == DetectionRegion(0.123456, 0.2, 0.3, 0.4)


def test_full_frame_region_is_explicit() -> None:
    assert FULL_FRAME_REGION == DetectionRegion(0, 0, 1, 1)
    assert FULL_FRAME_REGION.is_full_frame is True


@pytest.mark.parametrize(
    "region",
    [
        (math.nan, 0, 1, 1),
        (0, math.inf, 1, 1),
        (-0.01, 0, 1, 1),
        (0, 0, 0.019999, 1),
        (0, 0, 1, 0.019999),
        (0.8, 0, 0.3, 1),
        (0, 0.8, 1, 0.3),
    ],
)
def test_detection_region_rejects_invalid_geometry(
    region: tuple[float, float, float, float],
) -> None:
    with pytest.raises(ValueError, match="invalid detection region"):
        DetectionRegion(*region)
