# 小米音箱网页授权与数据库绑定设计

## 背景

当前应用从环境变量读取 `MI_USER`、`MI_PASS` 和 `MI_SPEAKERS_JSON`，并让
MiService 把认证状态写入宿主机 `.mi.token`。这要求管理员在目标服务器上运行交互式
脚本，且当前播报路径固定使用 MIoT `siid=5, aiid=3`，无法覆盖不同型号的音箱。

本次改动把小米账号授权、音箱发现和绑定配置迁移到管理页面。密码只在一次授权尝试的
内存中存在；账号标识、MiService Token 和用户选择的音箱绑定持久化到 SQLite。正式播报
统一改用 MiService 的 MiNA `text_to_speech(deviceID, text)`，不再使用固定 MIoT 动作，也
不维护型号白名单。

跨型号兼容性调查见
[`docs/research/xiaomi-speaker-miservice-compatibility.md`](../../research/xiaomi-speaker-miservice-compatibility.md)。
该调查表明 MIoT `play-text` 的服务/动作 IID 随型号变化，而 MiNA 提供不依赖 IID 的统一
TTS 接口；MiNA 返回设备仍需通过实际测试确认。

## 目标

1. 管理页可以在不刷新页面的情况下完成账号授权、短信/邮箱验证码输入和取消/重试。
2. 账号密码不从 `.env` 读取、不写入数据库、不写入日志或事件；`.mi.token` 文件不再读取或写入。
3. 单个小米账号支持多个 MiNA 音箱；账号、Token、绑定设备和摄像头配对保存到 SQLite。
4. MiNA 发现的全部设备都可以被用户选择，不按型号静态过滤。
5. 发现快照、用户绑定和当前运行设备分层，避免新设备未经确认自动加入运行配置。
6. Token 过期时停止新播报并提示重新授权；重新授权成功后可在页面恢复运行。
7. 旧的 `MI_USER`、`MI_PASS`、`MI_SPEAKERS_JSON`、`MI_DID` 和 `.mi.token` 直接废弃，
   不做配置导入或兼容读取。

## 非目标

- 不引入管理页登录、家庭成员权限或新的 HTTPS 终止层；继续要求只在可信局域网使用。
- 不保存完整 MiNA 原始设备响应、设备 Token、密码、Cookie、码流 URL 或音频文件。
- 不在每次页面打开或每次播报前自动调用 `device_list()`。
- 不保证所有型号实际都能发声；接口测试成功只表示 MiNA 请求被接受。
- 不保留固定 MIoT `5/3` 后备路径。本次不实现动态 MIoT spec 解析。

## 总体架构

应用仍是单个 FastAPI worker 和一个 SQLite 文件。新增 `MiNAAccountManager`，由它统一
管理当前账号、数据库 TokenStore、MiNAService、授权尝试和可动态替换的音箱 worker。
Web 层只调用管理器的小接口并渲染状态，不直接操作 MiService、队列或数据库连接。

```text
设置页 AJAX
    |
    v
MiNAAccountManager ---- SQLite
    |       |             |- xiaomi_account
    |       |             |- speaker_bindings
    |       |             |- cameras.speaker_id (binding_id/null)
    |       |
    |       +-- 临时授权会话（密码/验证码/Token/发现快照，仅内存）
    |
    +-- MiNAService.device_list()        -> 发现候选
    +-- MiNAService.text_to_speech()     -> 正式播报/手动测试
    +-- speaker worker queues            -> 按设备串行
```

### 运行时状态

账号配置和运行有效性是两个维度。账号状态使用有限状态：

```text
unconfigured
  -> authenticating
  -> otp_required
  -> fetching_devices
  -> devices_ready
  -> ready
  -> auth_required
  -> failed / cancelled / expired
```

`devices_ready` 表示临时授权成功且已有发现快照；至少保存一个绑定设备后才进入 `ready`。
没有绑定设备时页面仍可停留在设备选择状态，但不启动播报 worker，且摄像头规则不能开启。

## 授权流程

1. 用户输入账号和密码，前端 `POST /api/xiaomi/auth/start`。
2. 管理器取消当前尝试（如果存在），创建随机 `attempt_id` 和 10 分钟过期的内存会话，
   启动后台授权任务。MiAccount 使用自定义异步 TokenStore，不能访问文件系统 Token。
3. MiService 的 OTP callback 不读取终端，而是把方法（短信或邮箱）写入会话，并等待一个
   仅存在内存的 Future。会话状态变为 `otp_required`。
4. 页面每秒 AJAX 请求 `GET /api/xiaomi/auth/status?attempt_id=...`，局部更新提示和表单；
   不导航、不刷新整页。验证码通过 `POST /api/xiaomi/auth/otp` 提交，取消通过
   `POST /api/xiaomi/auth/cancel` 提交。
5. 授权成功后立即使用同一临时 MiNAService 调用 `device_list()`，状态变为 `devices_ready`。
   候选设备只保存在内存，响应只包含显示名称、硬件型号、是否已绑定和内部绑定 ID，不返回
   `deviceID`、`miotDID`、Token 或原始响应。
6. 超时、取消、重新开始或失败会释放密码、验证码、Future、临时 MiAccount/HTTP session、
   Token 和发现快照。旧账号运行时不受新尝试影响。

账号替换必须先完成新账号授权和设备选择，再通过一次数据库事务提交；事务成功前旧账号、
旧绑定和旧 worker 继续运行。

## 发现、绑定和设备运行模型

### 发现快照

`device_list()` 只在以下时机调用：授权成功后自动调用一次，以及设置页的“刷新设备”按钮。
成功响应替换当前内存快照，并按 `deviceID` 更新已绑定设备的供应商名称、硬件、`miotDID`、
`last_seen_at` 和 `available`。刷新失败保留上次快照和旧可用性，不把设备判定为消失。

### 持久绑定

用户在设备列表中提交绑定集合时执行全量保存，界面默认勾选此前已绑定设备。新增设备必须
来自最近一次成功发现快照；此前绑定但暂时未发现的设备可以继续保留。取消选择才会解除绑定。

保存解除绑定设备时，如果仍有摄像头引用，服务端先以 `409 unbind_confirmation_required`
返回受影响摄像头和一次性 `confirmation_id`，不修改数据库；前端展示摄像头名称并在用户
第二次确认后携带 `confirmation_id` 重试同一保存请求。确认后保存成功，设备记录保留但标记
`bound=0, available=0`，相关摄像头进入“音箱不可用”状态。没有摄像头引用的解除绑定记录
可以清理。设备重新出现在后续成功发现中时恢复 `available`；如果之前是用户明确取消绑定，
仍保持 `bound=0`，只有重新勾选并保存后才恢复 `bound=1`。

### 正式运行

已绑定设备以持久化 `deviceID` 作为 MiNA 调用键；每个设备一个有界串行队列。正式播报不
触发设备发现。设备普通网络错误只影响该次调用；账号认证错误转为全局 `auth_required`，
拒绝新动作并丢弃尚未开始的队列动作，正在执行的动作不重试。

## 数据库

数据库 schema 版本递增。新表和字段在一次 schema 迁移中创建；不兼容 schema 仍拒绝启动，
不自动删除数据库。

### `xiaomi_account`

```text
id          INTEGER PRIMARY KEY CHECK (id = 1)
username    TEXT NOT NULL
token_json  TEXT NOT NULL
updated_at  TEXT NOT NULL
```

Token JSON 由 MiService 自定义 TokenStore 原子读写。应用初始化和迁移时将数据库父目录
设为 `0700`、数据库文件设为 `0600`（在权限可用的环境中）。

### `speaker_bindings`

```text
binding_id      TEXT PRIMARY KEY
device_id       TEXT NOT NULL UNIQUE
display_name    TEXT NOT NULL
mina_name       TEXT NOT NULL
hardware        TEXT NOT NULL
miot_did        TEXT
last_seen_at    TEXT
bound           INTEGER NOT NULL CHECK (bound IN (0, 1))
available       INTEGER NOT NULL CHECK (available IN (0, 1))
test_status     TEXT NOT NULL
updated_at      TEXT NOT NULL
```

`device_id`、`miot_did` 和 Token 永远不出现在 HTML、JSON API 响应、事件详情或普通日志中。
`display_name` 是用户可编辑的 GuDuck 名称；同一个 `device_id` 重新发现时保留该名称和
摄像头绑定，只更新供应商元数据。

### `cameras`

`speaker_id` 改为可空，存储 `speaker_bindings.binding_id`，而不是 MiNA `deviceID`。迁移时
保留旧字符串作为不可解析的 legacy 值（不把它解释为新设备 DID，也不创建伪绑定）；运行时
找不到对应绑定的摄像头显示为需要重新选择。这样既保留了“音箱不可用”的可见状态，也避免
旧 ID 与新 `binding_id` 意外碰撞。已有摄像头、规则、欢迎词和事件保留，但不导入任何旧环境
变量或 `.mi.token`；新建或重新选择的摄像头才写入新的 `binding_id`，未绑定时写 `NULL`。

## Web API 与安全

```text
POST /api/xiaomi/auth/start
GET  /api/xiaomi/auth/status?attempt_id=...
POST /api/xiaomi/auth/otp
POST /api/xiaomi/auth/cancel
POST /api/xiaomi/devices/refresh
POST /api/xiaomi/bindings/save  (携带可选 confirmation_id)
POST /api/xiaomi/bindings/{binding_id}/test
```

所有写请求沿用当前 Origin 校验、HttpOnly SameSite CSRF cookie 和表单/JSON CSRF token。
每个错误响应只返回稳定的分类码和中文提示，不透传 MiService 原始异常。验证码提交只接受
当前有效 `attempt_id`，过期或已完成的尝试返回冲突错误。单进程只允许一个授权尝试，避免
多个密码和 OTP Future 并存。

设置页在未配置、需要重新授权或已授权未绑定时显示下一步；设备选择、保存绑定、测试按钮和
授权状态均局部更新，不刷新页面。测试结果只显示“测试成功/测试失败”，不阻止绑定保存。

## 规则与摄像头行为

- 新摄像头默认关闭所有规则；没有可用绑定音箱时，开启规则请求被拒绝并提示先选择可用音箱。
- 已开启规则遇到绑定设备解除、设备消失或账号 `auth_required` 时，保留用户开启意图，但
  有效运行暂停并停止该摄像头资源消耗；音箱恢复后自动恢复。
- 保存绑定时取消仍被摄像头引用的设备允许成功，但相关摄像头立即进入音箱不可用状态；不
  自动改绑到其他设备。
- 账号替换提交成功后，旧 worker 停止，新账号和已绑定设备的 worker 原子启动；失败则旧
  运行时不变。

## 测试与验收

### 单元与集成测试

- Storage：schema 创建/升级、单账号 TokenStore、绑定全量替换、默认勾选、设备消失/恢复、
  摄像头引用确认、敏感字段不出查询结果。
- MiService adapter：自定义 TokenStore、OTP Future 回调、MiNA 设备字段归一化、TTS 成功、
  普通失败和认证失败映射。
- Runtime/SpeakerManager：动态替换 worker、队列丢弃、设备不可用暂停/恢复、无可用音箱时
  拒绝开启规则、账号认证错误全局暂停。
- Web：AJAX 授权状态转换、验证码/取消/过期、设备刷新、默认勾选、绑定二次确认、手动测试、
  CSRF/Origin、防止 `deviceID`/`miotDID`/Token/密码出现在响应。

### 手动验收

1. 清空或使用新数据库启动应用，不设置任何 `MI_*` 账号/音箱变量；页面正常打开并显示未配置。
2. 在页面输入小米账号和密码，按实际短信/邮箱验证码完成授权；全程不刷新页面。
3. 确认页面列出 MiNA 返回的全部设备，选择并命名多个设备，验证默认勾选和全量保存。
4. 逐台点击测试，分别观察测试成功/失败，不因测试结果阻止保存。
5. 给不同摄像头绑定不同设备；取消仍被引用设备并完成二次确认，确认对应规则暂停且不静默改绑。
6. 手动刷新设备列表，验证消失/恢复状态；正式规则播报使用 MiNA TTS，不再使用 MIoT `5/3`。
7. 让 Token 失效或模拟认证错误，确认新动作被拒绝、页面提示重新授权，重新授权提交后恢复。
8. 检查 `.env`、日志、事件、HTML/JSON 响应和容器挂载中没有账号密码、Token、DID 或 `.mi.token`。

## 部署与文档

删除 `.env.example` 和 Compose 中的小米账号、密码、音箱 JSON 以及 `.mi.token` 挂载说明；
保留非小米运行参数。README 和 runbook 改为“启动后进入设置页授权”，说明密码不落盘、
Token 存储位置为 SQLite，并保留可信局域网 HTTP 的安全警告。
