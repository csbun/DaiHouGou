from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from daihougou.runtime import RuntimeSnapshot

PACKAGE_DIR = Path(__file__).parent


class ManagedRuntime(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def refresh_cameras(self) -> None: ...

    async def set_rule_enabled(self, camera_id: str, enabled: bool) -> None: ...

    async def set_camera_speaker(self, camera_id: str, speaker_id: str) -> None: ...

    def welcome_phrases(self) -> tuple[str, ...]: ...

    def set_welcome_phrases(self, lines: list[str]) -> tuple[str, ...]: ...

    def snapshot(self) -> RuntimeSnapshot: ...


def _verify_write_request(
    request: Request, form_token: str, expected_token: str
) -> None:
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    cookie = request.cookies.get("daihougou_csrf", "")
    if not origin or urlsplit(origin).netloc != host:
        raise HTTPException(status_code=403, detail="cross_origin_request")
    if not cookie or not secrets.compare_digest(cookie, expected_token):
        raise HTTPException(status_code=403, detail="invalid_csrf")
    if not form_token or not secrets.compare_digest(form_token, expected_token):
        raise HTTPException(status_code=403, detail="invalid_csrf")


def _phrase_error(error: ValueError) -> str:
    message = str(error)
    if "at least one" in message:
        return "至少填写一条欢迎词"
    if "at most 20" in message:
        return "最多填写 20 条欢迎词"
    if "at most 100" in message:
        return "每条欢迎词最多 100 个字符"
    return "欢迎词格式无效"


def create_app(runtime: ManagedRuntime, csrf_token: str | None = None) -> FastAPI:
    token = csrf_token or secrets.token_urlsafe(32)
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="大口九", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    def render_home(
        request: Request,
        *,
        phrases_text: str | None = None,
        phrase_error: str = "",
        status_code: int = 200,
    ) -> HTMLResponse:
        response = templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "snapshot": runtime.snapshot(),
                "phrases_text": (
                    phrases_text
                    if phrases_text is not None
                    else "\n".join(runtime.welcome_phrases())
                ),
                "phrase_error": phrase_error,
                "csrf_token": token,
            },
            status_code=status_code,
        )
        response.set_cookie(
            "daihougou_csrf",
            token,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        return render_home(request)

    @app.post("/commands/refresh-cameras")
    async def refresh_cameras(
        request: Request,
        csrf_token: str = Form(default=""),
    ) -> RedirectResponse:
        _verify_write_request(request, csrf_token, token)
        await runtime.refresh_cameras()
        return RedirectResponse("/", status_code=303)

    @app.post("/commands/camera-rule")
    async def update_camera_rule(
        request: Request,
        csrf_token: str = Form(default=""),
        camera_id: str = Form(default=""),
        enabled: str = Form(default=""),
    ) -> RedirectResponse:
        _verify_write_request(request, csrf_token, token)
        if enabled not in {"true", "false"}:
            raise HTTPException(status_code=400, detail="invalid_rule_state")
        try:
            await runtime.set_rule_enabled(camera_id, enabled == "true")
        except KeyError as error:
            raise HTTPException(status_code=404, detail="unknown_camera") from error
        return RedirectResponse("/", status_code=303)

    @app.post("/commands/camera-speaker")
    async def update_camera_speaker(
        request: Request,
        csrf_token: str = Form(default=""),
        camera_id: str = Form(default=""),
        speaker_id: str = Form(default=""),
    ) -> RedirectResponse:
        _verify_write_request(request, csrf_token, token)
        try:
            await runtime.set_camera_speaker(camera_id, speaker_id)
        except (KeyError, ValueError) as error:
            raise HTTPException(status_code=404, detail="unknown_camera_or_speaker") from error
        return RedirectResponse("/", status_code=303)

    @app.post("/commands/welcome-phrases", response_class=HTMLResponse)
    async def update_welcome_phrases(
        request: Request,
        csrf_token: str = Form(default=""),
        phrases: str = Form(default=""),
    ) -> Response:
        _verify_write_request(request, csrf_token, token)
        try:
            runtime.set_welcome_phrases(phrases.splitlines())
        except ValueError as error:
            return render_home(
                request,
                phrases_text=phrases,
                phrase_error=_phrase_error(error),
                status_code=422,
            )
        return RedirectResponse("/", status_code=303)

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        snapshot = runtime.snapshot()
        return {
            "status": snapshot.overall,
            "app": snapshot.app,
            "database": snapshot.database,
            "discovery": snapshot.discovery,
            "detector": snapshot.detector,
            "speaker_auth": snapshot.speaker_auth,
            "saved_camera_count": snapshot.saved_camera_count,
            "discovered_camera_count": snapshot.discovered_camera_count,
            "enabled_camera_count": snapshot.enabled_camera_count,
            "ready_camera_count": snapshot.ready_camera_count,
            "cameras": [camera.health_dict() for camera in snapshot.cameras],
        }

    return app
