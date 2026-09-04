from __future__ import annotations

import inspect
import json
import math
import secrets
import time
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode, urlsplit

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from guduck.camera_snapshot import SnapshotUnavailable
from guduck.detection_region import DetectionRegion
from guduck.object_catalog import COCO_CATEGORY_NAMES, OBJECTS365_CATEGORY_NAMES
from guduck.rules import BUILTIN_RULE_IDS
from guduck.runtime import CameraView, ObjectDetectorOption, RuntimeSnapshot
from guduck.settings import ObjectDetectorAdapter
from guduck.storage import BindingSaveResult, TestResult
from guduck.xiaomi import PublicSpeaker, XiaomiStateError, XiaomiStatus

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

    def xiaomi_status(self, attempt_id: str | None = None) -> XiaomiStatus: ...

    async def start_xiaomi_auth(self, username: str, password: str) -> str: ...

    async def submit_xiaomi_otp(self, attempt_id: str, code: str) -> None: ...

    async def cancel_xiaomi_auth(self, attempt_id: str) -> None: ...

    async def refresh_xiaomi_devices(self) -> XiaomiStatus: ...

    async def save_xiaomi_bindings(
        self,
        selected_ids: Sequence[str],
        confirmation_id: str | None,
        display_names: Mapping[str, str],
    ) -> BindingSaveResult: ...

    async def test_xiaomi_binding(self, binding_id: str) -> TestResult: ...

    def snapshot(self) -> RuntimeSnapshot: ...


def _verify_write_request(request: Request, form_token: str, expected_token: str) -> None:
    origin = request.headers.get("origin", "")
    host = request.headers.get("host", "")
    cookie = request.cookies.get("guduck_csrf", "")
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


def _public_speaker_payload(speaker: PublicSpeaker) -> dict[str, object]:
    return {
        "id": speaker.id,
        "name": speaker.name,
        "mina_name": speaker.mina_name,
        "hardware": speaker.hardware,
        "checked": speaker.checked,
        "available": speaker.available,
        "test_status": speaker.test_status,
    }


def _xiaomi_status_payload(status: XiaomiStatus) -> dict[str, object]:
    return {
        "state": status.state,
        "attempt_id": status.attempt_id,
        "otp_method": status.otp_method,
        "expires_at": status.expires_at,
        "error_code": status.error_code,
        "devices": [_public_speaker_payload(device) for device in status.devices],
        "bindings": [_public_speaker_payload(binding) for binding in status.bindings],
    }


def _no_store_json(
    content: object,
    *,
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(
        content,
        status_code=status_code,
        headers={"Cache-Control": "no-store"},
    )


def _xiaomi_error(error: XiaomiStateError) -> JSONResponse:
    code = error.code
    if code in {
        "unknown_auth_attempt",
        "otp_not_required",
        "account_unconfigured",
        "auth_required",
    }:
        status_code = 409
    elif code in {"device_refresh_failed", "auth_failed"}:
        status_code = 503
    else:
        status_code = 400
    return _no_store_json({"detail": code}, status_code=status_code)


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

    app = FastAPI(title="GuDuck", lifespan=lifespan, docs_url=None, redoc_url=None)
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
        xiaomi_status = runtime.xiaomi_status()
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
                "xiaomi": _xiaomi_status_payload(xiaomi_status),
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
            "guduck_csrf",
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

    @app.post("/api/xiaomi/auth/start")
    async def start_xiaomi_auth(
        request: Request,
        csrf_token: str = Form(default=""),
        username: str = Form(default=""),
        password: str = Form(default=""),
    ) -> JSONResponse:
        _verify_write_request(request, csrf_token, token)
        if not 1 <= len(username.strip()) <= 200 or not 1 <= len(password) <= 200:
            return _no_store_json({"detail": "invalid_credentials"}, status_code=400)
        try:
            attempt_id = await runtime.start_xiaomi_auth(username, password)
            status = runtime.xiaomi_status(attempt_id)
        except XiaomiStateError as error:
            return _xiaomi_error(error)
        return _no_store_json(
            {**_xiaomi_status_payload(status), "attempt_id": attempt_id},
            status_code=202,
        )

    @app.get("/api/xiaomi/auth/status")
    async def xiaomi_auth_status(attempt_id: str = "") -> JSONResponse:
        if len(attempt_id) > 128:
            return _no_store_json({"detail": "invalid_attempt_id"}, status_code=400)
        try:
            status = runtime.xiaomi_status(attempt_id or None)
        except XiaomiStateError as error:
            return _xiaomi_error(error)
        return _no_store_json(_xiaomi_status_payload(status))

    @app.post("/api/xiaomi/auth/otp")
    async def submit_xiaomi_otp(
        request: Request,
        csrf_token: str = Form(default=""),
        attempt_id: str = Form(default=""),
        code: str = Form(default=""),
    ) -> JSONResponse:
        _verify_write_request(request, csrf_token, token)
        if not 1 <= len(attempt_id) <= 128 or not 4 <= len(code.strip()) <= 12:
            return _no_store_json({"detail": "invalid_otp"}, status_code=400)
        try:
            await runtime.submit_xiaomi_otp(attempt_id, code)
            status = runtime.xiaomi_status(attempt_id)
        except XiaomiStateError as error:
            return _xiaomi_error(error)
        return _no_store_json(_xiaomi_status_payload(status))

    @app.post("/api/xiaomi/auth/cancel")
    async def cancel_xiaomi_auth(
        request: Request,
        csrf_token: str = Form(default=""),
        attempt_id: str = Form(default=""),
    ) -> JSONResponse:
        _verify_write_request(request, csrf_token, token)
        if not 1 <= len(attempt_id) <= 128:
            return _no_store_json({"detail": "invalid_attempt_id"}, status_code=400)
        try:
            await runtime.cancel_xiaomi_auth(attempt_id)
            status = runtime.xiaomi_status(attempt_id)
        except XiaomiStateError as error:
            return _xiaomi_error(error)
        return _no_store_json(_xiaomi_status_payload(status))

    @app.post("/api/xiaomi/devices/refresh")
    async def refresh_xiaomi_devices(
        request: Request,
        csrf_token: str = Form(default=""),
    ) -> JSONResponse:
        _verify_write_request(request, csrf_token, token)
        try:
            status = await runtime.refresh_xiaomi_devices()
        except XiaomiStateError as error:
            return _xiaomi_error(error)
        return _no_store_json(_xiaomi_status_payload(status))

    @app.post("/api/xiaomi/bindings/save")
    async def save_xiaomi_bindings(request: Request) -> JSONResponse:
        form = await request.form()
        form_token = form.get("csrf_token")
        _verify_write_request(
            request,
            form_token if isinstance(form_token, str) else "",
            token,
        )
        selected_ids = [
            value
            for value in form.getlist("selected_ids")
            if isinstance(value, str)
        ]
        confirmation = form.get("confirmation_id")
        confirmation_id = confirmation if isinstance(confirmation, str) else ""
        names_value = form.get("display_names")
        names_json = names_value if isinstance(names_value, str) else "{}"
        if (
            len(selected_ids) > 100
            or any(not 1 <= len(binding_id) <= 128 for binding_id in selected_ids)
            or len(confirmation_id) > 128
            or len(names_json) > 20_000
        ):
            return _no_store_json({"detail": "invalid_binding_request"}, status_code=400)
        try:
            raw_names = json.loads(names_json)
        except json.JSONDecodeError:
            return _no_store_json({"detail": "invalid_binding_request"}, status_code=400)
        if not isinstance(raw_names, dict) or any(
            not isinstance(binding_id, str)
            or not isinstance(name, str)
            or not 1 <= len(binding_id) <= 128
            or not 1 <= len(name.strip()) <= 50
            for binding_id, name in raw_names.items()
        ):
            return _no_store_json({"detail": "invalid_binding_request"}, status_code=400)
        selected_set = frozenset(selected_ids)
        display_names = {
            binding_id: name.strip()
            for binding_id, name in raw_names.items()
            if binding_id in selected_set
        }
        try:
            result = await runtime.save_xiaomi_bindings(
                selected_ids,
                confirmation_id or None,
                display_names,
            )
        except XiaomiStateError as error:
            return _xiaomi_error(error)
        except (TypeError, ValueError):
            return _no_store_json({"detail": "invalid_binding_request"}, status_code=400)
        if not result.saved:
            return _no_store_json(
                {
                    "detail": "unbind_confirmation_required",
                    "confirmation_id": result.confirmation_id,
                    "affected_cameras": list(result.affected_camera_ids),
                },
                status_code=409,
            )
        return _no_store_json(
            {
                "saved": True,
                "status": _xiaomi_status_payload(runtime.xiaomi_status()),
            }
        )

    @app.post("/api/xiaomi/bindings/{binding_id}/test")
    async def test_xiaomi_binding(
        request: Request,
        binding_id: str,
        csrf_token: str = Form(default=""),
    ) -> JSONResponse:
        _verify_write_request(request, csrf_token, token)
        if not 1 <= len(binding_id) <= 128:
            return _no_store_json({"detail": "invalid_binding_id"}, status_code=400)
        try:
            result = await runtime.test_xiaomi_binding(binding_id)
        except XiaomiStateError as error:
            return _xiaomi_error(error)
        return _no_store_json(
            {
                "success": result.success,
                "message": "测试成功" if result.success else "测试失败",
            }
        )

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
            if str(error) == "speaker_unavailable":
                raise HTTPException(status_code=409, detail="speaker_unavailable") from error
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
