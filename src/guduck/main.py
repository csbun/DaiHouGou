from __future__ import annotations

import os

import uvicorn
from fastapi import FastAPI

from guduck.camera_snapshot import CameraSnapshotter
from guduck.database import application_database_path
from guduck.detection_region import DetectionRegion
from guduck.detection_scheduler import DetectionScheduler
from guduck.go2rtc import Go2RtcClient
from guduck.runtime import Runtime
from guduck.settings import ObjectDetectorAdapter, Settings
from guduck.speaker_worker import SpeakerManager
from guduck.storage import Storage
from guduck.vision.frame_source import FfmpegFrameSource
from guduck.vision.object_detector import ObjectDetector
from guduck.vision.objects365_detector import Objects365ObjectDetector
from guduck.vision.person_detector import PersonDetector
from guduck.web import create_app


def create_production_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_mapping(os.environ)
    storage = Storage(application_database_path(resolved.data_dir))
    speaker_manager = SpeakerManager({}, storage)

    def object_detector_for(
        adapter: ObjectDetectorAdapter,
    ) -> ObjectDetector | Objects365ObjectDetector:
        if adapter is ObjectDetectorAdapter.OBJECTS365:
            return Objects365ObjectDetector(resolved.objects365_model)
        return ObjectDetector(resolved.object_model)

    scheduler = DetectionScheduler(
        lambda: PersonDetector(resolved.model, resolved.person_threshold),
        lambda: object_detector_for(storage.object_detector_adapter()),
    )

    def frame_source_factory(url: str, size: int, region: DetectionRegion) -> FfmpegFrameSource:
        try:
            return FfmpegFrameSource(url, resolved.detection_fps, size=size, region=region)
        except TypeError:
            return FfmpegFrameSource(url, resolved.detection_fps)

    runtime = Runtime(
        storage=storage,
        discovery=Go2RtcClient(resolved.go2rtc_api_url),
        rtsp_base_url=resolved.go2rtc_rtsp_base_url,
        speakers=resolved.speakers,
        speaker_manager=speaker_manager,
        scheduler=scheduler,
        frame_source_factory=frame_source_factory,
        snapshotter=CameraSnapshotter(),
        leave_seconds=resolved.leave_seconds,
        welcome_cooldown_seconds=resolved.welcome_cooldown_seconds,
        object_detector_factory=object_detector_for,
        object_detector_available=lambda adapter: (
            resolved.objects365_model
            if adapter is ObjectDetectorAdapter.OBJECTS365
            else resolved.object_model
        ).is_file(),
    )
    return create_app(runtime)


def run() -> None:
    settings = Settings.from_mapping(os.environ)
    uvicorn.run(
        create_production_app(settings),
        host=settings.web_host,
        port=settings.web_port,
        workers=1,
        access_log=False,
    )
