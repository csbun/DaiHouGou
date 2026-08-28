import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from daihougou.runtime import RuntimeSnapshot
from daihougou.storage import WELCOME_RULE_ID, EventRecord, Storage

PACKAGE_DIR = Path(__file__).parent


class ManagedRuntime(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def snapshot(self) -> RuntimeSnapshot: ...


def create_app(
    storage: Storage,
    runtime: ManagedRuntime,
    csrf_token: str | None = None,
) -> FastAPI:
    token = csrf_token or secrets.token_urlsafe(32)
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        storage.initialize()
        await runtime.start()
        try:
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="大口九", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> HTMLResponse:
        response = templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "snapshot": runtime.snapshot(),
                "rule_enabled": storage.rule_enabled(WELCOME_RULE_ID),
                "events": storage.recent_events(limit=8),
                "latest_rule_trigger": storage.latest_rule_trigger(WELCOME_RULE_ID),
                "csrf_token": token,
                "rule_id": WELCOME_RULE_ID,
            },
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

    @app.post("/rules/{rule_id}/{action}")
    async def update_rule(
        request: Request,
        rule_id: str,
        action: str,
        csrf_token: str = Form(default=""),
    ) -> RedirectResponse:
        origin = request.headers.get("origin", "")
        host = request.headers.get("host", "")
        cookie = request.cookies.get("daihougou_csrf", "")
        if not origin or urlsplit(origin).netloc != host:
            raise HTTPException(status_code=403, detail="cross_origin_request")
        if not cookie or not secrets.compare_digest(cookie, token):
            raise HTTPException(status_code=403, detail="invalid_csrf")
        if not csrf_token or not secrets.compare_digest(csrf_token, token):
            raise HTTPException(status_code=403, detail="invalid_csrf")
        if rule_id != WELCOME_RULE_ID or action not in {"enable", "disable"}:
            raise HTTPException(status_code=404, detail="unknown_rule")
        storage.set_rule_enabled(rule_id, action == "enable")
        storage.record_event(
            EventRecord(
                "rule_enabled_changed",
                True,
                rule_id=rule_id,
                details={"enabled": action == "enable"},
            )
        )
        return RedirectResponse("/", status_code=303)

    @app.get("/healthz")
    async def healthz() -> dict[str, str | int | float | None]:
        snapshot = runtime.snapshot()
        return {
            "status": snapshot.overall,
            "app": snapshot.app,
            "database": snapshot.database,
            "camera": snapshot.camera,
            "detector": snapshot.detector,
            "speaker_auth": snapshot.speaker_auth,
            "presence": snapshot.presence,
            "last_sequence": snapshot.last_sequence,
        }

    return app
