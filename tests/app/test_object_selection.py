from daihougou.object_selection import select_announced_objects
from daihougou.vision.object_detector import DetectedObject, ObjectDetection


def obj(label: str, confidence: float, box: tuple[int, int, int, int]) -> DetectedObject:
    return DetectedObject(label, confidence, *box)


def test_selection_filters_person_and_large_book_then_deduplicates_top_three() -> None:
    result = ObjectDetection(
        objects=(
            obj("person", 0.99, (0, 0, 20, 20)),
            obj("book", 0.95, (0, 0, 416, 300)),
            obj("dog", 0.90, (0, 0, 20, 20)),
            obj("sports ball", 0.85, (0, 0, 20, 20)),
            obj("cat", 0.80, (0, 0, 20, 20)),
            obj("cat", 0.70, (0, 0, 20, 20)),
            obj("bird", 0.60, (0, 0, 20, 20)),
        ),
        latency_ms=200,
    )

    selected = select_announced_objects(result, frame_width=416, frame_height=416)

    assert tuple(item.label for item in selected) == ("dog", "sports ball", "cat")


def test_selection_filters_book_at_exactly_half_the_frame_area() -> None:
    result = ObjectDetection(
        objects=(obj("book", 0.99, (0, 0, 100, 50)),),
        latency_ms=1,
    )

    selected = select_announced_objects(result, frame_width=100, frame_height=100)

    assert selected == ()
