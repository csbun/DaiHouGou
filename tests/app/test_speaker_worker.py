import asyncio
from dataclasses import dataclass
from threading import Event

from daihougou.rules import WELCOME_RULE_ID, SpeechAction
from daihougou.speaker import SpeakResult
from daihougou.speaker_worker import SpeakerWorker
from daihougou.storage import EventRecord


class MemoryStorage:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.events: list[EventRecord] = []

    def rule_enabled(self, rule_id: str) -> bool:
        assert rule_id == WELCOME_RULE_ID
        return self.enabled

    def record_event(self, event: EventRecord) -> None:
        self.events.append(event)


@dataclass
class FakeSpeaker:
    result: SpeakResult
    texts: list[str]

    def speak(self, text: str) -> SpeakResult:
        self.texts.append(text)
        return self.result


def test_worker_speaks_actions_serially_and_records_success() -> None:
    async def run() -> None:
        storage = MemoryStorage()
        speaker = FakeSpeaker(SpeakResult(True, 12, 0), [])
        worker = SpeakerWorker(speaker, storage)
        worker.start()

        assert worker.submit(SpeechAction(WELCOME_RULE_ID, "hello", 1.0)) is True
        await worker.join()
        await worker.stop()

        assert speaker.texts == ["hello"]
        assert [(event.kind, event.success) for event in storage.events] == [
            ("speaker_success", True)
        ]

    asyncio.run(run())


def test_worker_rechecks_rule_enabled_before_speaking() -> None:
    async def run() -> None:
        storage = MemoryStorage(enabled=False)
        speaker = FakeSpeaker(SpeakResult(True, 12, 0), [])
        worker = SpeakerWorker(speaker, storage)
        worker.start()

        assert worker.submit(SpeechAction(WELCOME_RULE_ID, "hello", 1.0)) is True
        await worker.join()
        await worker.stop()

        assert speaker.texts == []
        assert [event.kind for event in storage.events] == ["speaker_skipped_disabled"]

    asyncio.run(run())


def test_worker_stops_after_reauth_required() -> None:
    async def run() -> None:
        storage = MemoryStorage()
        speaker = FakeSpeaker(
            SpeakResult(False, 12, "reauth_required", "reauth_required"), []
        )
        worker = SpeakerWorker(speaker, storage)
        worker.start()

        assert worker.submit(SpeechAction(WELCOME_RULE_ID, "first", 1.0)) is True
        await worker.join()
        assert worker.auth_status == "reauth_required"
        assert worker.submit(SpeechAction(WELCOME_RULE_ID, "second", 2.0)) is False
        await worker.stop()

        assert speaker.texts == ["first"]
        assert [event.kind for event in storage.events] == ["speaker_reauth_required"]

    asyncio.run(run())


def test_worker_reports_full_queue_without_blocking() -> None:
    async def run() -> None:
        storage = MemoryStorage()
        release = Event()

        class BlockingSpeaker:
            def speak(self, text: str) -> SpeakResult:
                release.wait()
                return SpeakResult(True, 1, 0)

        worker = SpeakerWorker(BlockingSpeaker(), storage, maxsize=1)
        worker.start()
        assert worker.submit(SpeechAction(WELCOME_RULE_ID, "first", 1.0)) is True
        await asyncio.sleep(0)
        assert worker.submit(SpeechAction(WELCOME_RULE_ID, "second", 2.0)) is True
        assert worker.submit(SpeechAction(WELCOME_RULE_ID, "third", 3.0)) is False
        release.set()
        await worker.stop()

        assert [event.kind for event in storage.events if event.kind == "speaker_queue_full"] == [
            "speaker_queue_full"
        ]

    asyncio.run(run())
