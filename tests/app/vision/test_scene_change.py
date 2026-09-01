import numpy as np

from daihougou.vision.scene_change import SceneChangeGate


def test_first_frame_changes_but_identical_second_frame_does_not() -> None:
    gate = SceneChangeGate()
    frame = np.zeros((416, 416, 3), dtype=np.uint8)

    assert gate.changed(frame) is True
    assert gate.changed(frame.copy()) is False


def test_twenty_percent_large_pixel_change_crosses_candidate_threshold() -> None:
    gate = SceneChangeGate()
    before = np.zeros((64, 64, 3), dtype=np.uint8)
    after = before.copy()
    after[:13, :, :] = 25

    assert gate.changed(before) is True
    assert gate.changed(after) is True
