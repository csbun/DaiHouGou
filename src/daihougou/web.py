from __future__ import annotations

import inspect
import math
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from daihougou.camera_snapshot import SnapshotUnavailable
from daihougou.detection_region import DetectionRegion
from daihougou.object_catalog import COCO_CATEGORY_NAMES, OBJECTS365_CATEGORY_NAMES
from daihougou.rules import BUILTIN_RULE_IDS
from daihougou.runtime import CameraView, ObjectDetectorOption, RuntimeSnapshot
from daihougou.settings import ObjectDetectorAdapter

PACKAGE_DIR = Path(__file__).parent


class ManagedRuntime(Protocol):
    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def refresh_cameras(self) -> None: ...

    async def set_rule_enabled(self, camera_id: str, rule_id: str, enabled: bool) -> None: ...

    async def set_camera_speaker(self, camera_id: str, speaker_id: str) -> None: ...

    async def capture_camera_snapshot(self, camera_id: str) -> bytes: ...

    async def set_camera_detection_region(
        self, camera_id: str, region: DetectionRegion
    ) -> None: ...

    def welcome_phrases(self) -> tuple[str, ...]: ...

    def set_welcome_phrases(self, lines: list[str]) -> tuple[str, ...]: ...

    def object_detector_options(self) -> tuple[ObjectDetectorOption, ...]: ...

    async def set_object_detector_adapter(self, adapter: ObjectDetectorAdapter) -> None: ...

    def snapshot(self) -> RuntimeSnapshot: ...


def _verify_write_request(request: Request, form_token: str, expected_token: str) -> None:
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


def create_app(
    runtime: ManagedRuntime,
    csrf_token: str | None = None,
) -> FastAPI:
    token = csrf_token or secrets.token_urlsafe(32)
    templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
    snapshot_ready_at: dict[str, float] = {}
    snapshot_capability_seconds = 60 * 60

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
    ) -> HTMLResponse:
        snapshot = runtime.snapshot()
        response = templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "snapshot": snapshot,
                "csrf_token": token,
                "active_tab": "status",
                "camera_region_urls": {
                    camera.stream_id: f"/camera-region?{urlencode({'camera_id': camera.stream_id})}"
                    for camera in snapshot.cameras
                },
            },
        )
        return prepare_html(response)

    def find_camera(camera_id: str) -> CameraView:
        camera = next(
            (item for item in runtime.snapshot().cameras if item.stream_id == camera_id),
            None,
        )
        if camera is None:
            raise HTTPException(status_code=404, detail="unknown_camera")
        return camera

    def render_camera_region(
        request: Request,
        camera: CameraView,
        *,
        region: DetectionRegion | dict[str, str] | None = None,
        region_error: str = "",
        saved: bool = False,
        status_code: int = 200,
    ) -> HTMLResponse:
        resolved_region: DetectionRegion | dict[str, str] = region or camera.detection_region
        fields = ("x", "y", "width", "height")
        if isinstance(resolved_region, DetectionRegion):
            region_values = {field: f"{getattr(resolved_region, field):.6f}" for field in fields}
            region_display = {
                field: f"{getattr(resolved_region, field) * 100:.2f}" for field in fields
            }
        else:
            region_values = resolved_region
            region_display = {}
            for field, value in resolved_region.items():
                try:
                    parsed = float(value)
                except (TypeError, ValueError, OverflowError):
                    region_display[field] = value
                else:
                    region_display[field] = (
                        f"{parsed * 100:.2f}" if math.isfinite(parsed) else value
                    )
        response = templates.TemplateResponse(
            request=request,
            name="camera_region.html",
            context={
                "camera": camera,
                "csrf_token": token,
                "region_values": region_values,
                "region_display": region_display,
                "region_error": region_error,
                "saved": saved,
            },
            status_code=status_code,
        )
        return prepare_html(response)

    def render_settings(
        request: Request,
        *,
        phrases_text: str | None = None,
        phrase_error: str = "",
        object_detector_error: str = "",
        status_code: int = 200,
    ) -> HTMLResponse:
        object_detector_options = runtime.object_detector_options()
        selected_adapter = next(
            option.adapter for option in object_detector_options if option.selected
        )
        supported_category_names = (
            OBJECTS365_CATEGORY_NAMES
            if selected_adapter is ObjectDetectorAdapter.OBJECTS365
            else COCO_CATEGORY_NAMES
        )
        object_detector_names = {
            ObjectDetectorAdapter.NANODET: "NanoDet COCO",
            ObjectDetectorAdapter.OBJECTS365: "Objects365 YOLO26n",
        }
        response = templates.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "snapshot": runtime.snapshot(),
                "phrases_text": (
                    phrases_text
                    if phrases_text is not None
                    else "\n".join(runtime.welcome_phrases())
                ),
                "phrase_error": phrase_error,
                "object_detector_error": object_detector_error,
                "csrf_token": token,
                "active_tab": "settings",
                "object_detector_options": tuple(
                    {
                        "value": option.adapter.value,
                        "name": object_detector_names[option.adapter],
                        "available": option.available,
                        "selected": option.selected,
                    }
                    for option in object_detector_options
                ),
                "supported_category_count": len(supported_category_names),
                "supported_categories": tuple(supported_category_names.items()),
            },
            status_code=status_code,
        )
        return prepare_html(response)

    def prepare_html(response: HTMLResponse) -> HTMLResponse:
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

    @app.get("/settings", response_class=HTMLResponse)
    async def settings(request: Request) -> HTMLResponse:
        return render_settings(request)

    @app.get("/logs", response_class=HTMLResponse)
    async def logs(request: Request) -> HTMLResponse:
        response = templates.TemplateResponse(
            request=request,
            name="logs.html",
            context={
                "snapshot": runtime.snapshot(),
                "csrf_token": token,
                "active_tab": "logs",
            },
        )
        return prepare_html(response)

    @app.get("/camera-region", response_class=HTMLResponse)
    async def camera_region(
        request: Request,
        camera_id: str = "",
        saved: str = "",
    ) -> HTMLResponse:
        return render_camera_region(
            request,
            find_camera(camera_id),
            saved=saved == "1",
        )

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
        rule_id: str = Form(default=""),
        enabled: str = Form(default=""),
    ) -> Response:
        _verify_write_request(request, csrf_token, token)
        if enabled not in {"true", "false"}:
            raise HTTPException(status_code=400, detail="invalid_rule_state")
        if not rule_id:
            parameter_count = len(inspect.signature(runtime.set_rule_enabled).parameters)
            if parameter_count <= 2 or "application/json" not in request.headers.get("accept", ""):
                rule_id = BUILTIN_RULE_IDS[0]
            else:
                raise HTTPException(status_code=400, detail="unknown_rule")
        if rule_id not in BUILTIN_RULE_IDS:
            raise HTTPException(status_code=400, detail="unknown_rule")
        try:
            try:
                await runtime.set_rule_enabled(camera_id, rule_id, enabled == "true")
            except TypeError:
                await runtime.set_rule_enabled(camera_id, enabled == "true")  # type: ignore[call-arg]
        except KeyError as error:
            raise HTTPException(status_code=404, detail="unknown_camera") from error
        except ValueError as error:
            if str(error) == "unknown_rule":
                raise HTTPException(status_code=400, detail="unknown_rule") from error
            raise
        if "application/json" in request.headers.get("accept", ""):
            snapshot = runtime.snapshot()
            camera = next(camera for camera in snapshot.cameras if camera.stream_id == camera_id)
            return JSONResponse(
                {
                    "camera": camera.health_dict(),
                    "enabled_camera_count": snapshot.enabled_camera_count,
                    "ready_camera_count": snapshot.ready_camera_count,
                    "resource_warning": snapshot.resource_warning,
                }
            )
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

    @app.post("/commands/camera-snapshot")
    async def camera_snapshot(
        request: Request,
        csrf_token: str = Form(default=""),
        camera_id: str = Form(default=""),
    ) -> Response:
        _verify_write_request(request, csrf_token, token)
        find_camera(camera_id)
        try:
            payload = await runtime.capture_camera_snapshot(camera_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="unknown_camera") from error
        except SnapshotUnavailable:
            snapshot_ready_at.pop(camera_id, None)
            return JSONResponse(
                {"detail": "camera_snapshot_unavailable"},
                status_code=503,
                headers={"Cache-Control": "no-store"},
            )
        snapshot_ready_at[camera_id] = time.monotonic()
        return Response(
            content=payload,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/commands/camera-region")
    async def update_camera_region(
        request: Request,
        csrf_token: str = Form(default=""),
        camera_id: str = Form(default=""),
        x: str = Form(default=""),
        y: str = Form(default=""),
        width: str = Form(default=""),
        height: str = Form(default=""),
    ) -> Response:
        _verify_write_request(request, csrf_token, token)
        camera = find_camera(camera_id)
        values = {"x": x, "y": y, "width": width, "height": height}
        wants_json = "application/json" in request.headers.get("accept", "")
        try:
            region = DetectionRegion(float(x), float(y), float(width), float(height))
        except (TypeError, ValueError, OverflowError):
            if wants_json:
                raise HTTPException(status_code=422, detail="invalid_detection_region") from None
            return render_camera_region(
                request,
                camera,
                region=values,
                region_error="检测区域格式无效",
                status_code=422,
            )

        captured_at = snapshot_ready_at.get(camera_id)
        has_current_snapshot = (
            captured_at is not None
            and time.monotonic() - captured_at <= snapshot_capability_seconds
        )
        if not region.is_full_frame and not has_current_snapshot:
            if wants_json:
                raise HTTPException(status_code=409, detail="camera_snapshot_required")
            return render_camera_region(
                request,
                camera,
                region=values,
                region_error="请先获取当前画面",
                status_code=409,
            )
        try:
            await runtime.set_camera_detection_region(camera_id, region)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="unknown_camera") from error

        camera = find_camera(camera_id)
        if wants_json:
            return JSONResponse(
                {
                    "saved": True,
                    "region": {
                        "x": region.x,
                        "y": region.y,
                        "width": region.width,
                        "height": region.height,
                    },
                    "camera": {
                        "stream_id": camera.stream_id,
                        "stream": camera.stream,
                        "last_error": camera.last_error,
                    },
                }
            )
        query = urlencode({"camera_id": camera_id, "saved": "1"})
        return RedirectResponse(f"/camera-region?{query}", status_code=303)

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
            return render_settings(
                request,
                phrases_text=phrases,
                phrase_error=_phrase_error(error),
                status_code=422,
            )
        return RedirectResponse("/settings", status_code=303)

    @app.post("/commands/object-detector-adapter", response_class=HTMLResponse)
    async def update_object_detector_adapter(
        request: Request,
        csrf_token: str = Form(default=""),
        adapter: str = Form(default=""),
    ) -> Response:
        _verify_write_request(request, csrf_token, token)
        try:
            selected = ObjectDetectorAdapter(adapter)
        except ValueError:
            return render_settings(
                request,
                object_detector_error="检测器选项无效。",
                status_code=422,
            )
        try:
            await runtime.set_object_detector_adapter(selected)
        except RuntimeError as error:
            message = (
                "模型文件不可用，请先完成部署。"
                if str(error) == "object_detector_unavailable"
                else "模型加载失败，已继续使用原检测器。"
            )
            return render_settings(
                request,
                object_detector_error=message,
                status_code=422,
            )
        return RedirectResponse("/settings", status_code=303)

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
            "detectors": [
                {"kind": detector.kind, "status": detector.status, "loaded": detector.loaded}
                for detector in snapshot.detectors
            ],
        }

    return app
