import asyncio
import threading

from daihougou.rules import WELCOME_RULE_ID, SpeechAction
from daihougou.speaker import SpeakResult
from daihougou.speaker_worker import SpeakerManager
from daihougou.storage import EventRecord


class FakeStorage:
    def __init__(self) -> None:
        self.enabled = {"front": True, "back": True}
        self.pairings = {"front": "living_room", "back": "bedroom"}
        self.events: list[EventRecord] = []

    def camera_rule_enabled(self, camera_id: str, rule_id: str) -> bool:
        return self.enabled[camera_id] and rule_id == WELCOME_RULE_ID

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
