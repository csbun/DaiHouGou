# Camera Detection Region Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add one persistent normalized rectangular detection region per camera, an independent snapshot-based editor page, and pre-inference ROI cropping for every visual rule.

**Architecture:** A focused `DetectionRegion` value object owns normalization and validation, while SQLite stores the four normalized values on each camera. FFmpeg applies the region before scaling and padding; `Runtime` includes it in each camera descriptor and restarts only the changed camera. A separate one-shot snapshot service feeds a FastAPI/Jinja2 page whose dependency-free JavaScript editor manages pointer geometry and percentage inputs.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLite, FFmpeg, NumPy/OpenCV, vanilla JavaScript, CSS, pytest, Node.js built-in test runner

**Spec:** `docs/superpowers/specs/2026-09-02-camera-detection-region-design.md`

## Global Constraints

- Each camera has exactly one region shared by all visual rules.
- Coordinates use the decoded source frame, top-left origin, and six-decimal normalized `x`, `y`, `width`, and `height` values.
- Width and height must each be at least `0.02`; every coordinate must be finite and the rectangle must remain inside `[0, 1]`.
- Full frame is the explicit value `(0, 0, 1, 1)` and preserves the existing FFmpeg filter output.
- ROI cropping happens before FPS selection, scaling, padding, scene-change gating, and model inference.
- A region save restarts only an enabled target camera; disabled cameras remain stopped.
- The restart caused by a region save must not announce its first object frame; other startup and recovery behavior remains unchanged.
- Snapshots are uncropped JPEGs, longest edge at most 1280 pixels, quality approximately 85, memory-only, and `Cache-Control: no-store`.
- Snapshot capture and all writes require the existing same-origin and CSRF checks.
- No new runtime dependency or frontend build chain is introduced.
- SQLite v2 databases migrate in place to v3; any other incompatible schema remains rejected.
- Region edits do not create events, retain images, or expose RTSP URLs and FFmpeg diagnostics.

## File Structure

- Create `src/daihougou/detection_region.py`: own normalized region rounding, validation, and the full-frame constant without storage or UI concerns.
- Modify `src/daihougou/storage.py`: migrate schema v2 to v3 and persist one `DetectionRegion` on each camera.
- Modify `src/daihougou/vision/frame_source.py`: translate a validated region into a crop-first FFmpeg filter chain.
- Modify `src/daihougou/camera_runtime.py`: suppress only the first object inference after a region-save restart.
- Modify `src/daihougou/runtime.py`: expose regions in camera views, reconcile target-only restarts, isolate start errors, and serialize one-shot snapshots outside the configuration lock.
- Create `src/daihougou/camera_snapshot.py`: run one bounded FFmpeg process and return an uncropped in-memory JPEG through a stable failure boundary.
- Modify `src/daihougou/main.py`: inject the production region-aware frame source and snapshotter.
- Modify `src/daihougou/web.py`: render region configuration and provide protected snapshot/save commands.
- Create `src/daihougou/templates/camera_region.html`: provide the independent editor page and semantic form controls.
- Modify `src/daihougou/templates/index.html`: add one compact editor entry and non-full-region status per camera.
- Create `src/daihougou/static/region-editor.js`: own dependency-free region geometry, pointer interaction, snapshot loading, validation, and saving.
- Modify `src/daihougou/static/app.css`: add constrained desktop/mobile editor layout without changing the visual system.
- Create `tests/app/test_detection_region.py` and `tests/app/test_camera_snapshot.py`: cover the two new focused Python modules.
- Create `tests/js/region-editor.test.js`: test editor geometry with Node's built-in runner and no package manifest.
- Modify existing storage, frame-source, camera-runtime, runtime, production-assembly, Web, and generated-video tests at their current ownership boundaries.
- Modify `README.md`: document the operator workflow and fixed-camera limitation.

---

### Task 1: Detection Region Domain Model and SQLite v3 Migration

**Files:**
- Create: `src/daihougou/detection_region.py`
- Create: `tests/app/test_detection_region.py`
- Modify: `src/daihougou/storage.py`
- Modify: `tests/app/test_storage.py`

**Interfaces:**
- Produces: `DetectionRegion(x: float, y: float, width: float, height: float)`
- Produces: `FULL_FRAME_REGION: DetectionRegion`
- Produces: `DetectionRegion.is_full_frame -> bool`
- Produces: `CameraConfig.detection_region: DetectionRegion`
- Produces: `Storage.set_camera_detection_region(camera_id: str, region: DetectionRegion) -> None`
- Consumes: existing `Storage.initialize()`, `Storage.sync_cameras()`, and strict table-set compatibility checks

- [ ] **Step 1: Write failing value-object tests**

Create `tests/app/test_detection_region.py` with concrete valid, normalization, and invalid cases:

```python
import math

import pytest

from daihougou.detection_region import DetectionRegion, FULL_FRAME_REGION


def test_detection_region_rounds_to_six_decimals() -> None:
    region = DetectionRegion(0.12345649, 0.2, 0.30000049, 0.4)
    assert region == DetectionRegion(0.123456, 0.2, 0.3, 0.4)


def test_full_frame_region_is_explicit() -> None:
    assert FULL_FRAME_REGION == DetectionRegion(0, 0, 1, 1)
    assert FULL_FRAME_REGION.is_full_frame is True


@pytest.mark.parametrize(
    "region",
    [
        (math.nan, 0, 1, 1),
        (0, math.inf, 1, 1),
        (-0.01, 0, 1, 1),
        (0, 0, 0.019999, 1),
        (0, 0, 1, 0.019999),
        (0.8, 0, 0.3, 1),
        (0, 0.8, 1, 0.3),
    ],
)
def test_detection_region_rejects_invalid_geometry(
    region: tuple[float, float, float, float],
) -> None:
    with pytest.raises(ValueError, match="invalid detection region"):
        DetectionRegion(*region)
```

- [ ] **Step 2: Run the value-object tests and verify RED**

Run: `pytest tests/app/test_detection_region.py -v`

Expected: collection fails because `daihougou.detection_region` does not exist.

- [ ] **Step 3: Implement the minimal region value object**

In `src/daihougou/detection_region.py`, use `math.isfinite`, frozen dataclass fields, and `object.__setattr__` to round every value to six decimals before validating. Use the single stable error message `invalid detection region`. Define `is_full_frame` by equality with `(0.0, 0.0, 1.0, 1.0)` without referencing the module constant during constant construction.

- [ ] **Step 4: Run the value-object tests and verify GREEN**

Run: `pytest tests/app/test_detection_region.py -v`

Expected: all tests pass.

- [ ] **Step 5: Write failing storage and migration tests**

Update `tests/app/test_storage.py` to assert `SCHEMA_VERSION == 3`, new cameras receive `FULL_FRAME_REGION`, a non-full region round-trips through `set_camera_detection_region`, and a later save silently overwrites the earlier value. Add a `create_v2_database(path)` test helper that creates the exact four v2 tables, sets `PRAGMA user_version = 2`, and inserts one camera, one enabled welcome rule, customized welcome phrases, and one event. Add this migration assertion:

```python
def test_initialize_migrates_v2_database_without_losing_data(tmp_path: Path) -> None:
    path = tmp_path / "app.db"
    create_v2_database(path)

    storage = Storage(path)
    storage.initialize()

    camera = storage.list_cameras()[0]
    assert SCHEMA_VERSION == 3
    assert camera.detection_region == FULL_FRAME_REGION
    assert camera.speaker_id == "bedroom"
    assert storage.camera_rule_enabled("front", WELCOME_RULE_ID) is True
    assert storage.welcome_phrases() == ("Custom welcome",)
    assert storage.recent_events()[0].kind == "existing_event"
```

Also assert an unknown camera update raises `KeyError`, and a malformed table set with `user_version = 2` is rejected without adding region columns.

- [ ] **Step 6: Run the storage tests and verify RED**

Run: `pytest tests/app/test_storage.py tests/app/test_detection_region.py -v`

Expected: failures show schema version 2, missing region columns, and missing storage methods.

- [ ] **Step 7: Implement the v3 schema and migration**

Update `Storage.initialize()` so it has three explicit paths: create v3 for an empty v0 database, migrate an exact v2 table set with four `ALTER TABLE cameras ADD COLUMN ... NOT NULL DEFAULT ...` statements in the connection transaction, or validate an existing exact v3 schema. Only set `PRAGMA user_version = 3` after all four columns are added. Keep the existing welcome-phrase upgrade and built-in-rule backfill after schema resolution.

Update `CameraConfig`, `_list_cameras()`, and the camera `SELECT` to construct `DetectionRegion` from the four columns. Implement this storage method with an exact one-row update check:

```python
def set_camera_detection_region(
    self, camera_id: str, region: DetectionRegion
) -> None:
    with self._connect() as connection:
        cursor = connection.execute(
            """
            UPDATE cameras
            SET region_x = ?, region_y = ?, region_width = ?, region_height = ?
            WHERE stream_id = ?
            """,
            (region.x, region.y, region.width, region.height, camera_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(camera_id)
```

- [ ] **Step 8: Run focused storage tests and the complete suite**

Run: `pytest tests/app/test_detection_region.py tests/app/test_storage.py -v`

Expected: all focused tests pass.

Run: `pytest -q`

Expected: complete suite passes.

- [ ] **Step 9: Commit the domain and migration increment**

```bash
git add src/daihougou/detection_region.py src/daihougou/storage.py tests/app/test_detection_region.py tests/app/test_storage.py
git commit -m "feat: persist camera detection regions"
```

---

### Task 2: Apply Detection Regions in the FFmpeg Frame Source

**Files:**
- Modify: `src/daihougou/vision/frame_source.py`
- Modify: `tests/app/vision/test_frame_source.py`

**Interfaces:**
- Consumes: `DetectionRegion`, `FULL_FRAME_REGION`
- Produces: `build_ffmpeg_command(stream_url: str, fps: float, size: int = PERSON_FRAME_SIZE, region: DetectionRegion = FULL_FRAME_REGION) -> list[str]`
- Produces: `FfmpegFrameSource(..., region: DetectionRegion = FULL_FRAME_REGION, ...)`

- [ ] **Step 1: Write failing command tests for full-frame and cropped inputs**

Extend `tests/app/vision/test_frame_source.py`:

```python
def test_full_frame_keeps_existing_filter_without_crop() -> None:
    command = build_ffmpeg_command("rtsp://camera/front", 1.0, 256, FULL_FRAME_REGION)
    video_filter = command[command.index("-vf") + 1]
    assert video_filter.startswith("fps=1.0,scale=256:256")
    assert "crop=" not in video_filter


def test_region_crop_precedes_fps_scale_and_padding() -> None:
    region = DetectionRegion(0.25, 0.1, 0.5, 0.8)
    command = build_ffmpeg_command("rtsp://camera/front", 1.0, 416, region)
    video_filter = command[command.index("-vf") + 1]
    assert video_filter.startswith(
        "crop=w=iw*0.500000:h=ih*0.800000:x=iw*0.250000:y=ih*0.100000,"
        "fps=1.0,scale=416:416"
    )
```

Add real-FFmpeg tests guarded by the existing `shutil.which("ffmpeg")` marker. First generate a 320x180 frame with different left/right colors, apply the production video filter for a right-half region, and assert the fixed output contains only the right-half color apart from gray letterbox padding.

Then generate five 1 FPS frames: black at startup, a left-half-only change at second 1, and a right-half change at second 3. Preserve the production `-vf` and raw-video output suffix while replacing only the RTSP input prefix with the deterministic lavfi source. Feed the five cropped output frames through a real `SceneChangeGate` and assert its decisions are `[True, False, False, True, False]`. This is the red regression proving out-of-region motion cannot reach the object rule's gate.

- [ ] **Step 2: Run the frame-source tests and verify RED**

Run: `pytest tests/app/vision/test_frame_source.py -v`

Expected: calls with a region fail because the command and source do not accept it.

- [ ] **Step 3: Implement crop-filter generation**

Build the filter list in order. Append a formatted crop component only when `region.is_full_frame` is false, then append the existing `fps`, `scale`, and `pad` components. Keep six fixed decimal places in crop expressions so validated data, not arbitrary strings, reaches FFmpeg. Pass the region from `FfmpegFrameSource.__init__()` into `build_ffmpeg_command()`.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `pytest tests/app/vision/test_frame_source.py -v`

Expected: command tests pass; real FFmpeg tests pass where FFmpeg is installed and skip otherwise.

- [ ] **Step 5: Commit the crop increment**

```bash
git add src/daihougou/vision/frame_source.py tests/app/vision/test_frame_source.py
git commit -m "feat: crop camera frames to detection regions"
```

---

### Task 3: Reconcile Region Changes in the Camera Runtime

**Files:**
- Modify: `src/daihougou/camera_runtime.py`
- Modify: `src/daihougou/runtime.py`
- Modify: `src/daihougou/main.py`
- Modify: `tests/app/test_camera_runtime.py`
- Modify: `tests/app/test_runtime.py`
- Modify: `tests/app/test_main.py`

**Interfaces:**
- Consumes: `CameraConfig.detection_region`, region-aware `FfmpegFrameSource`
- Produces: `CameraView.detection_region: DetectionRegion = FULL_FRAME_REGION`
- Produces: `Runtime.set_camera_detection_region(camera_id: str, region: DetectionRegion) -> None`
- Produces: `CameraRuntime(..., suppress_initial_object_detection: bool = False)`
- Produces: frame source factory signature `(url: str, size: int, region: DetectionRegion) -> FrameSource`

- [ ] **Step 1: Write a failing camera-runtime test for one-time object suppression**

Add object-capable fakes to `tests/app/test_camera_runtime.py` and prove that `suppress_initial_object_detection=True` causes the first frame to establish the scene baseline without calling `detect_objects`, while a visibly changed second frame calls it exactly once. Assert the person pipeline still processes both frames when the welcome rule is enabled. Add a companion test with the default flag proving ordinary startup still detects objects on its first frame.

- [ ] **Step 2: Run the camera-runtime tests and verify RED**

Run: `pytest tests/app/test_camera_runtime.py -v`

Expected: `CameraRuntime` rejects the new keyword or performs first-frame object inference.

- [ ] **Step 3: Implement one-time object suppression**

Store a boolean flag on `CameraRuntime`. On the first frame considered by the object branch, always call `self._scene_gate.changed(sample.frame)` to seed its reference; if suppression is pending, clear the flag and skip creating the object job. Do not alter person inference, later scene-change checks, missing-frame invalidation, or ordinary startup defaults.

- [ ] **Step 4: Run camera-runtime tests and verify GREEN**

Run: `pytest tests/app/test_camera_runtime.py -v`

Expected: all camera-runtime tests pass.

- [ ] **Step 5: Write failing runtime reconciliation tests**

Adjust the runtime test source factory to record `(camera_id, size, region)` for each construction. Add scenarios proving:

```python
await runtime.set_rule_enabled("front", True)
await runtime.set_rule_enabled("back", True)
old_front = sources["front"]
old_back = sources["back"]

region = DetectionRegion(0.1, 0.2, 0.6, 0.5)
await runtime.set_camera_detection_region("front", region)

assert old_front.stopped is True
assert sources["back"] is old_back
assert old_back.stop_count == 0
assert source_calls[-1] == ("front", 256, region)
assert runtime.snapshot().cameras[0].detection_region == region
```

Add separate tests proving an identical save does not restart, a disabled camera save creates no source, the region persists when source construction raises, the target view becomes degraded with `camera_start_failed`, and an unknown camera raises `KeyError` without creating a source. Record the event count before and after a successful region update and assert it is unchanged.

- [ ] **Step 6: Run runtime and production-assembly tests and verify RED**

Run: `pytest tests/app/test_runtime.py tests/app/test_main.py -v`

Expected: missing runtime setter/view field and missing third frame-source argument cause failures.

- [ ] **Step 7: Implement region-aware reconciliation and failure isolation**

Change `_camera_descriptors` to store `(frozenset[str], int, DetectionRegion)`. Let `_reconcile()` accept a set of camera IDs whose new runtime should suppress its first object detection. Include `config.detection_region` in every desired descriptor and pass it to `_make_source()`.

Implement `set_camera_detection_region()` under the existing lock:

```python
async def set_camera_detection_region(
    self, camera_id: str, region: DetectionRegion
) -> None:
    current = next(
        (item for item in self._storage.list_cameras() if item.stream_id == camera_id),
        None,
    )
    if current is None:
        raise KeyError(camera_id)
    if current.detection_region == region:
        return
    self._storage.set_camera_detection_region(camera_id, region)
    await self._reconcile(suppress_initial_object_for=frozenset({camera_id}))
```

Add a per-camera `_camera_runtime_errors` map. Catch source construction/start errors inside the per-camera reconciliation branch, remove any partially installed runtime, store `camera_start_failed`, and continue without rolling back storage or affecting other cameras. Clear that error when the camera starts successfully or becomes undesired. Make `_camera_view()` report the stored failure as a degraded stream and degraded enabled pipelines.

Update `CameraView` with a full-frame default so existing test fixtures remain source-compatible. The editor reads the field from the server-side view; do not add the region to `/healthz` because that API does not need it. Update the production factory in `main.py` to pass `region=region` into `FfmpegFrameSource`; retain only the existing two-argument `FfmpegFrameSource(url, fps)` compatibility fallback needed by `test_main.py` fakes.

- [ ] **Step 8: Run focused runtime tests and the complete Python suite**

Run: `pytest tests/app/test_camera_runtime.py tests/app/test_runtime.py tests/app/test_main.py -v`

Expected: all focused tests pass.

Run: `pytest -q`

Expected: complete suite passes.

- [ ] **Step 9: Commit the runtime increment**

```bash
git add src/daihougou/camera_runtime.py src/daihougou/runtime.py src/daihougou/main.py tests/app/test_camera_runtime.py tests/app/test_runtime.py tests/app/test_main.py
git commit -m "feat: apply camera region updates at runtime"
```

---

### Task 4: Capture Uncropped One-Shot Camera Snapshots

**Files:**
- Create: `src/daihougou/camera_snapshot.py`
- Create: `tests/app/test_camera_snapshot.py`
- Modify: `src/daihougou/runtime.py`
- Modify: `src/daihougou/main.py`
- Modify: `tests/app/test_runtime.py`
- Modify: `tests/app/test_main.py`
- Modify: `tests/app/test_generated_video_pipeline.py`

**Interfaces:**
- Produces: `SnapshotUnavailable(RuntimeError)` with stable message `camera_snapshot_unavailable`
- Produces: `build_snapshot_command(rtsp_url: str) -> list[str]`
- Produces: `CameraSnapshotter.capture(rtsp_url: str) -> bytes`
- Produces: `Snapshotter` protocol with synchronous `capture(rtsp_url: str) -> bytes`
- Produces: required `Runtime(..., snapshotter: Snapshotter, ...)` dependency
- Produces: `Runtime.capture_camera_snapshot(camera_id: str) -> bytes` as an async method
- Consumes: `rtsp_stream_url(base_url, camera_id)` and saved camera IDs from storage

- [ ] **Step 1: Write failing snapshot command and failure tests**

Create `tests/app/test_camera_snapshot.py`. Assert the command uses `-nostdin`, TCP RTSP, one video frame, no audio, no shell, a scale filter bounded to 1280 without upscaling, MJPEG image-pipe output, and `-q:v 4`. Inject a runner to verify valid JPEG bytes beginning with `b"\xff\xd8"` are returned. Cover nonzero exit, timeout, empty output, and non-JPEG output; every failure must raise only `SnapshotUnavailable("camera_snapshot_unavailable")` even if fake stderr contains credentials and an RTSP URL.

- [ ] **Step 2: Run snapshot unit tests and verify RED**

Run: `pytest tests/app/test_camera_snapshot.py -v`

Expected: collection fails because `daihougou.camera_snapshot` does not exist.

- [ ] **Step 3: Implement the snapshot process boundary**

Use a runner signature `Callable[[list[str], float], subprocess.CompletedProcess[bytes]]`. The default runner calls `subprocess.run(command, stdin=DEVNULL, capture_output=True, check=False, timeout=10)`. Do not set `text=True`, log stderr, or preserve it in exceptions. Translate `OSError`, `subprocess.TimeoutExpired`, nonzero status, empty bytes, and invalid JPEG magic into the stable exception. The scale filter must preserve aspect ratio, cap each input dimension at 1280, and avoid enlarging smaller inputs before emitting exactly one JPEG frame.

- [ ] **Step 4: Add and run a real-FFmpeg snapshot test**

Add a test guarded by `shutil.which("ffmpeg")` that replaces the RTSP input prefix with a lavfi `color` source, runs the production scale/output suffix, decodes the returned JPEG with OpenCV, and asserts a 1920x1080 source becomes 1280x720 while a 640x360 source remains 640x360.

Run: `pytest tests/app/test_camera_snapshot.py -v`

Expected: unit tests pass; real FFmpeg test passes where available and skips otherwise.

- [ ] **Step 5: Write failing runtime snapshot tests**

Inject a snapshotter fake into `Runtime`. Test that `capture_camera_snapshot("room/a b")` passes the correctly percent-encoded RTSP URL, works while every rule is disabled, does not create a detection `FrameSource`, and raises `KeyError` for an unsaved camera. Start two concurrent captures and use events in the fake to assert the second does not enter the snapshotter until the first completes, while an unrelated `set_camera_speaker()` completes before capture release.

- [ ] **Step 6: Run runtime snapshot tests and verify RED**

Run: `pytest tests/app/test_runtime.py -k snapshot -v`

Expected: `Runtime` lacks snapshot injection and capture methods.

- [ ] **Step 7: Implement serialized capture outside the runtime lock**

Add a sync `Snapshotter` protocol to `runtime.py`, inject it into `Runtime`, and create `self._snapshot_semaphore = asyncio.Semaphore(1)`. Resolve camera existence synchronously before entering the semaphore, build the encoded RTSP URL, and call `snapshotter.capture` with `asyncio.to_thread`. Do not acquire `self._lock` while waiting or capturing.

Wire `CameraSnapshotter()` in `create_production_app()`. Update production assembly tests to fake it without running FFmpeg, and pass a non-capturing fake snapshotter to every direct `Runtime` construction in `test_runtime.py` and `test_generated_video_pipeline.py`.

- [ ] **Step 8: Run focused and full Python tests**

Run: `pytest tests/app/test_camera_snapshot.py tests/app/test_runtime.py tests/app/test_main.py -v`

Expected: focused tests pass.

Run: `pytest -q`

Expected: complete suite passes.

- [ ] **Step 9: Commit the snapshot increment**

```bash
git add src/daihougou/camera_snapshot.py src/daihougou/runtime.py src/daihougou/main.py tests/app/test_camera_snapshot.py tests/app/test_runtime.py tests/app/test_main.py tests/app/test_generated_video_pipeline.py
git commit -m "feat: capture uncropped camera snapshots"
```

---

### Task 5: Add Region Editor Web Contracts and Server-Rendered Page

**Files:**
- Create: `src/daihougou/templates/camera_region.html`
- Modify: `src/daihougou/templates/index.html`
- Modify: `src/daihougou/web.py`
- Modify: `tests/app/test_web.py`

**Interfaces:**
- Consumes: `Runtime.capture_camera_snapshot()`, `Runtime.set_camera_detection_region()`, `CameraView.detection_region`
- Produces: `GET /camera-region?camera_id=<encoded-id>`
- Produces: `POST /commands/camera-snapshot` returning `image/jpeg`
- Produces: `POST /commands/camera-region` returning JSON to JavaScript or a 303 back to the editor

- [ ] **Step 1: Extend the fake runtime and write failing page tests**

Give `FakeRuntime` a `region_updates` list, configurable snapshot bytes/error, `capture_camera_snapshot()`, and `set_camera_detection_region()`. Make `snapshot_fixture()` assign `FULL_FRAME_REGION` unless a region override is supplied.

Add tests that the home page places a compact “检测区域” link inside each existing camera identity cell using `urlencode({"camera_id": camera_id})`, displays “已划定” only for non-full regions, and does not print raw coordinates. Test `GET /camera-region` for `room/a b` returns the camera name, saved values, preview shell, four inputs, refresh/reset/cancel/save controls, and `Cache-Control: no-store`; unknown cameras return 404.

- [ ] **Step 2: Run the page tests and verify RED**

Run: `pytest tests/app/test_web.py -k 'region or detection_area' -v`

Expected: missing region route, fields, and home links cause failures.

- [ ] **Step 3: Implement page lookup and rendering**

Extend `ManagedRuntime` with the two region methods. Add a helper that finds a `CameraView` by exact stream ID or raises a 404. Render `camera_region.html` with `camera`, `csrf_token`, `region_error`, `saved`, and the submitted or persisted region. Apply the existing `prepare_html()` helper so the editor HTML is `no-store` and receives the CSRF cookie.

Keep the page unframed and work-focused: title and camera identity at the top, preview/editor as the main surface, coordinate controls below it, and a single bottom command row. Include `region-editor.js` with `defer`; Task 6 supplies behavior.

- [ ] **Step 4: Write failing snapshot and save endpoint tests**

Add tests for:

- valid snapshot returns exact bytes, `Content-Type: image/jpeg`, and `Cache-Control: no-store`;
- missing/wrong CSRF or cross-origin snapshot returns 403 without calling runtime;
- unknown camera returns 404;
- `SnapshotUnavailable` returns 503 with stable detail, no sensitive text, and `Cache-Control: no-store`;
- valid region save calls runtime with a six-decimal `DetectionRegion` and JSON reports `saved: true`, the region, and current camera stream state;
- form submission without JSON accept redirects to the same encoded editor URL;
- invalid numeric text, NaN, infinity, too-small size, and overflow return 422 without writing;
- missing/wrong CSRF and cross-origin save return 403;
- multiple valid saves append both fake runtime updates, proving last-write-wins behavior.

Add both new command paths to the existing parameterized CSRF and cross-origin tests.

- [ ] **Step 5: Run endpoint tests and verify RED**

Run: `pytest tests/app/test_web.py -k 'snapshot or region or csrf or cross_origin' -v`

Expected: missing command routes and protocol methods cause failures.

- [ ] **Step 6: Implement protected snapshot and region commands**

Use `Form(default="")` fields for camera ID and all four coordinate strings. Call `_verify_write_request()` before camera lookup or parsing. Parse with `float`, then construct `DetectionRegion`; map conversion and validation failures to 422. Never interpolate camera IDs into route paths.

Return snapshot bytes with `Response(content=payload, media_type="image/jpeg", headers={"Cache-Control": "no-store"})`. Map `KeyError` to 404 and `SnapshotUnavailable` to 503 without returning its cause.

For JSON saves, return:

```json
{
  "saved": true,
  "region": {"x": 0.1, "y": 0.2, "width": 0.6, "height": 0.5},
  "camera": {"stream_id": "front", "stream": "starting", "last_error": ""}
}
```

For regular form saves, redirect to `/camera-region?<encoded camera_id>&saved=1` with status 303. For regular invalid form submissions, re-render the editor with submitted values and a concise error at status 422; JSON requests receive the same error family as JSON. A persisted region remains a successful save even when the returned camera state is degraded.

- [ ] **Step 7: Run all Web tests and commit the backend page increment**

Run: `pytest tests/app/test_web.py -v`

Expected: all Web tests pass.

```bash
git add src/daihougou/templates/camera_region.html src/daihougou/templates/index.html src/daihougou/web.py tests/app/test_web.py
git commit -m "feat: add camera region editor endpoints"
```

---

### Task 6: Implement the Pointer-Based Region Editor

**Files:**
- Create: `src/daihougou/static/region-editor.js`
- Create: `tests/js/region-editor.test.js`
- Modify: `src/daihougou/templates/camera_region.html`
- Modify: `src/daihougou/static/app.css`
- Modify: `tests/app/test_web.py`

**Interfaces:**
- Produces: pure functions `normalizeRegion`, `validateRegion`, `drawRegion`, `moveRegion`, and `resizeRegion`
- Consumes: `data-region-editor` DOM attributes, snapshot/save endpoints, and server-rendered persisted coordinates
- Browser contract: custom event-free vanilla JS initialized once on `DOMContentLoaded`

- [ ] **Step 1: Write failing dependency-free geometry tests**

Create `tests/js/region-editor.test.js` using `node:test` and `node:assert/strict`. Require `../../src/daihougou/static/region-editor.js` and cover:

```javascript
test("draw clamps to the image and enforces two percent", () => {
  assert.deepEqual(drawRegion({ x: 0.9, y: 0.95 }, { x: 1.2, y: 1.1 }), {
    x: 0.9, y: 0.95, width: 0.1, height: 0.05,
  });
  assert.equal(validateRegion({ x: 0, y: 0, width: 0.019999, height: 1 }).valid, false);
});

test("move preserves size at every boundary", () => {
  assert.deepEqual(
    moveRegion({ x: 0.2, y: 0.3, width: 0.4, height: 0.5 }, -1, 1),
    { x: 0, y: 0.5, width: 0.4, height: 0.5 },
  );
});

test("each edge and corner resize stays in bounds", () => {
  assert.deepEqual(
    resizeRegion({ x: 0.2, y: 0.2, width: 0.5, height: 0.5 }, "nw", -0.1, -0.1),
    { x: 0.1, y: 0.1, width: 0.6, height: 0.6 },
  );
});
```

Cover all eight handle names (`n`, `ne`, `e`, `se`, `s`, `sw`, `w`, `nw`), reversed draw directions, six-decimal rounding, finite-value validation, and exact full-frame reset.

- [ ] **Step 2: Run Node tests and verify RED**

Run: `node --test tests/js/region-editor.test.js`

Expected: module load fails because `region-editor.js` does not exist.

- [ ] **Step 3: Implement and export pure geometry functions**

Use a small UMD-style boundary: assign exports to `module.exports` when Node is present and to `window.DetectionRegionEditor` in the browser. Guard browser initialization with `typeof document !== "undefined"` so Node never touches the DOM. Keep geometry functions independent of the DOM. Round output to six decimals after every operation, clamp pointer coordinates to `[0, 1]`, preserve the opposite resize edges, and enforce the 0.02 minimum without moving the anchored edge.

- [ ] **Step 4: Run geometry tests and verify GREEN**

Run: `node --test tests/js/region-editor.test.js`

Expected: all geometry tests pass.

- [ ] **Step 5: Complete the editor DOM and styles**

In `camera_region.html`, use an intrinsic-size `<img>` beneath a positioned overlay. Add one movable region rectangle and eight `type="button"` stable-size handle buttons with `aria-label` values naming their direction. Use pointer events with `setPointerCapture()` and `touch-action: none` on the interactive overlay so mouse and touch share one interaction path. Empty-overlay drags call `drawRegion`, rectangle drags call `moveRegion`, and handles call `resizeRegion`.

On load and “刷新画面”, submit the protected snapshot form with `fetch`, turn the JPEG blob into an object URL, revoke the previous URL before replacement, and only enable editing after the image `load` event. A failed refresh revokes and removes the old preview, retains coordinate text as locked state, and enables only retry and full-frame reset. Choosing full-frame reset while offline keeps coordinate inputs locked but enables save for that exact `(0, 0, 1, 1)` draft; any other offline draft remains unsaveable.

Synchronize percentage inputs and hidden normalized fields in both directions. Display two decimals but preserve the six-decimal model until a user changes that input. Invalid manual input shows one concise inline error and disables save. Reset creates exact full-frame values; cancel navigates directly to `/`. Save posts JSON, retains the page, updates the persisted baseline, and shows either “区域已保存” or “区域已保存，摄像头尚未恢复” from returned camera state. Do not register `beforeunload`.

Extend `app.css` with responsive constraints:

- editor main width `min(960px, 100%)`;
- preview uses the actual image aspect ratio, `max-height: 70vh`, and never stretches;
- overlay and image share identical dimensions;
- control handles remain at least 24px visual size and 36px touch target without resizing layout;
- coordinate fields use four columns on desktop and two columns below 760px;
- action buttons wrap below 440px without text overflow;
- colors remain consistent with the existing neutral, green, amber, and red palette.

- [ ] **Step 6: Add server-markup assertions for editor hooks**

Extend `tests/app/test_web.py` to assert the page has exactly eight direction handles, the overlay is labeled, the four visible inputs have numeric input mode and error associations, the saved normalized values are present in data attributes, and there is no `beforeunload` inline handler or camera URL in markup.

- [ ] **Step 7: Run JavaScript, Web, lint, and full tests**

Run: `node --test tests/js/region-editor.test.js`

Expected: all JavaScript tests pass.

Run: `pytest tests/app/test_web.py -v`

Expected: all Web tests pass.

Run: `ruff check src tests`

Expected: no lint errors.

Run: `pytest -q`

Expected: complete Python suite passes.

- [ ] **Step 8: Commit the interactive editor increment**

```bash
git add src/daihougou/static/region-editor.js src/daihougou/static/app.css src/daihougou/templates/camera_region.html tests/js/region-editor.test.js tests/app/test_web.py
git commit -m "feat: implement camera region editor"
```

---

### Task 7: Operator Documentation and Final Verification

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: complete storage, crop, runtime, snapshot, Web, and editor behavior from Tasks 1-6
- Produces: concise operator documentation for selecting a camera region

- [ ] **Step 1: Document the operator workflow**

Add a short README section under the management-page instructions: open “检测区域” for a camera, wait for the current snapshot, draw or enter a rectangle, save, and use “恢复全画面” to remove the restriction. State that one region applies to all visual rules, saves briefly restart that camera, offline cameras can only be reset to full frame, and camera movement, rotation, resolution, or aspect-ratio changes do not remap the region.

- [ ] **Step 2: Run final automated verification**

Run: `ruff check src tests`

Expected: no lint errors.

Run: `node --test tests/js/region-editor.test.js`

Expected: all JavaScript tests pass.

Run: `pytest -q`

Expected: complete Python suite passes with only environment-declared FFmpeg skips when FFmpeg is unavailable.

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 3: Perform desktop and mobile browser verification**

Start the local application on an unused loopback port with a temporary seeded camera and a test snapshotter returning a deterministic 16:9 JPEG. Use the in-app browser at 1440x900, 768x1024, and 390x844. At every viewport verify the image is nonblank and uncropped; editor overlay matches the image bounds; draw, move, every edge/corner handle, percentage input, refresh-preserves-draft, reset, cancel, snapshot-failure lockout, and save-in-place work; controls do not overlap or overflow; and homepage status changes to “已划定”. Capture screenshots for the editor desktop and mobile states, then stop the server.

- [ ] **Step 4: Review the final diff against every spec acceptance criterion**

Read `docs/superpowers/specs/2026-09-02-camera-detection-region-design.md` alongside `git diff adf212a --stat` and the final code. Check all seven acceptance criteria explicitly: independent editor, target-only restart, ROI-only scene gating, no save-triggered announcements, deterministic offline/special-ID behavior, v2 data preservation, and no snapshot persistence. Fix any uncovered gap with a new failing test before changing production code.

- [ ] **Step 5: Commit operator documentation**

```bash
git add README.md
git commit -m "docs: explain camera detection regions"
```
