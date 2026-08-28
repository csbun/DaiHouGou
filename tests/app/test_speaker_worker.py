import asyncio

from daihougou.rules import SpeechAction
from daihougou.speaker import SpeakResult
from daihougou.speaker_worker import SpeakerWorker
from daihougou.storage import EventRecord


class FakeStorage:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.events: list[EventRecord] = []

    def rule_enabled(self, rule_id: str) -> bool:
        return self.enabled

    def record_event(self, event: EventRecord) -> None:
        self.events.append(event)


class FakeSpeaker:
    def __init__(self, result: SpeakResult) -> None:
        self.result = result
        self.spoken: list[str] = []

    def speak(self, text: str) -> SpeakResult:
        self.spoken.append(text)
        return self.result


def action(number: int = 1) -> SpeechAction:
    return SpeechAction("welcome_on_person_entry", f"欢迎 {number}", float(number))


def test_worker_rechecks_rule_before_speaking() -> None:
    async def scenario() -> None:
        storage = FakeStorage(enabled=False)
        speaker = FakeSpeaker(SpeakResult(True, 10, 0))
        worker = SpeakerWorker(speaker, storage)
        await worker.start()
        assert worker.submit(action()) is True
        await worker.join()
        await worker.stop()
        assert speaker.spoken == []
        assert storage.events[-1].kind == "speaker_skipped_disabled"

    asyncio.run(scenario())


def test_worker_records_classified_reauthentication() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        speaker = FakeSpeaker(SpeakResult(False, 20, "reauth_required", "reauth_required"))
        worker = SpeakerWorker(speaker, storage)
        await worker.start()
        worker.submit(action())
        await worker.join()
        await worker.stop()
        assert worker.auth_status == "reauth_required"
        assert storage.events[-1].details == {"code": "reauth_required"}

    asyncio.run(scenario())


def test_worker_records_success_and_does_not_expose_queue() -> None:
    async def scenario() -> None:
        storage = FakeStorage()
        speaker = FakeSpeaker(SpeakResult(True, 10, 0))
        worker = SpeakerWorker(speaker, storage)
        await worker.start()
        worker.submit(action())
        await worker.join()
        await worker.stop()

        assert speaker.spoken == ["欢迎 1"]
        assert storage.events[-1].kind == "speaker_completed"
        assert storage.events[-1].success is True
        assert not hasattr(worker, "queue")

    asyncio.run(scenario())


def test_worker_drops_newest_action_when_queue_is_full() -> None:
    storage = FakeStorage()
    speaker = FakeSpeaker(SpeakResult(True, 10, 0))
    worker = SpeakerWorker(speaker, storage, queue_size=1)

    assert worker.submit(action(1)) is True
    assert worker.submit(action(2)) is False
    assert storage.events[-1].kind == "speaker_queue_full"
