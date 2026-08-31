# 大口九

大口九是一个部署在家庭局域网内的儿童陪护应用。当前 MVP 通过 `go2rtc` 读取小米生态
摄像头的视频流，在本地检测“人员进入画面”事件，并通过小爱音箱随机播放一条欢迎语。

当前版本提供：

- 本地视频解码与人员检测，不把摄像头画面上传给第三方视觉服务；
- 一个简单的局域网管理页面，可查看运行状态、最近事件，为每台摄像头独立开启规则并指定
  播放音箱；
- 通过 MiService 直连小爱音箱，不要求安装 Home Assistant；
- 多摄像头断线隔离与自动恢复、规则冷却，以及摄像头配置、全局欢迎词和事件记录持久化。

> 当前管理页面没有登录功能。服务器、摄像头、音箱和管理设备必须位于同一个可信局域网，
> 不要将 `8080`、`1984` 或 `8554` 端口暴露到互联网。

## 当前支持范围

- 摄像头：已验证小白智能摄像机和小白智能摄像机 2.5K 版；应用会发现 `go2rtc` 中所有
  已命名的码流，不限制摄像头数量。
- 音箱：已验证小米小爱音箱 Play 增强版，型号 `xiaomi.wifispeaker.l05c`。
- 规则：每台摄像头独立检测人员从画面外进入，并通过固定配对的音箱随机播放一条英语
  欢迎语。新发现摄像头的规则默认关闭。
- 音箱控制：当前使用 MiService 调用小米服务，需要服务器能够访问互联网。Home Assistant
  仅用于设备兼容性 PoC 的备选路径，不是运行 MVP 的依赖。

## 运行要求

- Debian 系 Linux 服务器；
- Docker Engine 23 或更高版本；
- Docker Compose v2.24 或更高版本；
- 至少 10 GB 可用磁盘空间；
- 服务器与小米设备处于同一个可信子网；
- 可访问 GitHub、容器镜像仓库、模型下载地址和小米服务。

先确认 Docker 和磁盘空间：

```bash
docker version
docker compose version
df -h .
```

## 安装

克隆仓库并进入项目目录：

```bash
git clone https://github.com/csbun/DaiHouGou.git
cd DaiHouGou
```

创建私有状态目录和本地环境文件：

```bash
mkdir -p deploy/go2rtc/state deploy/app/state deploy/miservice/state
chmod 700 deploy/app/state deploy/miservice/state
test -e deploy/go2rtc/state/go2rtc.yaml || \
  cp deploy/go2rtc/go2rtc.example.yaml deploy/go2rtc/state/go2rtc.yaml
test -e .env.poc || cp .env.poc.example .env.poc
test -e .env.mvp || cp .env.mvp.example .env.mvp
chmod 600 .env.poc .env.mvp
```

这些本地环境文件和状态目录已被 Git 及 Docker 构建上下文忽略。不要提交其中的账号、密码、
Token、DID、局域网地址或完整 `xiaomi://` 地址。

从旧的单摄像头版本升级时，新版本不迁移旧 SQLite 结构。必须先停止 app，并按运行手册
“14.1 升级前备份并重置旧数据库”把旧数据库移动到
`backup-before-multicamera-日期时间` 目录；旧 app 在新镜像部署完成前保持停止。

## 配置摄像头

### 1. 启动 go2rtc

```bash
docker compose -f compose.poc.yaml up -d go2rtc
docker compose -f compose.poc.yaml ps go2rtc
docker compose -f compose.poc.yaml logs --tail=20 go2rtc
curl --fail --show-error http://127.0.0.1:1984/api
```

`go2rtc` 的管理 API 默认只监听服务器的 `127.0.0.1:1984`。如需从另一台电脑打开其管理
页面，先建立 SSH 隧道：

```bash
ssh -L 1984:127.0.0.1:1984 SERVER_USER@SERVER_IP
```

然后在该电脑访问 `http://127.0.0.1:1984`。

### 2. 添加小米摄像头

在 go2rtc 页面中登录拥有摄像头的小米账号，从设备列表中按设备名称、型号和实际预览画面
确认目标摄像头：

1. 为每台摄像头设置稳定且唯一的流名称，例如 `xiaobai`、`xiaobai_25k`。该名称会原样显示
   在大口九管理页中，应用内不能改名。
2. 保留页面自动生成的完整 `xiaomi://` 源地址，不要手工重写账号、Token、DID、型号或 IP。
3. 优先在源地址查询参数中使用 `subtype=sd`，保存后连续预览至少 30 秒。
4. 如果 `sd` 不稳定，依次尝试 `auto`、`1`、`2`，选择码率最低且可以持续更新的码流。

配置会保存在 `deploy/go2rtc/state/go2rtc.yaml`。该文件包含家庭设备凭据，不得提交或公开。

确认所有固定名称已经注册：

```bash
curl --fail --silent --show-error http://127.0.0.1:1984/api/streams \
  | python3 -m json.tool
```

输出中必须出现准备接入大口九的每个流名称。摄像头也必须已通电、开机，并在米家 App 中
显示在线。应用只会在启动时和人工点击“刷新摄像头”时读取这份列表，不会后台轮询。

## 配置小爱音箱

### 1. 查询音箱 DID

在 `.env.poc` 中填写拥有目标音箱的小米账号和密码，暂时保留 `MI_DID=` 为空：

```dotenv
MI_USER='小米账号'
MI_PASS='小米账号密码'
MI_DID=
```

账号或密码包含 `$`、`#`、空格等字符时应使用单引号，避免 Compose 插值。然后构建工具镜像
并查询账号下的设备：

```bash
docker compose -f compose.poc.yaml --profile tools build probe
docker compose -f compose.poc.yaml --profile tools run --rm \
  --entrypoint python probe -m miservice list
```

小米服务可能要求在终端输入手机验证码。根据设备名称“小米小爱音箱 Play 增强版”和型号
`xiaomi.wifispeaker.l05c` 找到目标音箱，把该条目的数值 `did` 填入 `.env.poc` 的
`MI_DID`。不要使用摄像头 DID、MiNA `deviceID`、IP、MAC 地址或型号字符串代替。

设备列表可能包含 Token 等隐私数据，不要保存、提交或粘贴完整输出。

### 2. 持久化登录 Token

上面的命令会把 MiService 登录状态写入 `deploy/miservice/state/.mi.token`。该目录同时挂载到
工具容器和常驻应用，因此重建容器后通常不需要再次输入验证码。

确认文件存在并收紧权限：

```bash
sudo test -s deploy/miservice/state/.mi.token
sudo chmod 600 deploy/miservice/state/.mi.token
sudo stat -c '%a %n' deploy/miservice/state deploy/miservice/state/.mi.token
```

再次执行设备查询；正常情况下不再要求验证码：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm \
  --entrypoint python probe -m miservice list
```

## 配置应用

编辑 `.env.mvp`，至少填写以下内容：

```dotenv
MI_USER='小米账号'
MI_PASS='小米账号密码'
MI_SPEAKERS_JSON='[{"id":"living_room","name":"客厅音箱","did":"音箱数值DID"},{"id":"bedroom","name":"卧室音箱","did":"另一个音箱数值DID"}]'
GO2RTC_API_URL=http://127.0.0.1:1984
GO2RTC_RTSP_BASE_URL=rtsp://127.0.0.1:8554
WEB_HOST=服务器的局域网IPv4地址
WEB_PORT=8080
```

`MI_USER`、`MI_PASS` 和 `MI_SPEAKERS_JSON` 中的 DID 必须与已经通过 MiService 查询的同一
账号及音箱一致。`id` 是应用内部稳定且唯一的英文标识，`name` 是管理页显示名称；数组中的
第一个音箱是新发现摄像头的默认配对音箱。至少配置一个音箱，且不能重复 `id` 或 DID。
`WEB_HOST` 应填写服务器在家庭局域网中的固定 IPv4 地址，不要填写 `SERVER_LAN_IP`、
`0.0.0.0` 或 Docker 网桥地址。可以使用以下命令查看服务器地址：

```bash
ip -4 -brief address
```

其余参数可以先保留默认值：

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DATA_DIR` | `/var/lib/daihougou/data` | SQLite 状态目录 |
| `MODEL` | `/opt/daihougou/models/person_detection_mediapipe_2023mar.onnx` | 人员检测模型 |
| `DETECTION_FPS` | `1.0` | 每秒检测帧数 |
| `PERSON_THRESHOLD` | `0.55` | 人员检测置信度阈值，范围为 0 到 1 |
| `LEAVE_SECONDS` | `10.0` | 连续无人多久后判定人员离开 |
| `WELCOME_COOLDOWN_SECONDS` | `60.0` | 两次欢迎播报之间的最短间隔 |

## 启动应用

构建应用镜像并启动 go2rtc 与大口九：

```bash
docker compose -f compose.poc.yaml build app
docker compose -f compose.poc.yaml up -d go2rtc app
docker compose -f compose.poc.yaml ps go2rtc app
docker compose -f compose.poc.yaml logs --tail=50 app
```

首次构建会下载 Python 依赖和人员检测模型，需要能够访问互联网。应用首次启动加载模型并等待
摄像头画面时，容器可以短暂显示 `health: starting`。

等待约 60 秒后检查健康状态：

```bash
server_lan_ip=$(sed -n 's/^WEB_HOST=//p' .env.mvp)
curl --fail --silent --show-error "http://${server_lan_ip}:8080/healthz" \
  | python3 -m json.tool
```

应用启动后会发现 `go2rtc` 当前所有码流并保存摄像头记录，但新摄像头的欢迎规则全部保持
关闭，因此不会启动 FFmpeg 或人员检测模型；管理页会明确显示“未启动（规则关闭）”。摄像头
关闭或临时断线时，接口仍可访问，对应摄像头会显示降级，其他摄像头继续运行。

从同一局域网中的浏览器打开：

```text
http://SERVER_IP:8080/
```

管理页中的摄像头名称直接来自 `go2rtc`。需要新增摄像头时，先在 `go2rtc` 保存码流，再点击
“刷新摄像头”；刷新是手动操作，不会产生额外轮询。为每台摄像头选择固定的播放音箱并开启
“人员进入欢迎”规则后，开关会原地更新而不会刷新整个页面。先让画面连续无人至少 10 秒，
再让人员正常进入画面进行验证。人员持续留在画面中时不会重复播报；再次播报需要人员离开
至少 10 秒，并等待默认 60 秒冷却结束。

顶部“设置”页面中的欢迎词每行一条，保存后下一次触发立即使用，无需重启。新数据库默认
提供 10 句英语欢迎语；升级时仅会替换旧版本自带的 3 句中文默认值，不会覆盖用户修改过的
内容。开启第 4 台及更多摄像头时页面会提示当前服务器负载可能升高，但不会阻止操作。没有
任何已开启视觉规则时，应用不会保留 FFmpeg 解码进程或检测模型。

## 常用运维命令

查看状态和日志：

```bash
docker compose -f compose.poc.yaml ps go2rtc app
docker compose -f compose.poc.yaml logs --tail=100 go2rtc app
```

重启应用：

```bash
docker compose -f compose.poc.yaml restart app
```

停止应用但保留 go2rtc：

```bash
docker compose -f compose.poc.yaml stop app
```

升级代码后重建应用，数据库、摄像头配置、音箱配对和 MiService Token 会保留在宿主机：

```bash
git pull --ff-only
docker compose -f compose.poc.yaml build app
docker compose -f compose.poc.yaml up -d app
```

如果管理页显示音箱认证需要重新登录，重新运行一次交互式 `miservice list`，完成验证码验证后
执行：

```bash
sudo chmod 600 deploy/miservice/state/.mi.token
docker compose -f compose.poc.yaml restart app
```

## 数据与安全

- 管理页无账号认证，只应在可信局域网中使用。
- 不要通过路由器转发 `8080`、`1984` 或 `8554` 端口。
- 不要提交 `.env.poc`、`.env.mvp`、`deploy/*/state/` 或 `artifacts/`。
- 应用不持久化图片、视频或音频；`deploy/app/state/` 只保存 SQLite 规则状态与事件记录。
- 音箱 DID 只保存在私有环境文件中，不会显示在管理页、状态接口或事件记录中。
- MiService 直连不经过 Home Assistant，但仍会通过互联网调用小米服务。

## 详细运行手册

摄像头码流验证、音箱单次试播、稳定性测试、认证故障处理和完整验收步骤见
[设备兼容性 PoC 与 MVP 运行手册](docs/poc-runbook.md)。

## 许可证

本项目使用 [MIT License](LICENSE)。
