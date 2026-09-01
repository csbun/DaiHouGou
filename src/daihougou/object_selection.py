from daihougou.object_catalog import SUPPORTED_CATEGORY_NAMES
from daihougou.vision.object_detector import DetectedObject, ObjectDetection


def select_announced_objects(
    result: ObjectDetection, *, frame_width: int, frame_height: int
) -> tuple[DetectedObject, ...]:
    selected_by_label: dict[str, DetectedObject] = {}
    for detected in result.objects:
        if detected.label == "person" or detected.label == "unknown":
            continue
        if detected.label not in SUPPORTED_CATEGORY_NAMES:
            continue
        if detected.label == "book" and detected.area_ratio(frame_width, frame_height) >= 0.50:
            continue
        existing = selected_by_label.get(detected.label)
        if existing is None or detected.confidence > existing.confidence:
            selected_by_label[detected.label] = detected
    return tuple(sorted(selected_by_label.values(), key=lambda item: (-item.confidence, item.label))[:3])
