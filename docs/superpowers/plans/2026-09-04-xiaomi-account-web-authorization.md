# 小米音箱网页授权与数据库绑定 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 移除服务器端小米账号环境变量和脚本登录，新增设置页 AJAX 授权、MiNA 音箱发现/绑定、SQLite TokenStore，以及基于 MiNA `text_to_speech` 的动态播报。

**Architecture:** SQLite 保存单账号 Token 与用户确认的音箱绑定；内存中的 `XiaomiAccountManager` 管理授权尝试、OTP Future、MiNA 发现快照和账号替换。`SpeakerManager` 根据持久绑定动态创建每设备串行 worker，正式播报只调用 MiNA TTS；Runtime 通过绑定可用性决定摄像头规则是否实际运行。

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLite (`sqlite3`), `miservice==3.0.1`, vanilla JavaScript, pytest/httpx。

**Spec:** `docs/superpowers/specs/2026-09-04-xiaomi-account-web-authorization-design.md`

## Global Constraints

- 账号密码不从 `.env` 读取、不写入数据库、不写入日志或事件；`.mi.token` 文件不再读取或写入。
- MiNA 发现的全部设备都可以被用户选择，不按型号静态过滤。
- 正式播报统一调用 `MiNAService.text_to_speech(deviceID, text)`，不再发送固定 MIoT `siid=5, aiid=3`。
- `device_list()` 只在授权成功后自动调用一次和用户点击“刷新设备”时调用，不在页面打开或每次播报前调用。
- 设备原始 `deviceID`、`miotDID`、Token 不出现在 HTML、JSON API 响应、事件详情或普通日志。
- 绑定保存为全量提交，原有绑定默认勾选；解除仍被摄像头引用的绑定需要一次性确认。
- Token 过期时拒绝新动作并丢弃尚未开始的队列动作，正在执行的动作不重试；重新授权成功后恢复运行。
- 新摄像头无绑定且所有规则默认关闭；没有可用绑定音箱时拒绝开启规则。
- 继续使用当前局域网 HTTP、Origin 校验和 HttpOnly SameSite CSRF cookie，不新增应用登录。
- 数据库父目录权限设为 `0700`、数据库文件权限设为 `0600`（权限可用时）。
- 不导入旧 `MI_USER`、`MI_PASS`、`MI_SPEAKERS_JSON`、`MI_DID` 或 `.mi.token`；已有摄像头和规则数据保留。

## 文件结构

- Create: `src/guduck/xiaomi.py` — MiService 适配器、数据库 TokenStore、授权尝试状态机、发现/绑定门面。
- Modify: `src/guduck/storage.py` — schema v4、账号/绑定表、摄像头可空绑定、设备发现和全量保存事务。
- Modify: `src/guduck/speaker.py` — 删除 subprocess/固定 MIoT speaker，提供 MiNA TTS speaker 和错误分类。
- Modify: `src/guduck/speaker_worker.py` — 支持空启动、动态替换绑定 worker、不可用设备和认证全局门控。
- Modify: `src/guduck/runtime.py` — 从数据库绑定生成视图，规则启用校验，以及绑定状态驱动的 camera reconcile。
- Modify: `src/guduck/main.py`、`src/guduck/settings.py` — 无需小米 env 即可启动，组装新的 account manager。
- Modify: `src/guduck/web.py` — 新增 Xiaomi AJAX API 和设置页上下文。
- Modify: `src/guduck/templates/settings.html`、`src/guduck/static/app.js`、`src/guduck/static/app.css` — 授权、OTP、设备选择、测试与绑定保存，不刷新页面。
- Modify: `.env.example`、`compose.yaml`、`README.md`、`docs/poc-runbook.md` — 删除旧账号/token 配置和挂载说明，记录网页授权流程。
- Test: `tests/app/test_storage.py`、`tests/app/test_xiaomi.py`（new）、`tests/app/test_speaker_worker.py`、`tests/app/test_runtime.py`、`tests/app/test_web.py`、`tests/app/test_main.py`、`tests/app/test_settings.py`、`tests/app/test_compose.py`、`tests/speakers/test_direct.py`（改名或删除旧 direct 断言）。

---

### Task 1: SQLite schema、TokenStore 和绑定事务

**Files:**
- Modify: `src/guduck/storage.py`
- Test: `tests/app/test_storage.py`

**Interfaces:**
- Produces `XiaomiAccount(id: int, username: str, token_json: str, updated_at: str)` and `SpeakerBinding(binding_id: str, device_id: str, display_name: str, mina_name: str, hardware: str, miot_did: str | None, last_seen_at: str | None, bound: bool, available: bool, test_status: str, updated_at: str)` dataclasses.
- Produces `Storage.get_xiaomi_account() -> XiaomiAccount | None`, `Storage.save_xiaomi_account(username: str, token_json: str) -> None`, `Storage.clear_xiaomi_account() -> None`.
- Produces `Storage.list_speaker_bindings() -> list[SpeakerBinding]`, `Storage.upsert_discovered_speakers(devices: Sequence[DiscoveredSpeaker]) -> list[SpeakerBinding]`, and `Storage.save_speaker_bindings(selected_binding_ids: Sequence[str], confirmation_id: str | None = None) -> BindingSaveResult`.
- Defines `DiscoveredSpeaker(device_id: str, mina_name: str, hardware: str, miot_did: str | None)`, `BindingSaveResult(saved: bool, confirmation_id: str | None, affected_camera_ids: tuple[str, ...])`, and `TestResult(success: bool)` in the storage/domain layer so the adapter can pass typed records without a circular import.
- Changes `Storage.sync_cameras(stream_ids, default_speaker_id=None)` so new cameras store `NULL`; `camera_speaker_id(camera_id) -> str | None` and `set_camera_speaker(camera_id, speaker_id: str | None)` accept an unbound camera.

- [ ] **Step 1: Add failing schema and migration tests.** Extend `tests/app/test_storage.py` with assertions that a fresh database is schema v4 and contains exactly `xiaomi_account`/`speaker_bindings`, that a v3 database migrates while preserving cameras/rules/events, and that old camera speaker strings remain unresolved legacy values rather than becoming device IDs.

- [ ] **Step 2: Run the focused tests and verify the expected failures.**

Run: `pytest tests/app/test_storage.py -k 'xiaomi or schema or migrate' -v`

Expected: FAIL because `SCHEMA_VERSION` is still 3 and the new tables/methods do not exist.

- [ ] **Step 3: Implement schema v4 and strict migration.** Set `SCHEMA_VERSION = 4`; add exact table definitions for `xiaomi_account` (`id CHECK (id = 1)`) and `speaker_bindings` (unique `device_id`, boolean `bound`/`available` checks). Rebuild `cameras` inside one transaction to make `speaker_id TEXT` nullable while preserving its old values, add missing region columns when migrating from v3, and reject any unexpected table/definition before mutating. Fresh databases create all tables directly at v4. Do not add a foreign key from `cameras.speaker_id` so legacy orphan values remain visible as unavailable.

- [ ] **Step 4: Implement atomic TokenStore and binding CRUD.** Add an async-compatible `DatabaseTokenStore` with `async load() -> dict[str, object] | None` and `async save(token: Mapping[str, object]) -> None`; serialize only the MiService token JSON through `Storage`, replacing the row in a transaction. `upsert_discovered_speakers` must match by `device_id`, preserve `binding_id`, `display_name`, `bound`, and camera references, update Mina metadata/`last_seen_at`/`available`, and mark missing bound rows unavailable without deleting them. `save_speaker_bindings` must validate IDs from the current snapshot or existing rows, calculate camera references, return a stable confirmation token plus affected camera IDs on the first unsafe unbind, and on confirmed retry atomically set `bound` values and affected cameras to unavailable. Never expose sensitive columns through a public dataclass or event helper.

- [ ] **Step 5: Run storage tests and commit the storage slice.**

Run: `pytest tests/app/test_storage.py -v`

Expected: PASS, including v2/v3 compatibility tests updated to expect v4 and preservation of legacy camera speaker values.

Commit: `git add src/guduck/storage.py tests/app/test_storage.py && git commit -m "feat: persist Xiaomi accounts and speaker bindings"`

### Task 2: MiNA adapter、授权状态机与发现快照

**Files:**
- Create: `src/guduck/xiaomi.py`
- Test: `tests/app/test_xiaomi.py`

**Interfaces:**
- Adapts `DiscoveredSpeaker(device_id: str, mina_name: str, hardware: str, miot_did: str | None)` from MiNA records; this internal record never crosses the Web boundary.
- Produces `XiaomiStatus(state: str, attempt_id: str | None, otp_method: str | None, expires_at: float | None, error_code: str | None, devices: tuple[PublicDevice, ...], bindings: tuple[PublicBinding, ...])`; `PublicDevice`/`PublicBinding` omit raw IDs.
- Produces `XiaomiAccountManager.start_auth(username: str, password: str) -> str`, `status(attempt_id: str | None) -> XiaomiStatus`, `submit_otp(attempt_id: str, code: str) -> None`, `cancel_auth(attempt_id: str) -> None`, `refresh_devices() -> XiaomiStatus`, `save_bindings(selected_ids: Sequence[str], confirmation_id: str | None = None) -> BindingSaveResult`, and `test_binding(binding_id: str) -> TestResult`.
- Produces `XiaomiAccountManager.runtime_speakers() -> Mapping[str, Speaker]`, `has_available_binding(binding_id: str | None) -> bool`, and `display_bindings() -> tuple[PublicBinding, ...]` for Runtime/Web.

- [ ] **Step 1: Write failing adapter/state tests.** In `tests/app/test_xiaomi.py`, provide fake `MiAccount`/`MiNAService` factories and test: one active ten-minute attempt; OTP callback records `Phone`/`Email` and waits for `submit_otp`; cancellation/expiry releases password and OTP Future; successful auth calls `device_list()` once; refresh failure retains the prior snapshot; all device IDs/token/password are absent from status and errors; token-store load/save round-trips through SQLite.

- [ ] **Step 2: Run the focused tests to confirm they fail.**

Run: `pytest tests/app/test_xiaomi.py -v`

Expected: FAIL with missing module/classes.

- [ ] **Step 3: Implement MiService boundaries.** Wrap `miservice.MiAccount` and `MiNAService` behind injectable factories so tests never contact Xiaomi. Construct account sessions with the database TokenStore and no persistent password; keep password, OTP, temporary token/session, Future, and discovery snapshot only in the attempt object. Normalize `device_list()` records into `DiscoveredDevice` and do not filter by model/capability. Map MiService auth exceptions/codes to `auth_required`/`failed` category codes and all other errors to stable Chinese-safe classifications.

- [ ] **Step 4: Implement the state machine and atomic replacement.** Enforce `unconfigured -> authenticating -> otp_required -> fetching_devices -> devices_ready -> ready`; expire after 600 seconds; a new `start_auth` cancels the previous attempt. On success, keep the old account/worker untouched until the new TokenStore row and selected bindings commit in one storage transaction, then swap the active MiNA client and return `ready` only when at least one bound device exists. `refresh_devices()` updates bindings through Task 1 and preserves prior snapshot on failure. `test_binding()` calls MiNA `text_to_speech` with a short fixed test phrase, updates `test_status`, and returns only `success`/`failure`.

- [ ] **Step 5: Run adapter tests and commit.**

Run: `pytest tests/app/test_xiaomi.py -v`

Expected: PASS.

Commit: `git add src/guduck/xiaomi.py tests/app/test_xiaomi.py && git commit -m "feat: add MiNA account authorization flow"`

### Task 3: MiNA speaker and dynamic worker lifecycle

**Files:**
- Modify: `src/guduck/speaker.py`
- Modify: `src/guduck/speaker_worker.py`
- Modify: `src/guduck/xiaomi.py`
- Test: `tests/app/test_xiaomi.py`, `tests/app/test_speaker_worker.py`

**Interfaces:**
- `Speaker.speak(text: str) -> SpeakResult` remains synchronous to preserve the existing `asyncio.to_thread` worker boundary.
- `MiNASpeaker(device_id: str, mina_service_factory: Callable[[], object])` invokes `text_to_speech(device_id, text)` inside the worker thread and maps timeout, ordinary failure, and authentication failure to `SpeakResult` without raw exception text.
- `SpeakerManager.replace_speakers(speakers: Mapping[str, Speaker], available_ids: Collection[str]) -> Awaitable[None]` stops removed workers, starts added workers, and updates statuses without accepting actions for unavailable IDs.

- [ ] **Step 1: Update failing speaker tests.** Replace fixed MIoT payload assertions in `tests/speakers/test_direct.py` with MiNA TTS call assertions, and add worker tests for empty startup, dynamic replacement, unavailable-device rejection, authentication failure dropping pending work, and no retry of an in-flight call.

- [ ] **Step 2: Run the focused speaker tests and verify failure.**

Run: `pytest tests/speakers tests/app/test_speaker_worker.py -v`

Expected: FAIL because `DirectSpeaker` still shells out and `SpeakerManager` has no replacement API.

- [ ] **Step 3: Implement `MiNASpeaker` and remove the old subprocess path.** Delete `DirectSpeaker`, `REAUTH_MARKERS`, and environment construction from `src/guduck/speaker.py`. Use a narrow injected MiNA service protocol; catch `TimeoutError`/network exceptions as `timeout`/`speaker_error`, recognize MiService auth code/exception as `reauth_required`, and never copy exception strings into `SpeakResult.error`.

- [ ] **Step 4: Implement worker replacement and availability gates.** Refactor queue/task maps so `SpeakerManager({})` is valid. Add a lock around replacement, drain/stop removed queues, create workers for new bindings, reject submissions when `auth_status == "reauth_required"`, binding is unavailable, or ID is unknown, and preserve existing event kinds for skipped/failed actions. Add `set_auth_status`/`set_available_ids` hooks used by `XiaomiAccountManager`.

- [ ] **Step 5: Run speaker tests and commit.**

Run: `pytest tests/speakers tests/app/test_speaker_worker.py -v`

Expected: PASS.

Commit: `git add src/guduck/speaker.py src/guduck/speaker_worker.py tests/speakers tests/app/test_speaker_worker.py && git commit -m "feat: route speaker playback through MiNA TTS"`

### Task 4: Runtime、摄像头绑定和规则暂停/恢复

**Files:**
- Modify: `src/guduck/runtime.py`
- Modify: `src/guduck/storage.py`
- Modify: `src/guduck/speaker_worker.py`
- Test: `tests/app/test_runtime.py`, `tests/app/test_storage.py`

**Interfaces:**
- `Runtime` no longer requires `SpeakerConfig` or a non-empty static speaker tuple; it receives an `XiaomiAccountManager`/managed speaker manager and derives `SpeakerOption`/availability from persisted bindings.
- `ManagedSpeakerManager` gains `available(speaker_id: str | None) -> bool` and `replace_speakers(...)`.
- `CameraView.speaker_id` becomes `str | None`; an unresolved legacy ID is rendered unavailable with a rebind-needed message.

- [ ] **Step 1: Add failing runtime tests.** Cover startup with no Xiaomi account, new cameras with `speaker_id is None`, rejecting rule enable when the camera has no available binding, preserving enabled intent while its binding disappears, stopping camera runtime while unavailable, and restoring runtime after a successful device refresh plus rebind.

- [ ] **Step 2: Run focused runtime tests to verify failure.**

Run: `pytest tests/app/test_runtime.py -k 'speaker or rule or camera' -v`

Expected: FAIL because Runtime currently requires static speakers and always reconciles enabled cameras.

- [ ] **Step 3: Make Runtime binding-aware.** Remove `_speaker_ids`/static speaker catalog, query binding views from the manager, change `sync_cameras` default to `None`, and include `speaker_available` in `_reconcile`'s desired camera set. Keep enabled rule rows unchanged when unavailable so they auto-resume once the same binding is available again. Return `speaker_auth`, display names, and per-device status without raw IDs.

- [ ] **Step 4: Add account-manager/runtime synchronization.** On auth success, refresh, save, or auth failure, update worker maps and call Runtime reconciliation through an explicit callback or `refresh_speaker_state()` method. Ensure old account/workers stay active until the replacement transaction succeeds; on failure, leave the old snapshot and worker set unchanged.

- [ ] **Step 5: Run runtime tests and commit.**

Run: `pytest tests/app/test_runtime.py tests/app/test_storage.py -v`

Expected: PASS.

Commit: `git add src/guduck/runtime.py src/guduck/storage.py src/guduck/speaker_worker.py tests/app/test_runtime.py tests/app/test_storage.py && git commit -m "feat: pause cameras without available speakers"`

### Task 5: Settings AJAX API and no-refresh UI

**Files:**
- Modify: `src/guduck/web.py`
- Modify: `src/guduck/templates/settings.html`
- Modify: `src/guduck/static/app.js`
- Modify: `src/guduck/static/app.css`
- Test: `tests/app/test_web.py`

**Interfaces:**
- Extend `ManagedRuntime` with `xiaomi_status()`, `start_xiaomi_auth(username, password)`, `submit_xiaomi_otp(attempt_id, code)`, `cancel_xiaomi_auth(attempt_id)`, `refresh_xiaomi_devices()`, `save_xiaomi_bindings(selected_ids, confirmation_id)`, and `test_xiaomi_binding(binding_id)`.
- Add `POST /api/xiaomi/auth/start`, `GET /api/xiaomi/auth/status`, `POST /api/xiaomi/auth/otp`, `POST /api/xiaomi/auth/cancel`, `POST /api/xiaomi/devices/refresh`, `POST /api/xiaomi/bindings/save`, and `POST /api/xiaomi/bindings/{binding_id}/test`.
- JSON responses contain state, stable category codes, display names, binding IDs, checked/available/test status, affected camera names, and no `deviceID`/`miotDID`/Token/password.

- [ ] **Step 1: Add failing Web tests.** Extend the fake runtime and `tests/app/test_web.py` with CSRF/Origin checks for every write route; start/status/OTP/cancel transitions; 409 `unbind_confirmation_required` containing affected camera names and `confirmation_id`; manual test returning exactly `测试成功` or `测试失败`; and assertions that sensitive IDs/passwords never occur in HTML or JSON.

- [ ] **Step 2: Run the focused Web tests to confirm failure.**

Run: `pytest tests/app/test_web.py -k 'xiaomi or binding or auth' -v`

Expected: FAIL because the routes, template context, and fake runtime methods do not exist.

- [ ] **Step 3: Implement API routes with existing CSRF helpers.** Parse form/JSON bodies with explicit length limits, require the active `attempt_id` for OTP/status writes, translate manager exceptions into stable `400`/`409`/`503` codes, and never return MiService exception text. Return `Cache-Control: no-store` on all status/device responses.

- [ ] **Step 4: Implement the settings section and AJAX polling.** Add account state copy, username/password fields only for the active request, OTP method/code form, cancel/retry buttons, device list showing Mina name/hardware/custom display name, default checked prior bindings, refresh, test, and two-step unbind confirmation. In `app.js`, use `fetch` plus a one-second timer while an attempt is active; update only the Xiaomi section and stop the timer on terminal states. Do not use `location.reload()` or navigation.

- [ ] **Step 5: Run Web tests and commit.**

Run: `pytest tests/app/test_web.py -v`

Expected: PASS.

Commit: `git add src/guduck/web.py src/guduck/templates/settings.html src/guduck/static/app.js src/guduck/static/app.css tests/app/test_web.py && git commit -m "feat: add AJAX Xiaomi authorization settings"`

### Task 6: Production assembly、环境清理和文档

**Files:**
- Modify: `src/guduck/settings.py`
- Modify: `src/guduck/main.py`
- Modify: `.env.example`
- Modify: `compose.yaml`
- Modify: `README.md`
- Modify: `docs/poc-runbook.md`
- Test: `tests/app/test_settings.py`, `tests/app/test_main.py`, `tests/app/test_compose.py`, `tests/test_docker_context.py`

**Interfaces:**
- `Settings.from_mapping` accepts all existing non-Mi parameters and rejects `MI_USER`, `MI_PASS`, `MI_SPEAKERS_JSON`, and `MI_DID` with an explicit unsupported-key error; no `Settings.mi_user`, `mi_pass`, or static speaker catalog remains.
- `create_production_app` creates `Storage`, `XiaomiAccountManager`, and an empty/dynamic `SpeakerManager`; the app must start with no Xiaomi environment variables.

- [ ] **Step 1: Add failing production/config tests.** Assert that a minimal environment starts settings parsing without Xiaomi values, any old MI key is rejected, `create_production_app` does not construct `DirectSpeaker`, compose has no MI env or `.mi.token` mount, and the example env/README describe page authorization.

- [ ] **Step 2: Run focused production tests and verify failure.**

Run: `pytest tests/app/test_settings.py tests/app/test_main.py tests/app/test_compose.py tests/test_docker_context.py -v`

Expected: FAIL because settings still require MI values and compose/docs still contain the old setup.

- [ ] **Step 3: Remove legacy configuration and assemble the new manager.** Delete `SpeakerConfig` parsing and old MI fields; preserve object detector, camera, and web settings. Pass the database path to `XiaomiAccountManager`, inject its MiService factories, and let Runtime start with zero speaker workers. Keep `workers=1` because the in-memory authorization attempt is process-local.

- [ ] **Step 4: Remove old deployment surfaces and document the safe workflow.** Delete MI variables from `.env.example`, remove `/var/lib/guduck/mi`/HOME token mounts and probe profile references from `compose.yaml`, update README and `docs/poc-runbook.md` to enter `/settings`, explain OTP and manual tests, state that passwords are not persisted and Tokens live in SQLite, and retain the trusted-LAN HTTP warning. Do not add migration/import commands.

- [ ] **Step 5: Run production tests and commit.**

Run: `pytest tests/app/test_settings.py tests/app/test_main.py tests/app/test_compose.py tests/test_docker_context.py -v`

Expected: PASS.

Commit: `git add src/guduck/settings.py src/guduck/main.py .env.example compose.yaml README.md docs/runbook.md tests/app/test_settings.py tests/app/test_main.py tests/app/test_compose.py tests/test_docker_context.py && git commit -m "feat: remove Xiaomi credentials from environment"`

### Task 7: 全量验证、敏感信息审计和验收回归

**Files:**
- Modify: any test files required by the preceding implementation slices; no unrelated production files.

- [ ] **Step 1: Run the complete Python and JavaScript test suites.**

Run: `pytest -q && node --test tests/js/region-editor.test.js`

Expected: exit code 0 with zero failed tests.

- [ ] **Step 2: Run static checks.**

Run: `ruff check src tests && git diff --check`

Expected: no Ruff findings and no whitespace errors.

- [ ] **Step 3: Audit for retired credential surfaces and fixed MIoT calls.**

Run: `rg -n 'MI_USER|MI_PASS|MI_SPEAKERS_JSON|MI_DID|\.mi\.token|siid.?5|aiid.?3|miservice.*action|DirectSpeaker' src tests .env.example compose.yaml README.md docs || true`

Expected: no runtime/config/deployment references; historical research/spec text may mention the retired behavior only as explanatory evidence.

- [ ] **Step 4: Run manual acceptance against a fresh database.** Start without all `MI_*` values, authorize with a real Mi account, enter the actual OTP in the settings page, verify the full MiNA device list and multi-select bindings, manually test each device, bind different cameras, exercise two-step unbind, refresh disappearance/reappearance, and confirm token expiry blocks new speech then reauthorization restores it. Capture only “测试成功/测试失败” results; do not log credentials or raw IDs.

- [ ] **Step 5: Review the final diff and commit any test-only corrections.**

Run: `git status --short && git diff --stat HEAD~6..HEAD`

Expected: only the Xiaomi authorization/binding implementation, tests, deployment cleanup, and the approved design/research documents are present.
