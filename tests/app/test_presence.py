import pytest

from daihougou.presence import PresenceEventKind, PresenceState, PresenceTracker


def feed(tracker: PresenceTracker, values: list[tuple[float, bool]]):
    return [tracker.observe(at, present) for at, present in values]


def test_startup_calibrates_present_without_emitting_entry() -> None:
    tracker = PresenceTracker(leave_seconds=10)

    events = feed(tracker, [(0, True), (1, False), (2, True)])

    assert events == [None, None, None]
    assert tracker.state is PresenceState.PRESENT


def test_absent_to_two_of_three_present_emits_one_entry() -> None:
    tracker = PresenceTracker(leave_seconds=10)
    feed(tracker, [(0, False), (1, False), (2, False)])

    first = tracker.observe(3, True)
    second = tracker.observe(4, True)
    third = tracker.observe(5, True)

    assert first is None
    assert second is not None
    assert second.kind is PresenceEventKind.PERSON_ENTERED
    assert third is None


def test_single_negative_does_not_make_present_person_leave() -> None:
    tracker = PresenceTracker(leave_seconds=10)
    feed(tracker, [(0, True), (1, True), (2, True)])

    assert tracker.observe(5, False) is None
    assert tracker.observe(6, True) is None
    assert tracker.state is PresenceState.PRESENT


def test_ten_seconds_without_person_changes_to_absent_without_event() -> None:
    tracker = PresenceTracker(leave_seconds=10)
    feed(tracker, [(0, True), (1, True), (2, True)])

    assert tracker.observe(11.9, False) is None
    assert tracker.state is PresenceState.PRESENT
    assert tracker.observe(12, False) is None
    assert tracker.state is PresenceState.ABSENT


def test_reentry_after_leaving_requires_two_new_positive_observations() -> None:
    tracker = PresenceTracker(leave_seconds=10)
    feed(tracker, [(0, True), (1, True), (2, True)])
    tracker.observe(12, False)

    assert tracker.observe(13, True) is None
    event = tracker.observe(14, True)

    assert event is not None
    assert event.kind is PresenceEventKind.PERSON_ENTERED


def test_observation_time_must_be_monotonic() -> None:
    tracker = PresenceTracker()
    tracker.observe(2, False)

    with pytest.raises(ValueError, match="monotonic"):
        tracker.observe(1, False)
