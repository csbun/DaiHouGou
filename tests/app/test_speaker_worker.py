import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from guduck.rules import OBJECT_RULE_ID, WELCOME_RULE_ID, SpeechAction
from guduck.speaker import MiNASpeaker, SpeakResult
from guduck.speaker_worker import SpeakerManager
from guduck.storage import EventRecord


class FakeStorage:
    def __init__(self) -> None:
        self.enabled = {"front": True, "back": True}
        self.pairings = {"front": "living_room", "back": "bedroom"}
        self.events: list[EventRecord] = []

    def camera_rule_enabled(self, camera_id: str, rule_id: str) -> bool:
        return self.enabled[camera_id] and rule_id in {WELCOME_RULE_ID, OBJECT_RULE_ID}

    def camera_speaker_id(self, camera_id: str) -> str:
        return self.pairings[camera_id]

    def record_event(self, event: EventRecord) -> None:
        self.events.append(event)


class FakeSpeaker:
    def __init__(self, result: SpeakResult | None = None) -> None:
        self.result = result or SpeakResult(True, 1, 0)
        self.spoken: list[str] = []

    def speak(self, text: str) -> SpeakResult:
        self.spoken.append(text)
        return self.result


def action(camera: str, speaker: str, text: str) -> SpeechAction:
    return SpeechAction(camera, WELCOME_RULE_ID, speaker, text, 1.0)


def object_action(
    camera: str, speaker: str, text: str, created: float | None = None
) -> SpeechAction:
    created = time.monotonic() if created is None else created
    return SpeechAction(
        camera,
        OBJECT_RULE_ID,
        speaker,
        text,
        created,
        details={"objects": [{"label": text, "confidence": 0.9}]},
        coalesce_key=(camera, OBJECT_RULE_ID),
        max_queue_age_seconds=3.0,
        completion_event_kind="object_announcement_completed",
        superseded_event_kind="object_announcement_superseded",
    )


def test_manager_routes_each_camera_to_its_fixed_speaker() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        living = FakeSpeaker()
        bedroom = FakeSpeaker()
        manager = SpeakerManager(
            {"living_room": living, "bedroom": bedroom}, storage
        )
        await manager.start()
        manager.submit(action("front", "living_room", "前门"))
        manager.submit(action("back", "bedroom", "后门"))
        await manager.join()
        await manager.stop()

        assert living.spoken == ["前门"]
        assert bedroom.spoken == ["后门"]
        assert manager.speaker_statuses == {
            "living_room": "ready",
            "bedroom": "ready",
        }

    asyncio.run(scenario())


def test_manager_records_spoken_text_for_completed_audio() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        manager = SpeakerManager({"living_room": FakeSpeaker()}, storage)
        await manager.start()
        manager.submit(action("front", "living_room", "欢迎回家"))
        await manager.join()
        await manager.stop()

        event = storage.events[-1]
        assert event.kind == "speaker_completed"
        assert event.details["text"] == "欢迎回家"

    asyncio.run(scenario())


def test_manager_drops_action_after_camera_pairing_changes() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        living = FakeSpeaker()
        manager = SpeakerManager({"living_room": living}, storage)
        storage.pairings["front"] = "bedroom"
        await manager.start()
        manager.submit(action("front", "living_room", "过期"))
        await manager.join()
        await manager.stop()

        assert living.spoken == []
        event = storage.events[-1]
        assert (event.kind, event.camera_id, event.speaker_id) == (
            "speaker_skipped_pairing_changed",
            "front",
            "living_room",
        )

    asyncio.run(scenario())


def test_manager_drops_action_after_camera_rule_is_disabled() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        living = FakeSpeaker()
        manager = SpeakerManager({"living_room": living}, storage)
        storage.enabled["front"] = False
        await manager.start()
        manager.submit(action("front", "living_room", "过期"))
        await manager.join()
        await manager.stop()

        assert living.spoken == []
        assert storage.events[-1].kind == "speaker_skipped_disabled"

    asyncio.run(scenario())


def test_reauthentication_on_one_speaker_gates_all_queues() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        manager = SpeakerManager(
            {
                "living_room": FakeSpeaker(
                    SpeakResult(False, 1, "reauth_required", "reauth_required")
                ),
                "bedroom": FakeSpeaker(),
            },
            storage,
        )
        await manager.start()
        manager.submit(action("front", "living_room", "触发认证"))
        await manager.join()

        assert manager.auth_status == "reauth_required"
        assert manager.submit(action("back", "bedroom", "不得播放")) is False
        assert storage.events[-1].kind == "speaker_reauth_required"
        await manager.stop()

    asyncio.run(scenario())


def test_manager_can_start_empty_and_install_speakers_later() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        manager = SpeakerManager({}, storage)
        await manager.start()

        assert manager.available("living_room") is False
        assert manager.submit(action("front", "living_room", "尚未绑定")) is False
        assert storage.events[-1].kind == "speaker_unknown"

        living = FakeSpeaker()
        await manager.replace_speakers({"living_room": living}, {"living_room"})
        assert manager.available("living_room") is True
        assert manager.submit(action("front", "living_room", "已经绑定")) is True
        await manager.join()
        await manager.stop()

        assert living.spoken == ["已经绑定"]

    asyncio.run(scenario())


def test_dynamic_replacement_stops_removed_worker_and_starts_added_worker() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        living = FakeSpeaker()
        bedroom = FakeSpeaker()
        manager = SpeakerManager({"living_room": living}, storage)
        await manager.start()

        await manager.replace_speakers({"bedroom": bedroom}, {"bedroom"})

        assert manager.available("living_room") is False
        assert manager.available("bedroom") is True
        assert manager.submit(action("front", "living_room", "旧音箱")) is False
        assert storage.events[-1].kind == "speaker_unknown"
        assert manager.submit(action("back", "bedroom", "新音箱")) is True
        await manager.join()
        await manager.stop()

        assert living.spoken == []
        assert bedroom.spoken == ["新音箱"]

    asyncio.run(scenario())


def test_known_but_unavailable_speaker_rejects_new_work() -> None:
    storage = FakeStorage()
    manager = SpeakerManager({"living_room": FakeSpeaker()}, storage)

    manager.set_available_ids(set())

    assert manager.available("living_room") is False
    assert manager.submit(action("front", "living_room", "离线")) is False
    assert storage.events[-1].kind == "speaker_unavailable"


def test_auth_failure_drops_pending_work_without_retrying_in_flight_call() -> None:
    class AuthFailingSpeaker:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()
            self.spoken: list[str] = []

        def speak(self, text: str) -> SpeakResult:
            self.spoken.append(text)
            self.entered.set()
            assert self.release.wait(2)
            return SpeakResult(False, 1, "reauth_required", "reauth_required")

    async def scenario() -> None:
        storage = FakeStorage()
        speaker = AuthFailingSpeaker()
        auth_notifications = 0

        def on_auth_required() -> None:
            nonlocal auth_notifications
            auth_notifications += 1

        manager = SpeakerManager(
            {"living_room": speaker},
            storage,
            on_auth_required=on_auth_required,
        )
        await manager.start()
        assert manager.submit(action("front", "living_room", "认证失效"))
        assert await asyncio.to_thread(speaker.entered.wait, 2)
        assert manager.submit(action("front", "living_room", "不得重试"))
        speaker.release.set()
        await manager.join()

        assert speaker.spoken == ["认证失效"]
        assert auth_notifications == 1
        assert manager.auth_status == "reauth_required"
        assert [event.kind for event in storage.events] == [
            "speaker_reauth_required",
            "speaker_reauth_required",
        ]
        await manager.stop()

    asyncio.run(scenario())


def test_cross_speaker_executor_backlog_is_gated_after_auth_failure() -> None:
    class MiNA:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def text_to_speech(self, device_id: str, text: str) -> bool:
            self.calls.append((device_id, text))
            if device_id == "living-device":
                raise RuntimeError("Error 401 private token")
            return True

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
        storage = FakeStorage()
        service = MiNA()
        auth_required = threading.Event()
        speakers = {
            "living_room": MiNASpeaker(
                "living-device",
                lambda: service,
                loop,
                is_authorized=lambda: not auth_required.is_set(),
                on_auth_required=auth_required.set,
            ),
            "bedroom": MiNASpeaker(
                "bedroom-device",
                lambda: service,
                loop,
                is_authorized=lambda: not auth_required.is_set(),
                on_auth_required=auth_required.set,
            ),
        }
        manager = SpeakerManager(speakers, storage)
        await manager.start()

        assert manager.submit(action("front", "living_room", "触发失效"))
        assert manager.submit(action("back", "bedroom", "不得开始"))
        await manager.join()
        await manager.stop()

        assert service.calls == [("living-device", "触发失效")]
        assert manager.auth_status == "reauth_required"
        assert [event.kind for event in storage.events] == [
            "speaker_reauth_required",
            "speaker_reauth_required",
        ]

    asyncio.run(scenario())


def test_replacement_drains_queued_action_and_waits_for_in_flight_call() -> None:
    class BlockingSpeaker(FakeSpeaker):
        def __init__(self) -> None:
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def speak(self, text: str) -> SpeakResult:
            self.entered.set()
            assert self.release.wait(2)
            return super().speak(text)

    async def scenario() -> None:
        storage = FakeStorage()
        old = BlockingSpeaker()
        new = FakeSpeaker()
        manager = SpeakerManager({"living_room": old}, storage)
        await manager.start()
        assert manager.submit(action("front", "living_room", "已经开始"))
        assert await asyncio.to_thread(old.entered.wait, 2)
        assert manager.submit(action("front", "living_room", "仍在排队"))

        replacement = asyncio.create_task(
            manager.replace_speakers({"living_room": new}, {"living_room"})
        )
        await asyncio.sleep(0)
        assert replacement.done() is False
        old.release.set()
        await replacement

        assert old.spoken == ["已经开始"]
        assert [event.kind for event in storage.events] == [
            "speaker_reconfigured",
            "speaker_completed",
        ]
        assert manager.submit(action("front", "living_room", "新 worker"))
        await manager.join()
        await manager.stop()
        assert new.spoken == ["新 worker"]

    asyncio.run(scenario())


def test_two_actions_for_one_speaker_never_overlap() -> None:
    class SerialSpeaker:
        def __init__(self) -> None:
            self.first_entered = threading.Event()
            self.release_first = threading.Event()
            self.active = 0
            self.max_active = 0
            self.calls = 0
            self.lock = threading.Lock()

        def speak(self, text: str) -> SpeakResult:
            with self.lock:
                self.calls += 1
                call_number = self.calls
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            if call_number == 1:
                self.first_entered.set()
                assert self.release_first.wait(2)
            with self.lock:
                self.active -= 1
            return SpeakResult(True, 1, 0)

    async def scenario() -> None:
        storage = FakeStorage()
        speaker = SerialSpeaker()
        manager = SpeakerManager({"living_room": speaker}, storage)
        await manager.start()
        manager.submit(action("front", "living_room", "一"))
        manager.submit(action("front", "living_room", "二"))
        assert await asyncio.to_thread(speaker.first_entered.wait, 2)
        assert speaker.calls == 1
        speaker.release_first.set()
        await manager.join()
        await manager.stop()

        assert speaker.calls == 2
        assert speaker.max_active == 1

    asyncio.run(scenario())


def test_different_speakers_can_speak_concurrently() -> None:
    release = threading.Event()

    class ParallelSpeaker:
        def __init__(self) -> None:
            self.entered = threading.Event()

        def speak(self, text: str) -> SpeakResult:
            self.entered.set()
            assert release.wait(2)
            return SpeakResult(True, 1, 0)

    async def scenario() -> None:
        storage = FakeStorage()
        living = ParallelSpeaker()
        bedroom = ParallelSpeaker()
        manager = SpeakerManager(
            {"living_room": living, "bedroom": bedroom}, storage
        )
        await manager.start()
        manager.submit(action("front", "living_room", "前门"))
        manager.submit(action("back", "bedroom", "后门"))
        assert await asyncio.to_thread(living.entered.wait, 2)
        assert await asyncio.to_thread(bedroom.entered.wait, 2)
        release.set()
        await manager.join()
        await manager.stop()

    asyncio.run(scenario())


def test_manager_rejects_unknown_or_full_speaker_queue() -> None:
    storage = FakeStorage()
    manager = SpeakerManager({"living_room": FakeSpeaker()}, storage, queue_size=1)

    assert manager.submit(action("back", "missing", "未知")) is False
    assert storage.events[-1].kind == "speaker_unknown"
    assert manager.submit(action("front", "living_room", "一")) is True
    assert manager.submit(action("front", "living_room", "二")) is False
    assert storage.events[-1].kind == "speaker_queue_full"


def test_new_object_action_replaces_pending_action_but_not_active_call() -> None:
    class BlockingSpeaker(FakeSpeaker):
        def __init__(self) -> None:
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def speak(self, text: str) -> SpeakResult:
            self.entered.set()
            assert self.release.wait(2)
            return super().speak(text)

    async def scenario() -> None:
        storage = FakeStorage()
        speaker = BlockingSpeaker()
        manager = SpeakerManager({"living_room": speaker}, storage)
        await manager.start()
        assert manager.submit(object_action("front", "living_room", "cat"))
        assert await asyncio.to_thread(speaker.entered.wait, 2)
        assert manager.submit(object_action("front", "living_room", "dog"))
        speaker.release.set()
        await manager.join()
        await manager.stop()

        assert speaker.spoken == ["cat", "dog"]
        superseded = [event for event in storage.events if event.kind == "object_announcement_superseded"]
        assert superseded == []

    asyncio.run(scenario())


def test_pending_object_action_is_replaced_and_old_action_is_recorded() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        speaker = FakeSpeaker()
        manager = SpeakerManager({"living_room": speaker}, storage)
        assert manager.submit(object_action("front", "living_room", "cat"))
        assert manager.submit(object_action("front", "living_room", "dog"))
        await manager.start()
        await manager.join()
        await manager.stop()

        assert speaker.spoken == ["dog"]
        event = storage.events[0]
        assert event.kind == "object_announcement_superseded"
        assert event.details == {"objects": [{"confidence": 0.9, "label": "cat"}]}

    asyncio.run(scenario())


def test_object_action_expires_at_dequeue_time_but_welcome_does_not() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        speaker = FakeSpeaker()
        now = [1.0]
        manager = SpeakerManager({"living_room": speaker}, storage, clock=lambda: now[0])
        assert manager.submit(object_action("front", "living_room", "cat", created=1.0))
        now[0] = 4.01
        assert manager.submit(action("front", "living_room", "welcome"))
        await manager.start()
        await manager.join()
        await manager.stop()

        assert speaker.spoken == ["welcome"]
        assert [event.kind for event in storage.events] == [
            "speaker_skipped_expired",
            "speaker_completed",
        ]

    asyncio.run(scenario())
