import asyncio
import threading

import numpy as np

from daihougou.detection_scheduler import DetectionScheduler
from daihougou.vision.frame_source import FrameSample
from daihougou.vision.person_detector import PersonDetection


def sample(sequence: int) -> FrameSample:
    return FrameSample(
        sequence,
        float(sequence),
        np.zeros((256, 256, 3), dtype=np.uint8),
    )


def test_scheduler_loads_one_detector_and_releases_it_when_stopped() -> None:
    class Detector:
        def detect(self, frame: np.ndarray) -> PersonDetection:
            return PersonDetection(False, 0.1, 1)

    async def scenario() -> None:
        created: list[Detector] = []

        def factory() -> Detector:
            detector = Detector()
            created.append(detector)
            return detector

        scheduler = DetectionScheduler(factory)
        assert scheduler.loaded is False
        await scheduler.start()
        assert scheduler.loaded is True
        await asyncio.gather(
            scheduler.detect("front", sample(1)),
            scheduler.detect("back", sample(2)),
        )
        assert len(created) == 1
        await scheduler.stop()
        assert scheduler.loaded is False
        assert scheduler.status == "stopped"

    asyncio.run(scenario())


def test_fifo_queue_prevents_one_camera_from_starving_another() -> None:
    class OrderedDetector:
        def __init__(self) -> None:
            self.sequences: list[int] = []

        def detect(self, frame: np.ndarray) -> PersonDetection:
            self.sequences.append(int(frame[0, 0, 0]))
            return PersonDetection(False, 0.1, 1)

    async def scenario() -> None:
        detector = OrderedDetector()
        scheduler = DetectionScheduler(lambda: detector)
        await scheduler.start()
        frames = [sample(1), sample(2), sample(3)]
        for frame in frames:
            frame.frame[0, 0, 0] = frame.sequence
        front_first = asyncio.create_task(scheduler.detect("front", frames[0]))
        back = asyncio.create_task(scheduler.detect("back", frames[1]))
        await front_first
        front_second = asyncio.create_task(scheduler.detect("front", frames[2]))
        await asyncio.gather(back, front_second)
        await scheduler.stop()

        assert detector.sequences == [1, 2, 3]

    asyncio.run(scenario())


def test_detector_calls_are_serialized() -> None:
    class BlockingDetector:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()
            self.lock = threading.Lock()
            self.active = 0
            self.max_active = 0
            self.calls = 0

        def detect(self, frame: np.ndarray) -> PersonDetection:
            with self.lock:
                self.calls += 1
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                if self.calls == 1:
                    self.entered.set()
            if self.calls == 1:
                assert self.release.wait(2)
            with self.lock:
                self.active -= 1
            return PersonDetection(False, 0.1, 1)

    async def scenario() -> None:
        detector = BlockingDetector()
        scheduler = DetectionScheduler(lambda: detector)
        await scheduler.start()
        front = asyncio.create_task(scheduler.detect("front", sample(1)))
        back = asyncio.create_task(scheduler.detect("back", sample(2)))
        assert await asyncio.to_thread(detector.entered.wait, 2)
        assert detector.calls == 1
        detector.release.set()
        await asyncio.gather(front, back)
        await scheduler.stop()

        assert detector.calls == 2
        assert detector.max_active == 1

    asyncio.run(scenario())


def test_duplicate_in_flight_request_for_one_camera_is_rejected() -> None:
    class BlockingDetector:
        def __init__(self) -> None:
            self.entered = threading.Event()
            self.release = threading.Event()

        def detect(self, frame: np.ndarray) -> PersonDetection:
            self.entered.set()
            assert self.release.wait(2)
            return PersonDetection(False, 0.1, 1)

    async def scenario() -> None:
        detector = BlockingDetector()
        scheduler = DetectionScheduler(lambda: detector)
        await scheduler.start()
        first = asyncio.create_task(scheduler.detect("front", sample(1)))
        assert await asyncio.to_thread(detector.entered.wait, 2)
        try:
            await scheduler.detect("front", sample(2))
        except RuntimeError as error:
            assert str(error) == "detector_request_pending"
        else:
            raise AssertionError("duplicate request was accepted")
        detector.release.set()
        await first
        await scheduler.stop()

    asyncio.run(scenario())
