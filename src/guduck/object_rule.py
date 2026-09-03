from __future__ import annotations

from typing import Protocol

from guduck.object_selection import select_announced_objects
from guduck.rules import OBJECT_RULE_ID, SpeechAction
from guduck.vision.object_detector import ObjectDetection


class ObjectRuleState(Protocol):
    def camera_speaker_id(self, camera_id: str) -> str: ...


class ObjectCategoryAnnouncementRule:
    def __init__(self, camera_id: str, state: ObjectRuleState) -> None:
        self._camera_id = camera_id
        self._state = state

    def handle(
        self,
        result: ObjectDetection,
        *,
        occurred_monotonic: float,
        frame_size: tuple[int, int],
    ) -> SpeechAction | None:
        frame_width, frame_height = frame_size
        selected = select_announced_objects(
            result, frame_width=frame_width, frame_height=frame_height
        )
        if not selected:
            return None
        objects = [
            {"label": item.label, "confidence": round(item.confidence, 4)}
            for item in selected
        ]
        return SpeechAction(
            camera_id=self._camera_id,
            rule_id=OBJECT_RULE_ID,
            speaker_id=self._state.camera_speaker_id(self._camera_id),
            text=", ".join(item["label"] for item in objects),
            created_monotonic=occurred_monotonic,
            details={"objects": objects},
            coalesce_key=(self._camera_id, OBJECT_RULE_ID),
            max_queue_age_seconds=3.0,
            completion_event_kind="object_announcement_completed",
            superseded_event_kind="object_announcement_superseded",
        )
