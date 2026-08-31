# Object Category Announcement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the confirmed per-camera picture-book object announcement rule without regressing the existing person-entry welcome rule.

**Architecture:** Keep separate person and object detectors behind one serialized multi-kind inference scheduler. Each camera uses one latest-frame source and runs only its enabled pipelines; the object pipeline is gated by per-camera scene change. Extend the existing rule, storage, speaker, runtime, and server-rendered Web boundaries rather than adding plugins or a frontend framework.

**Tech Stack:** Python 3.12, asyncio, FastAPI, Jinja2, SQLite, NumPy, OpenCV DNN, FFmpeg, pytest, Ruff

**Spec:** `docs/superpowers/specs/2026-08-31-object-category-announcement-design.md`

## Global Constraints

- Do not execute this plan unless `docs/validation/object-category-announcement-poc.md` exists and records `Decision: proceed with production implementation`.
- Preserve SQLite schema version 2 and all existing camera, welcome-rule, speaker, and event data.
- Keep `welcome_on_person_entry` and `announce_objects_on_scene_change` as the only accepted rule IDs.
- Keep person and object models independently loadable, independently degradable, and serialized on CPU.
- Keep one FFmpeg process per active camera: 256 square pixels for person-only and 416 square pixels whenever the object rule is enabled.
- Run scene checks at the existing input rate of 1 FPS with `64x64`, pixel delta `25`, and changed ratio `0.20` unless the passing PoC report records replacement values.
- Announce at most three unique non-person labels, exclude `book` at box area ratio >= 0.50, and never announce `unknown`.
- Never persist frames, bounding boxes, model paths, raw output tensors, or private validation data.
- Use TDD for every production behavior: add one failing test, verify the expected failure, implement the minimum, and verify green before continuing.

---

### Task 1: Built-In Rule Catalog and Idempotent Storage Backfill

**Files:**
- Modify: `src/daihougou/rules.py`
- Modify: `src/daihougou/storage.py`
- Modify: `tests/app/test_storage.py`

**Interfaces:**
- Consumes: existing schema v2 database and discovered camera IDs.
- Produces: `OBJECT_RULE_ID`, `BUILTIN_RULE_IDS`, `BUILTIN_RULE_NAMES`, and one disabled row per built-in rule per camera.

- [ ] **Step 1: Write failing storage tests**

```python
from daihougou.rules import (
    BUILTIN_RULE_IDS,
    BUILTIN_RULE_NAMES,
    OBJECT_RULE_ID,
    WELCOME_RULE_ID,
)


def test_initialize_backfills_disabled_object_rule_without_changing_welcome(
    tmp_path: Path,
) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.sync_cameras(["front"], "living_room")
    storage.set_camera_rule_enabled("front", WELCOME_RULE_ID, True)
    with sqlite3.connect(tmp_path / "app.db") as connection:
        connection.execute(
            "DELETE FROM camera_rules WHERE camera_id = ? AND rule_id = ?",
            ("front", OBJECT_RULE_ID),
        )

    storage.initialize()

    assert storage.camera_rule_enabled("front", WELCOME_RULE_ID) is True
    assert storage.camera_rule_enabled("front", OBJECT_RULE_ID) is False
    assert BUILTIN_RULE_IDS == (WELCOME_RULE_ID, OBJECT_RULE_ID)
    assert BUILTIN_RULE_NAMES[OBJECT_RULE_ID] == "绘本物体播报"


def test_new_camera_gets_every_builtin_rule_disabled(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "app.db")
    storage.initialize()
    storage.sync_cameras(["front"], "living_room")

    assert {
        rule_id: storage.camera_rule_enabled("front", rule_id)
        for rule_id in BUILTIN_RULE_IDS
    } == {
        WELCOME_RULE_ID: False,
        OBJECT_RULE_ID: False,
    }
```

- [ ] **Step 2: Run the tests and verify RED**

Run: `pytest tests/app/test_storage.py::test_initialize_backfills_disabled_object_rule_without_changing_welcome tests/app/test_storage.py::test_new_camera_gets_every_builtin_rule_disabled -v`

Expected: import fails because `OBJECT_RULE_ID` and `BUILTIN_RULE_IDS` do not exist.

- [ ] **Step 3: Add the catalog and idempotent inserts**

Add to `rules.py`:

```python
WELCOME_RULE_ID = "welcome_on_person_entry"
OBJECT_RULE_ID = "announce_objects_on_scene_change"
BUILTIN_RULE_IDS = (WELCOME_RULE_ID, OBJECT_RULE_ID)
BUILTIN_RULE_NAMES = {
    WELCOME_RULE_ID: "人员进入欢迎",
    OBJECT_RULE_ID: "绘本物体播报",
}
```

In `Storage.initialize`, after schema validation and before pruning, insert missing rules for existing cameras with this exact SQL for each built-in ID:

```sql
INSERT OR IGNORE INTO camera_rules(camera_id, rule_id, enabled, updated_at)
SELECT stream_id, ?, 0, ? FROM cameras
```

In `sync_cameras`, replace the single welcome insert for a newly inserted camera with `executemany` over `BUILTIN_RULE_IDS`. Do not create an object `rule_configs` row and do not change `SCHEMA_VERSION`.

- [ ] **Step 4: Run storage tests**

Run: `pytest tests/app/test_storage.py -v`

Expected: all storage tests pass, including v2 schema checks.

- [ ] **Step 5: Commit**

```bash
git add src/daihougou/rules.py src/daihougou/storage.py tests/app/test_storage.py
git commit -m "feat: persist built-in camera rules"
```

### Task 2: Scene Gate, Object Rule, and Replaceable Speech Metadata

**Files:**
- Create: `src/daihougou/vision/scene_change.py`
- Create: `src/daihougou/object_rule.py`
- Create: `src/daihougou/object_catalog.py`
- Create: `tests/app/vision/test_scene_change.py`
- Create: `tests/app/test_object_rule.py`
- Modify: `src/daihougou/rules.py`

**Interfaces:**
- Consumes: `ObjectDetection`, per-camera speaker lookup, and sampled monotonic time.
- Produces: `SceneChangeGate.changed(frame) -> bool` and `ObjectCategoryAnnouncementRule.handle(result, at) -> SpeechAction | None`.

- [ ] **Step 1: Write failing scene gate tests**

```python
import numpy as np

from daihougou.vision.scene_change import SceneChangeGate


def test_first_frame_changes_but_identical_second_frame_does_not() -> None:
    gate = SceneChangeGate()
    frame = np.zeros((416, 416, 3), dtype=np.uint8)

    assert gate.changed(frame) is True
    assert gate.changed(frame.copy()) is False


def test_twenty_percent_large_pixel_change_crosses_candidate_threshold() -> None:
    gate = SceneChangeGate()
    before = np.zeros((64, 64, 3), dtype=np.uint8)
    after = before.copy()
    after[:13, :, :] = 25

    assert gate.changed(before) is True
    assert gate.changed(after) is True
```

- [ ] **Step 2: Run scene tests and verify RED**

Run: `pytest tests/app/vision/test_scene_change.py -v`

Expected: collection fails because `daihougou.vision.scene_change` does not exist.

- [ ] **Step 3: Implement the scene gate**

```python
SCENE_SAMPLE_SIZE = 64
SCENE_PIXEL_DELTA = 25
SCENE_CHANGED_RATIO = 0.20


class SceneChangeGate:
    def __init__(self) -> None:
        self._reference: npt.NDArray[np.uint8] | None = None

    def changed(self, frame: npt.NDArray[np.uint8]) -> bool:
        reduced = cv2.resize(frame, (SCENE_SAMPLE_SIZE, SCENE_SAMPLE_SIZE))
        gray = cv2.cvtColor(reduced, cv2.COLOR_BGR2GRAY)
        reference = self._reference
        self._reference = gray
        if reference is None:
            return True
        ratio = float(np.count_nonzero(cv2.absdiff(reference, gray) >= SCENE_PIXEL_DELTA))
        return ratio / gray.size >= SCENE_CHANGED_RATIO

    def invalidate(self) -> None:
        self._reference = None
```

- [ ] **Step 4: Write failing object rule tests**

```python
from daihougou.object_rule import ObjectCategoryAnnouncementRule
from daihougou.object_catalog import SUPPORTED_CATEGORY_NAMES
from daihougou.rules import OBJECT_RULE_ID
from daihougou.vision.object_detector import COCO_CATEGORIES, DetectedObject, ObjectDetection


class RuleState:
    def __init__(self, speaker_id: str) -> None:
        self.speaker_id = speaker_id

    def camera_speaker_id(self, camera_id: str) -> str:
        assert camera_id == "front"
        return self.speaker_id


def detected(label: str, confidence: float, box: tuple[int, int, int, int]) -> DetectedObject:
    return DetectedObject(label, confidence, *box)


def test_object_rule_filters_deduplicates_limits_and_builds_replaceable_action() -> None:
    state = RuleState(speaker_id="living_room")
    rule = ObjectCategoryAnnouncementRule("front", state)
    result = ObjectDetection(
        objects=(
            detected("person", 0.99, (0, 0, 50, 50)),
            detected("book", 0.95, (0, 0, 416, 300)),
            detected("dog", 0.90, (0, 0, 40, 40)),
            detected("sports ball", 0.85, (0, 0, 40, 40)),
            detected("cat", 0.80, (0, 0, 40, 40)),
            detected("cat", 0.70, (0, 0, 30, 30)),
            detected("bird", 0.60, (0, 0, 30, 30)),
        ),
        latency_ms=220,
    )

    action = rule.handle(result, occurred_monotonic=10.0, frame_size=(416, 416))

    assert action is not None
    assert action.rule_id == OBJECT_RULE_ID
    assert action.text == "dog, sports ball, cat"
    assert action.coalesce_key == ("front", OBJECT_RULE_ID)
    assert action.max_queue_age_seconds == 3.0
    assert action.completion_event_kind == "object_announcement_completed"
    assert action.superseded_event_kind == "object_announcement_superseded"
    assert action.details == {
        "objects": [
            {"label": "dog", "confidence": 0.9},
            {"label": "sports ball", "confidence": 0.85},
            {"label": "cat", "confidence": 0.8},
        ]
    }


def test_supported_catalog_matches_every_non_person_model_category() -> None:
    assert tuple(SUPPORTED_CATEGORY_NAMES) == COCO_CATEGORIES[1:]
    assert SUPPORTED_CATEGORY_NAMES["cat"] == "猫"
    assert SUPPORTED_CATEGORY_NAMES["dog"] == "狗"
```

- [ ] **Step 5: Run object rule tests and verify RED**

Run: `pytest tests/app/test_object_rule.py -v`

Expected: collection fails because `daihougou.object_rule` does not exist.

- [ ] **Step 6: Extend `SpeechAction` and implement the object rule**

Append defaulted fields to the existing frozen `SpeechAction` so welcome call sites remain valid:

```python
details: dict[str, object] = field(default_factory=dict)
coalesce_key: tuple[str, str] | None = None
max_queue_age_seconds: float | None = None
completion_event_kind: str = "speaker_completed"
superseded_event_kind: str | None = None
```

Create `SUPPORTED_CATEGORY_NAMES` in `object_catalog.py` with the exact insertion order and translations from the spec. `ObjectCategoryAnnouncementRule` must read only `camera_speaker_id(camera_id)` from its state and call the PoC-validated `select_announced_objects` helper. Return `None` when the helper is empty. Build details from its ordered result before returning the action and round confidence to four decimal places.

- [ ] **Step 7: Run focused rule tests and lint**

Run: `pytest tests/app/test_rules.py tests/app/test_object_rule.py tests/app/vision/test_scene_change.py -v`

Expected: all tests pass.

Run: `ruff check src/daihougou/rules.py src/daihougou/object_rule.py src/daihougou/object_catalog.py src/daihougou/vision/scene_change.py tests/app/test_object_rule.py tests/app/vision/test_scene_change.py`

Expected: exit 0.

- [ ] **Step 8: Commit**

```bash
git add src/daihougou/rules.py src/daihougou/object_rule.py src/daihougou/object_catalog.py src/daihougou/vision/scene_change.py tests/app/test_object_rule.py tests/app/vision/test_scene_change.py
git commit -m "feat: define scene change object announcement rule"
```

### Task 3: Latest-Wins Object Speech and Three-Second Expiry

**Files:**
- Modify: `src/daihougou/speaker_worker.py`
- Modify: `tests/app/test_speaker_worker.py`

**Interfaces:**
- Consumes: extended `SpeechAction` metadata from Task 2.
- Produces: stable FIFO slots across cameras/rules, keyed replacement for pending object actions, and playback-time expiry.

- [ ] **Step 1: Write failing replacement and expiry tests**

Add tests that hold the speaker's first call with a `threading.Event`, enqueue two object actions with the same `coalesce_key`, release the first call, and assert only the newer object text is spoken. Assert the old action records `object_announcement_superseded` with its object details. Add a clock-injected test where an object action created at `1.0` is dequeued at `4.01`, is not spoken, and records `speaker_skipped_expired`; verify a welcome action with no max age still plays.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/app/test_speaker_worker.py -v`

Expected: replacement test speaks both object actions or queue-expiry construction fails because `SpeakerManager` has no clock parameter.

- [ ] **Step 3: Implement coalesced queue tokens**

Keep each existing `asyncio.Queue`, but allow entries of `SpeechAction | tuple[str, str] | None`. Add:

```python
self._pending_actions: dict[str, dict[tuple[str, str], SpeechAction]] = {
    speaker_id: {} for speaker_id in self._speakers
}
self._clock = clock
```

For an action with no `coalesce_key`, enqueue the action unchanged. For a keyed action already in that speaker's pending map, replace the mapped value, record the old action using its non-null `superseded_event_kind`, and do not enqueue another token. For a new key, enqueue the key first and then store the action; if the queue is full, do neither. The consumer resolves a tuple token by popping its current mapped action. Once popped, a later submission creates a new FIFO slot and cannot interrupt the active call.

Before existing enabled/pairing checks, drop an action when both values exist and:

```python
self._clock() - action.created_monotonic > action.max_queue_age_seconds
```

Use `action.completion_event_kind` for the speaker result event and merge `action.details` with the safe result code. Use the old defaults for welcome events.

- [ ] **Step 4: Run speaker tests and lint**

Run: `pytest tests/app/test_speaker_worker.py -v`

Expected: all tests pass, including serialization and concurrent-speaker behavior.

Run: `ruff check src/daihougou/speaker_worker.py tests/app/test_speaker_worker.py`

Expected: exit 0.

- [ ] **Step 5: Commit**

```bash
git add src/daihougou/speaker_worker.py tests/app/test_speaker_worker.py
git commit -m "feat: coalesce and expire object announcements"
```

### Task 4: Multi-Kind Serialized Inference Scheduler

**Files:**
- Modify: `src/daihougou/detection_scheduler.py`
- Modify: `tests/app/test_detection_scheduler.py`

**Interfaces:**
- Consumes: person and object detector factories indexed by `DetectorKind`.
- Produces: `enable`, `disable`, `close`, `detect_person`, `detect_objects`, and per-kind `DetectorSnapshot`.

- [ ] **Step 1: Replace single-detector tests with failing multi-kind contract tests**

Test these behaviors independently:

1. Enabling `PERSON` loads only the person factory; enabling `OBJECT` later loads exactly one object detector.
2. Concurrent person and object calls never overlap in their synchronous `detect` methods.
3. FIFO submission across `("person", "front")`, `("object", "front")`, and `("person", "back")` preserves order.
4. A duplicate unfinished `(kind, camera_id)` request raises `detector_request_pending`.
5. An object detector exception marks only object `degraded`, fails queued object jobs, and a later person job still succeeds.
6. Disabling and re-enabling a degraded kind invokes its factory again; disabling the last kind stops the consumer.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/app/test_detection_scheduler.py -v`

Expected: tests fail because the scheduler constructor accepts one factory and has no detector-kind API.

- [ ] **Step 3: Introduce the exact public scheduler types**

```python
class DetectorKind(StrEnum):
    PERSON = "person"
    OBJECT = "object"


@dataclass(frozen=True)
class DetectorSnapshot:
    status: str
    loaded: bool
    fatal_error: bool


class DetectionScheduler:
    stop_timeout_seconds = 2.0
```

Implement constructor parameters `person_factory: Callable[[], PersonDetectorProtocol]` and `object_factory: Callable[[], ObjectDetectorProtocol]`. Add exact public methods `enable(kind: DetectorKind) -> None`, `disable(kind: DetectorKind) -> None`, `detect_person(camera_id: str, sample: FrameSample) -> PersonDetection`, `detect_objects(camera_id: str, sample: FrameSample) -> ObjectDetection`, `snapshot(kind: DetectorKind) -> DetectorSnapshot`, and `close() -> None`.

- [ ] **Step 4: Implement one consumer with isolated detector state**

Use one unbounded internal queue because callers are bounded to one future per key. Store pending futures by `(DetectorKind, camera_id)`, detector instances by kind, and per-kind status/fatal flags. `enable` loads with `asyncio.to_thread`; a load failure marks only that kind degraded and raises `detector_start_failed:<kind>`. `_consume` calls the selected instance in `asyncio.to_thread`. An inference exception fails and removes pending jobs for that kind, removes its detector, marks it degraded, and continues the loop instead of raising out of the consumer. `disable` fails only that kind's pending futures, drops its instance, and stops the consumer only when no detector remains. `close` disables both kinds and leaves both snapshots stopped.

- [ ] **Step 5: Run scheduler and related tests**

Run: `pytest tests/app/test_detection_scheduler.py tests/app/vision/test_person_detector.py tests/app/vision/test_object_detector.py -v`

Expected: all tests pass.

Run: `ruff check src/daihougou/detection_scheduler.py tests/app/test_detection_scheduler.py`

Expected: exit 0.

- [ ] **Step 6: Commit**

```bash
git add src/daihougou/detection_scheduler.py tests/app/test_detection_scheduler.py
git commit -m "refactor: schedule isolated vision detector kinds"
```

### Task 5: Adaptive Frame Source and Dual Camera Pipelines

**Files:**
- Modify: `src/daihougou/vision/frame_source.py`
- Modify: `src/daihougou/camera_runtime.py`
- Modify: `tests/app/vision/test_frame_source.py`
- Modify: `tests/app/test_camera_runtime.py`

**Interfaces:**
- Consumes: optional welcome/object rules, Task 4 scheduler, and one sized frame source.
- Produces: one camera loop that independently updates `person` and `object` pipeline snapshots.

- [ ] **Step 1: Write failing frame-size tests**

Change frame-source tests to construct both `FfmpegFrameSource("rtsp://127.0.0.1:8554/front", size=256)` and `FfmpegFrameSource("rtsp://127.0.0.1:8554/front", size=416)`. Assert commands use matching scale/pad dimensions, returned array shapes match the instance size, and invalid sizes outside `{256, 416}` raise `ValueError("unsupported frame size")`.

- [ ] **Step 2: Run frame tests and verify RED**

Run: `pytest tests/app/vision/test_frame_source.py -v`

Expected: construction fails because the frame source has no `size` parameter.

- [ ] **Step 3: Make frame dimensions instance-owned**

Change `build_ffmpeg_command(stream_url, fps, size)` and store `self._size` plus `self._frame_bytes = size * size * 3`. Build and reshape frames from those instance values. Remove module-level `FRAME_BYTES`; keep `PERSON_FRAME_SIZE = 256` and `OBJECT_FRAME_SIZE = 416` constants for callers and tests.

- [ ] **Step 4: Write failing dual-pipeline camera tests**

Add tests for:

1. Welcome-only camera calls only `detect_person` on every sample.
2. Object-only camera calls `detect_objects` on the first sample, skips an identical second sample, and calls again on a changed third sample.
3. Both rules enabled submit person and object detection concurrently for a changed sample and produce both action types without overlap inside the scheduler.
4. Object inference failure marks only the object pipeline degraded and prevents later object submissions for that camera session while person processing continues.
5. A frame gap invalidates both presence and scene reference; recovery analyzes the first object frame again.

- [ ] **Step 5: Run camera tests and verify RED**

Run: `pytest tests/app/test_camera_runtime.py -v`

Expected: fake scheduler contract and constructor fail because `CameraRuntime` supports only one person pipeline.

- [ ] **Step 6: Implement explicit optional pipelines**

Construct `CameraRuntime` with `welcome_rule: WelcomeRule | None`, `object_rule: ObjectCategoryAnnouncementRule | None`, and a new `SceneChangeGate`. For each sample, append a person coroutine only when welcome is present and append an object coroutine only when object is present, not locally failed, and `gate.changed(frame)` is true. Await the concrete list with `asyncio.gather(*jobs, return_exceptions=True)` and process results independently. Preserve person presence behavior. Pass object results to the object rule with `(frame_width, frame_height)` and submit a returned action. Extend `CameraSnapshot` with separate immutable `person` and `object` pipeline records containing status, confidence, latency, and error.

- [ ] **Step 7: Run camera, frame, and generated-video tests**

Run: `pytest tests/app/test_camera_runtime.py tests/app/vision/test_frame_source.py tests/app/test_generated_video_pipeline.py -v`

Expected: all tests pass after updating generated-video fakes to the explicit person methods.

Run: `ruff check src/daihougou/camera_runtime.py src/daihougou/vision/frame_source.py tests/app/test_camera_runtime.py tests/app/vision/test_frame_source.py`

Expected: exit 0.

- [ ] **Step 8: Commit**

```bash
git add src/daihougou/camera_runtime.py src/daihougou/vision/frame_source.py tests/app/test_camera_runtime.py tests/app/vision/test_frame_source.py tests/app/test_generated_video_pipeline.py
git commit -m "feat: run independent camera vision pipelines"
```

### Task 6: Multi-Rule Runtime Reconciliation and Health

**Files:**
- Modify: `src/daihougou/runtime.py`
- Modify: `tests/app/test_runtime.py`

**Interfaces:**
- Consumes: built-in storage rows, multi-kind scheduler, adaptive frame-source factory.
- Produces: generic `set_rule_enabled(camera_id, rule_id, enabled)`, per-rule camera views, per-kind detector views, and correct health aggregation.

- [ ] **Step 1: Write failing runtime behavior tests**

Add focused tests that prove:

1. Enabling object-only loads only the object kind, creates a 416 source, and leaves presence unknown.
2. Enabling welcome after object restarts neither model unnecessarily and keeps one source.
3. Disabling object while welcome stays enabled rebuilds the source at 256 and unloads only object.
4. The last visual rule disabled stops the camera source and both unused detector kinds.
5. Object factory failure leaves the object setting enabled, shows object degraded, keeps welcome ready, and makes overall degraded only while object is enabled.
6. Disabling then enabling object retries its factory.
7. Existing discovery failure and per-camera isolation tests still pass for both rule IDs.

- [ ] **Step 2: Run runtime tests and verify RED**

Run: `pytest tests/app/test_runtime.py -v`

Expected: tests fail because runtime updates only `WELCOME_RULE_ID` and snapshots contain one rule.

- [ ] **Step 3: Introduce exact view contracts**

```python
@dataclass(frozen=True)
class DetectorView:
    kind: str
    status: str
    loaded: bool


@dataclass(frozen=True)
class RuleView:
    id: str
    name: str
    enabled: bool
    status: str
    last_confidence: float | None
    last_detection_latency_ms: int | None
    last_error: str
    latest_trigger: StoredEvent | None


@dataclass(frozen=True)
class CameraView:
    stream_id: str
    speaker_id: str
    speaker: str
    available: bool | None
    stream: str
    rules: tuple[RuleView, ...]
    last_error: str
```

`RuntimeSnapshot` must expose `detectors: tuple[DetectorView, ...]`. `enabled_camera_count` counts cameras with any enabled rule; `ready_camera_count` counts cameras whose stream and every enabled rule are ready; `resource_warning` remains enabled-camera count >= 4.

- [ ] **Step 4: Reconcile desired camera and detector state**

Change `set_rule_enabled` to require a known `rule_id`, persist first, then reconcile under the existing lock. For every camera compute a frozen enabled-rule set. The desired frame size is 416 when object is enabled and 256 otherwise. Rebuild a camera runtime when its enabled-rule set or size differs from its current descriptor. Enable each detector kind when at least one available desired camera needs it; disable it when none do. If a detector load fails, preserve storage, mark its rule views degraded, skip that pipeline, and still construct any runnable sibling pipeline. Use `latest_rule_trigger(rule_id, camera_id)` independently.

- [ ] **Step 5: Aggregate health without global false failures**

Overall status is unhealthy only for app/database/dispatcher/speaker fatal failure. It is degraded for discovery failure, an enabled camera stream failure, or an enabled rule whose detector/pipeline is degraded. A stopped detector with zero dependent enabled cameras is normal. Return both detector states from health data without model paths.

- [ ] **Step 6: Run runtime tests and lint**

Run: `pytest tests/app/test_runtime.py tests/app/test_healthcheck.py -v`

Expected: all tests pass.

Run: `ruff check src/daihougou/runtime.py tests/app/test_runtime.py`

Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add src/daihougou/runtime.py tests/app/test_runtime.py tests/app/test_healthcheck.py
git commit -m "feat: reconcile per-camera visual rules"
```

### Task 7: Two-Rule Management UI and Supported Category Catalog

**Files:**
- Modify: `src/daihougou/web.py`
- Modify: `src/daihougou/templates/index.html`
- Modify: `src/daihougou/templates/settings.html`
- Modify: `src/daihougou/static/app.js`
- Modify: `src/daihougou/static/app.css`
- Modify: `tests/app/test_web.py`

**Interfaces:**
- Consumes: Task 6 views and `SUPPORTED_CATEGORY_NAMES` from `object_catalog.py`.
- Produces: generic rule commands, two compact controls per camera, read-only category table, and per-kind health JSON.

- [ ] **Step 1: Write failing Web contract tests**

Update `FakeRuntime.set_rule_enabled` to accept `(camera_id, rule_id, enabled)`. Add tests asserting:

1. Home renders both Chinese rule names and two `data-rule-checkbox` inputs per camera with stable `data-rule-id` values.
2. A valid object-rule command forwards all three values and JSON returns the complete camera rule array.
3. Missing or unknown `rule_id` returns 400 without calling runtime.
4. Unknown camera still returns 404 and all commands retain CSRF/origin protection.
5. Settings renders `cat / 猫`, `dog / 狗`, and the fixed limitation copy, with no object configuration form.
6. `/healthz` returns separate person/object detector states and never includes model paths, images, boxes, or credentials.

- [ ] **Step 2: Run Web tests and verify RED**

Run: `pytest tests/app/test_web.py -v`

Expected: tests fail because the command omits `rule_id` and views expose a single boolean.

- [ ] **Step 3: Generalize the command and JSON response**

Add `rule_id: str = Form(default="")`, reject values outside `BUILTIN_RULE_IDS` with `HTTPException(400, "unknown_rule")`, and call:

```python
await runtime.set_rule_enabled(camera_id, rule_id, enabled == "true")
```

Return `camera.health_dict()` with its full rules array plus existing counts. Pass the bilingual supported-category tuple to settings template context.

- [ ] **Step 4: Build the server-rendered controls**

Render a `.camera-rules` grid containing one unframed `.rule-control` per `RuleView`, with label, status, latest result, checkbox, hidden camera ID, hidden rule ID, and existing CSRF token. Keep speaker selection outside the rules grid. Update JavaScript to submit `rule_id`, locate the matching control by both camera and rule IDs, and update all returned rule states without page reload. Keep fixed switch dimensions and restore the prior checked state on request failure.

On settings, render a compact two-column semantic table for every supported English/Chinese label. Do not add search, filtering, checkboxes, editable fields, cards, or explanatory feature marketing.

- [ ] **Step 5: Run Web tests and inspect responsive output**

Run: `pytest tests/app/test_web.py -v`

Expected: all tests pass.

Run: `ruff check src/daihougou/web.py tests/app/test_web.py`

Expected: exit 0.

Prepare and start an exact local UI fixture without enabling either model:

```bash
mkdir -p /tmp/daihougou-ui-fixture
python -c 'from pathlib import Path; from daihougou.storage import Storage; s=Storage(Path("/tmp/daihougou-ui-fixture/daihougou.db")); s.initialize(); s.sync_cameras(("picture-book", "play-room"), "living_room")'
MI_USER=fixture \
MI_PASS=fixture \
MI_SPEAKERS_JSON='[{"id":"living_room","name":"客厅音箱","did":"fixture-did"}]' \
DATA_DIR=/tmp/daihougou-ui-fixture \
GO2RTC_API_URL=http://127.0.0.1:9 \
WEB_HOST=127.0.0.1 \
WEB_PORT=8081 \
daihougou
```

Use Playwright against `http://127.0.0.1:8081/` and `/settings` at desktop `1440x900` and mobile `390x844`, saving screenshots under `/tmp/daihougou-ui-screenshots`. Verify no text overlap, both switches remain fixed size, speaker controls remain usable, and the category table wraps without horizontal page overflow. Stop the server and remove `/tmp/daihougou-ui-fixture` and `/tmp/daihougou-ui-screenshots` after review.

- [ ] **Step 6: Commit**

```bash
git add src/daihougou/web.py src/daihougou/templates/index.html src/daihougou/templates/settings.html src/daihougou/static/app.js src/daihougou/static/app.css tests/app/test_web.py
git commit -m "feat: manage object announcement rules"
```

### Task 8: Production Wiring, Model Supply Chain, Documentation, and Full Verification

**Files:**
- Modify: `src/daihougou/settings.py`
- Modify: `src/daihougou/main.py`
- Modify: `docker/mvp.Dockerfile`
- Modify: `.env.mvp.example`
- Modify: `README.md`
- Create: `third_party/nanodet/LICENSE`
- Modify: `tests/app/test_settings.py`
- Modify: `tests/app/test_main.py`
- Modify: `tests/app/test_mvp_dockerfile.py`
- Modify: `tests/app/test_mvp_container.py`
- Modify: `tests/app/test_generated_video_pipeline.py`

**Interfaces:**
- Consumes: passing PoC model identity and all production interfaces from Tasks 1-7.
- Produces: fully wired app image, fixed model verification, license attribution, deployment guidance, and end-to-end regression evidence.

- [ ] **Step 1: Write failing settings and Docker supply-chain tests**

Add assertions that `Settings.from_mapping` defaults `object_model` to `/opt/daihougou/models/object_detection_nanodet_2022nov.onnx`, accepts only an `OBJECT_MODEL` path override, and exposes no object threshold variables. Assert Dockerfile contains the fixed NanoDet media URL, exact SHA384, separate checksum verification for both models, and copies `third_party/nanodet/LICENSE` into the image. Assert the license begins with `Apache License` and contains `Version 2.0, January 2004`.

- [ ] **Step 2: Run tests and verify RED**

Run: `pytest tests/app/test_settings.py tests/app/test_mvp_dockerfile.py tests/app/test_mvp_container.py -v`

Expected: tests fail because `object_model`, NanoDet download, and license file do not exist.

- [ ] **Step 3: Wire production factories and frame sizing**

Add `object_model: Path` to `Settings`. Construct `DetectionScheduler` with:

```python
person_factory=lambda: PersonDetector(resolved.model, resolved.person_threshold)
object_factory=lambda: ObjectDetector(resolved.object_model)
```

Change the runtime frame-source factory signature to `(url: str, size: int)` and construct `FfmpegFrameSource(url, resolved.detection_fps, size=size)`. Preserve one Uvicorn worker.

- [ ] **Step 4: Pin the model and license**

Add NanoDet URL and SHA384 build arguments, download to `/opt/daihougou/models/object_detection_nanodet_2022nov.onnx`, and verify it with `sha384sum --check` exactly like the person model. Copy the Apache-2.0 text from the fixed OpenCV Zoo directory into `third_party/nanodet/LICENSE`; do not alter or summarize the license text.

- [ ] **Step 5: Extend generated-video integration coverage**

Use generated geometric frames, not retained household imagery. Test a static page produces only the initial object inference, a visibly changed page produces another object action, welcome and object jobs remain serialized, and two cameras keep independent scene references and pending announcement keys. Use fake detectors for semantic pipeline coverage; the passing PoC report remains the evidence for real NanoDet quality.

- [ ] **Step 6: Update deployment documentation**

Document `OBJECT_MODEL`, model download requirements, two rule switches, fixed-camera framing, first-frame object announcement, 1 FPS scene gate, no stabilization wait, three-label English output, supported/unsupported categories, default-off upgrade, 3-second queue expiry, model-specific degradation, temporary PoC corpus deletion, and CPU warning. Remove the previous “animal recognition is a non-goal” statement from superseded scope text or mark it explicitly superseded by this design.

- [ ] **Step 7: Run focused production tests**

Run: `pytest tests/app/test_settings.py tests/app/test_main.py tests/app/test_mvp_dockerfile.py tests/app/test_mvp_container.py tests/app/test_generated_video_pipeline.py -v`

Expected: all tests pass.

- [ ] **Step 8: Run the complete verification suite**

Run: `pytest -v`

Expected: all tests pass with zero failures.

Run: `ruff check .`

Expected: exit 0 with no diagnostics.

Run: `git diff --check`

Expected: exit 0 with no whitespace errors.

Build: `docker compose -f compose.poc.yaml build app`

Expected: image build exits 0 and both model checksum checks report success.

Container tests: `docker compose -f compose.poc.yaml run --rm --no-deps app pytest -v`

Expected: all container tests pass offline after the image is built.

- [ ] **Step 9: Commit**

```bash
git add src/daihougou/settings.py src/daihougou/main.py docker/mvp.Dockerfile .env.mvp.example README.md third_party/nanodet/LICENSE tests/app/test_settings.py tests/app/test_main.py tests/app/test_mvp_dockerfile.py tests/app/test_mvp_container.py tests/app/test_generated_video_pipeline.py
git commit -m "feat: ship scene-change object announcements"
```
