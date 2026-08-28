import os

import uvicorn
from fastapi import FastAPI

from daihougou.presence import PresenceTracker
from daihougou.rules import WelcomeRule
from daihougou.runtime import Runtime
from daihougou.settings import Settings
from daihougou.speaker import DirectSpeaker
from daihougou.speaker_worker import SpeakerWorker
from daihougou.storage import Storage
from daihougou.vision.frame_source import FfmpegFrameSource
from daihougou.vision.person_detector import PersonDetector
from daihougou.web import create_app


def create_production_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or Settings.from_mapping(os.environ)
    storage = Storage(resolved.data_dir / "daihougou.db")
    speaker_worker = SpeakerWorker(
        DirectSpeaker(resolved.mi_user, resolved.mi_pass, resolved.mi_did),
        storage,
    )
    runtime = Runtime(
        FfmpegFrameSource(resolved.stream_url, resolved.detection_fps),
        PersonDetector(resolved.model, resolved.person_threshold),
        PresenceTracker(resolved.leave_seconds),
        WelcomeRule(storage, resolved.welcome_cooldown_seconds),
        speaker_worker,
        storage,
    )
    return create_app(storage, runtime)


def run() -> None:
    settings = Settings.from_mapping(os.environ)
    uvicorn.run(
        create_production_app(settings),
        host=settings.web_host,
        port=settings.web_port,
        workers=1,
        access_log=False,
    )
