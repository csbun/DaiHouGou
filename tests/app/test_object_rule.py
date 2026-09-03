from daihougou.object_catalog import (
    COCO_CATEGORY_NAMES,
    OBJECTS365_CATEGORY_NAMES,
    SUPPORTED_CATEGORY_NAMES,
)
from daihougou.object_rule import ObjectCategoryAnnouncementRule
from daihougou.rules import OBJECT_RULE_ID
from daihougou.vision.object_detector import (
    COCO_CATEGORIES,
    DetectedObject,
    ObjectDetection,
)
from daihougou.vision.objects365_detector import OBJECTS365_CATEGORIES


class RuleState:
    def __init__(self, speaker_id: str) -> None:
        self.speaker_id = speaker_id

    def camera_speaker_id(self, camera_id: str) -> str:
        assert camera_id == "front"
        return self.speaker_id


def detected(label: str, confidence: float, box: tuple[int, int, int, int]) -> DetectedObject:
    return DetectedObject(label, confidence, *box)


def test_object_rule_filters_deduplicates_limits_and_builds_replaceable_action() -> None:
    state = RuleState(speaker_id="living_room")
    rule = ObjectCategoryAnnouncementRule("front", state)
    result = ObjectDetection(
        objects=(
            detected("person", 0.99, (0, 0, 50, 50)),
            detected("book", 0.95, (0, 0, 416, 300)),
            detected("dog", 0.90, (0, 0, 40, 40)),
            detected("sports ball", 0.85, (0, 0, 40, 40)),
            detected("cat", 0.80, (0, 0, 40, 40)),
            detected("cat", 0.70, (0, 0, 30, 30)),
            detected("bird", 0.60, (0, 0, 30, 30)),
        ),
        latency_ms=220,
    )

    action = rule.handle(result, occurred_monotonic=10.0, frame_size=(416, 416))

    assert action is not None
    assert action.rule_id == OBJECT_RULE_ID
    assert action.text == "dog, sports ball, cat"
    assert action.coalesce_key == ("front", OBJECT_RULE_ID)
    assert action.max_queue_age_seconds == 3.0
    assert action.completion_event_kind == "object_announcement_completed"
    assert action.superseded_event_kind == "object_announcement_superseded"
    assert action.details == {
        "objects": [
            {"label": "dog", "confidence": 0.9},
            {"label": "sports ball", "confidence": 0.85},
            {"label": "cat", "confidence": 0.8},
        ]
    }


def test_supported_catalog_covers_both_detector_vocabularies() -> None:
    assert tuple(COCO_CATEGORY_NAMES) == COCO_CATEGORIES[1:]
    assert tuple(OBJECTS365_CATEGORY_NAMES) == OBJECTS365_CATEGORIES[1:]
    assert set(COCO_CATEGORIES[1:]).issubset(SUPPORTED_CATEGORY_NAMES)
    assert set(OBJECTS365_CATEGORIES[1:]).issubset(SUPPORTED_CATEGORY_NAMES)
    assert SUPPORTED_CATEGORY_NAMES["cat"] == "猫"
    assert SUPPORTED_CATEGORY_NAMES["dog"] == "狗"
    assert SUPPORTED_CATEGORY_NAMES["rabbit"] == "兔子"
