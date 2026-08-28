# 设备兼容性 PoC 与 MVP 运行手册

> 本手册同时覆盖设备兼容性 PoC 和首个“人员进入欢迎”MVP。PoC 的 30 分钟摄像头与
> 30 次音箱稳定性步骤仍可稍后执行；已经确认 `xiaobai` 拉流和 L05C 直连播报可用时，
> 可以先按第 14 节启动 MVP。扩大日常使用范围前仍须补完第 6、8、10 和 11 节的稳定性验收。

请在目标 Debian 系服务器上运行本 PoC，不要在开发电脑上运行。服务器和所有小米设备
必须处于同一个可信局域网内。管理页没有账号登录保护；不要通过路由器将管理页的 8080
端口、go2rtc 的 1984 端口或 RTSP 的 8554 端口暴露到局域网之外。

## 运行模式

音箱支持两条互相独立的路径：

- `direct`：不经过 Home Assistant，由探测容器直接调用 MiService 的 MIoT action。
- `ha`：通过 Home Assistant 的 REST API 调用小米集成。

Home Assistant 是可选的。只使用 `direct` 时，跳过第 7 步，不要启动 `homeassistant`，
第 8 步只运行直连小节，`.env.poc` 中的 `HA_*` 无需填写或修改，保留示例默认值即可。
最终验收报告中 HA 显示
`NOT RUN` 不影响选择 `CONTINUE_GO2RTC_DIRECT`。

本手册中的“直连”表示不经过 HA，并不表示完全的局域网本地控制。当前实现通过
`MI_USER`、`MI_PASS` 和 `MI_DID` 调用 MiService，仍需要互联网访问小米服务；应用尚未接入
MiService 的 `MI_LOCAL=<IP>:<Token>` UDP 本地模式。

## 1. 检查服务器

要求 Docker Engine 23 或更高版本、Docker Compose v2.24 或更高版本，以及至少 10 GB 可用
磁盘空间。`app` 使用 v2.24 引入的可选 `env_file`，因此只运行前 13 节 PoC 时不需要提前创建
`.env.mvp`。

```bash
docker version
docker compose version
df -h .
```

## 2. 创建本地环境配置

```bash
cp .env.poc.example .env.poc
chmod 600 .env.poc
poc_campaign_id="poc-$(date +%Y%m%d-%H%M%S)"
sed -i "s/^POC_CAMPAIGN_ID=.*/POC_CAMPAIGN_ID=${poc_campaign_id}/" .env.poc
grep '^POC_CAMPAIGN_ID=' .env.poc
```

在服务器本地填写 `.env.poc`。使用 `direct` 路径时，运行音箱探测前最终必须填写
`MI_USER`、`MI_PASS` 和 `MI_DID`，其中 `MI_DID` 按本节下文先查询再回填；使用 `ha`
路径时，填写第 7 步找到的 `HA_*` 值。不要提交此文件，也不要把其内容粘贴到 issue 或报告
中。上面的命令会将示例占位值替换为当前 PoC 的唯一标识符；允许
格式为 3–64 位，以字母或数字开头，后续只能包含字母、数字、点、下划线或连字符。如果保留
`replace-with-a-unique-poc-run-id`，probe 会在连接设备前报错
`POC_CAMPAIGN_ID must be a unique 3-64 character identifier`。

每次开始新的完整 PoC 时都生成一个新的 `POC_CAMPAIGN_ID`；同一轮 PoC 从首次探测到生成
`gate.md` 必须始终使用同一个值，否则已采集证据无法归入同一次测试。除非同步修改 Compose
的挂载配置，否则保持 `POC_INVENTORY_PATH=/workspace/config/poc-devices.json` 不变。

选择 `direct` 路径时，先在 `.env.poc` 中填写拥有目标音箱的同一个小米账号 `MI_USER` 和
`MI_PASS`，暂时保留 `MI_DID=` 为空。构建探测镜像并查询该账号下的设备：

```bash
docker compose -f compose.poc.yaml --profile tools build probe
docker compose -f compose.poc.yaml --profile tools run --rm \
  --entrypoint python probe -m miservice list
```

在输出中同时按设备名称“小米小爱音箱 Play 增强版”和型号
`xiaomi.wifispeaker.l05c` 定位目标音箱，将该条目的数值 `did` 填入 `.env.poc`：

```dotenv
MI_DID=<音箱条目的数值 did>
```

这里需要的是 MIoT 设备列表中的 `did`。不要填写摄像头 DID、MiNA 列表中的 `deviceID`、
局域网 IP、MAC 地址或型号字符串；设备名称可能重复，也不建议用名称代替数值 DID。列表
输出可能包含设备令牌及其他家庭隐私数据，不要保存、提交或粘贴完整输出。只检查已回填的
这一行，不要输出 `.env.poc` 的其他内容：

```bash
grep '^MI_DID=' .env.poc
```

如果 `miservice list` 返回 `code: 70016` 和“登录验证失败”，说明请求已经到达小米账号服务，
但账号口令在 `serviceLoginAuth2` 阶段被拒绝；此错误发生在设备列表查询和 OTP 验证之前，
与 `MI_DID`、摄像头及 go2rtc 无关。先停止重复尝试，再用下面的命令确认 Compose 传入的值
是否存在、长度是否符合预期，以及是否意外带有首尾空格。该命令不会输出账号或密码正文：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm \
  --entrypoint python probe -c '
import os
for key in ("MI_USER", "MI_PASS"):
    value = os.environ.get(key, "")
    print("{}: set={} len={} trimmed={}".format(
        key, bool(value), len(value), value == value.strip()
    ))
'
```

如果 `set=False`、长度与实际值不符或 `trimmed=False`，先修正 `.env.poc`。Compose 会对未加
引号和双引号中的 `$变量` 做插值；账号或密码含 `$`、`#`、空格等字符时，使用单引号保留
字面值：

```dotenv
MI_USER='实际账号'
MI_PASS='实际密码'
```

如果值中本身含单引号，在单引号值中写成 `\'`。修正后重新执行上面的无泄密检查。确认
容器收到的长度正确后，在浏览器打开 `https://account.xiaomi.com/`，使用同一个登录标识和
密码完成一次密码登录及可能出现的安全验证；`MI_USER` 应使用这次成功登录的标识。浏览器
也拒绝时，先在小米账号页面解决凭据问题。浏览器成功但 `miservice list` 仍返回 `70016`
时，不要连续重试；记录上面检查命令的四个非敏感结果，并继续排查账号认证兼容性。小米
后续还可能返回 `70022` 限流，重复登录只会扩大问题。原始异常会显示账号，反馈日志前先
将账号、Token、DID 等信息替换为 `<REDACTED>`。

## 3. 启动 go2rtc 并配置摄像头

```bash
mkdir -p deploy/go2rtc/state
cp deploy/go2rtc/go2rtc.example.yaml deploy/go2rtc/state/go2rtc.yaml
docker compose -f compose.poc.yaml up -d go2rtc
```

确认容器状态为 `running`，启动日志显示读取 `/config/go2rtc.yaml`，并且 API 在服务器本机
可访问：

```bash
docker compose -f compose.poc.yaml ps go2rtc
docker compose -f compose.poc.yaml logs --tail=20 go2rtc
curl --fail --show-error http://127.0.0.1:1984/api
```

如果状态为 `restarting` 或 `exited`，不要继续配置摄像头。先根据日志解决启动错误；正常日志
应包含 `config path=/config/go2rtc.yaml` 和 `[api] listen addr=127.0.0.1:1984`。

在另一台电脑上创建 SSH 隧道，然后访问 `http://127.0.0.1:1984`：

```bash
ssh -L 1984:127.0.0.1:1984 SERVER_USER@SERVER_IP
```

当前 MVP 只要求流名称为 `xiaobai` 的一台摄像头。如果现在只接入一台摄像头，下面所有
`xiaobai_25k` 操作都可以跳过，不会阻塞第 14 节。两台摄像头内容保留用于后续完整 PoC。

以下操作从小米账号已登录并显示摄像头列表开始：

1. 不要按列表顺序判断设备。先用米家设备名称分别找到“小白智能摄像机”和
   “小白智能摄像机 2.5K 版”的候选项，再核对每项显示的型号。对候选项分别打开 go2rtc
   预览，用实际画面对应的房间或视角做最终确认，避免名称重复或设备排序变化时选错摄像头。
2. 选择“小白智能摄像机”，将流名称固定为 `xiaobai`；选择“小白智能摄像机 2.5K 版”，
   将流名称固定为 `xiaobai_25k`。后续清单、探测和报告都使用这两个名称，不要互换。
3. 两路都保留 Web UI 自动生成的完整 `xiaomi://` 源地址，不要手工拼接或改写其中的账号、
   地区、局域网 IP、DID 或 `model`。只在查询参数中设置 `subtype=sd`：地址已有 `?` 时添加
   `&subtype=sd`，没有 `?` 时添加 `?subtype=sd`；如果已经有 `subtype` 参数，只修改它的值。
4. 保存后返回 Streams 页面，逐路打开预览并各观察至少 30 秒。通过要求是画面持续更新，
   不能只有首帧，也不能持续显示连接错误。
5. 如果某一路的 `sd` 不能通过 30 秒预览，按 `auto`、`1`、`2` 的顺序逐项尝试。每次只改
   这一项的 `subtype` 值，保存后重新观察至少 30 秒，不要同时改其他地址参数。选择其中最低且
   能连续显示的码流，并记录该路最终使用的 `subtype`；不要在清单、issue 或报告中记录完整
   `xiaomi://` 地址。

保存后的配置结构应类似下面的脱敏示例；尖括号内容只表示占位，不是需要原样填写的值：

```yaml
xiaomi:
  "<REDACTED_ACCOUNT_ID>": "<REDACTED_TOKEN>"
streams:
  xiaobai:
    - xiaomi://<REDACTED>?did=<REDACTED_DID>&model=<REDACTED_MODEL>&subtype=<SELECTED_SUBTYPE>
  xiaobai_25k:
    - xiaomi://<REDACTED>?did=<REDACTED_DID>&model=<REDACTED_MODEL>&subtype=<SELECTED_SUBTYPE>
```

实际配置中的账号标识、令牌、DID、局域网 IP 和完整 `xiaomi://` 地址都是家庭隐私数据，
不得提交到 Git，也不得粘贴到 issue 或报告中。

完整双摄像头 PoC 在完成两路预览后，应在服务器上确认两个固定名称都已注册；当前单摄像头
MVP 只需确认 `xiaobai`：

```bash
curl --fail --silent --show-error http://127.0.0.1:1984/api/streams
```

完整 PoC 的输出必须同时包含 `xiaobai` 和 `xiaobai_25k`；当前 MVP 的输出包含 `xiaobai`
即可。然后使用项目现有的 probe 镜像检查已配置的 RTSP 输出；未配置第二台摄像头时跳过
第二条命令：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm --entrypoint ffprobe probe \
  -v error -rtsp_transport tcp -select_streams v:0 \
  -show_entries stream=index,codec_type,codec_name,width,height \
  -of json rtsp://127.0.0.1:8554/xiaobai
docker compose -f compose.poc.yaml --profile tools run --rm --entrypoint ffprobe probe \
  -v error -rtsp_transport tcp -select_streams v:0 \
  -show_entries stream=index,codec_type,codec_name,width,height \
  -of json rtsp://127.0.0.1:8554/xiaobai_25k
```

所有已执行的命令都必须以退出码 `0` 结束，并且各自输出的 `streams` 数组至少包含一项
`codec_type` 为 `video` 的流。这里的 `-rtsp_transport tcp` 只控制 ffprobe 到 go2rtc 的
RTSP 连接；它与 `xiaomi://` 源地址中控制摄像头到 go2rtc 的 `transport` 参数不是同一层。
如果遗漏该选项，ffprobe 可能先尝试 go2rtc 不支持的 UDP RTSP 传输并显示
`461 Unsupported transport`。不要仅因为这个 `461` 就修改小米源地址。

若 API 输出缺少名称，检查 Web UI 中是否使用了上面的固定名称。若名称存在，但预览或
ffprobe 返回 `404 Not Found`，先用只请求视频的 API 调用获取 Xiaomi 层的真实错误：

```bash
curl --max-time 45 --silent --show-error \
  --write-out '\nHTTP %{http_code}\n' \
  'http://127.0.0.1:1984/api/streams?src=xiaobai&video'
```

若返回类似 `xiaomi: probe: miss: read media: cs2: read udp ... i/o timeout`，首先确认摄像头
已通电、已开机，并且在米家 App 中显示在线；摄像头关闭时，go2rtc 仍可能完成账号鉴权和
设备发现，但无法收到媒体数据。打开摄像头后保持原配置不变，重新执行 API 和 ffprobe
验证。只有设备确认在线后仍然超时，才查看
`docker compose -f compose.poc.yaml logs --tail=100 go2rtc`，并按上述顺序逐项尝试其他
`subtype`；不要同时修改 `subtype` 和 `transport`。任一路仍未通过时都不要进入第 4 步。

## 4. 记录设备清单

```bash
mkdir -p config
cp config/poc-devices.example.json config/poc-devices.json
```

按本手册完成 PoC 时，下面列出的设备信息都应填写。当前自动验收会检查两台摄像头的
`name`、`miot_model`、`firmware` 和 `codec`，以及音箱的 `name`、`miot_model` 和
`firmware`；摄像头的 `ip` 暂未被自动检查，但仍应按手册记录，供确认设备身份和排查 DHCP
地址变化使用。不要用产品销售名称代替 MIoT 型号。

| 设备和字段 | 获取位置和填写方法 |
|---|---|
| 摄像头 `miot_model` | 使用第 3 步 go2rtc 小米设备列表中显示的型号；也可以只查看本地 `deploy/go2rtc/state/go2rtc.yaml` 中对应源地址的 `model=` 参数。填写完整的 MIoT 型号字符串，不要复制整条源地址。 |
| 摄像头 `firmware` | 在米家 App 中分别进入设备卡片，打开右上角菜单，在“固件更新”或“固件升级”页面记录当前已安装版本。菜单名称可能随 App 或设备插件版本略有不同。 |
| 摄像头 `ip` | 优先从路由器的 DHCP 客户端或已连接设备列表中，按设备名称和 MAC 地址核对后记录；也可以只查看 go2rtc 自动生成源地址中 `@` 之后、`?` 之前的局域网 IP。记录本次 PoC 开始时实际使用的地址。 |
| 摄像头 `codec` | 使用第 3 步最终选定的 `subtype` 运行 `ffprobe`，将视频流的 `codec_name` 原样填写，例如 `h264` 或 `hevc`。不要根据产品宣传或米家中的编码开关推测。 |
| 音箱 `miot_model` | 模板已按目标“小米小爱音箱 Play 增强版”预填 `xiaomi.wifispeaker.l05c`。先在米家设备信息中核对，随后用第 2 步的 `miservice list` 再确认。 |
| 音箱 `firmware` | 在米家或小爱音箱 App 的设备设置、设备信息或固件升级页面记录当前已安装版本。 |

`miservice list` 的完整输出，以及完整的 `xiaomi://` 源地址，可能包含令牌、DID、账号标识
或其他家庭隐私数据。只把上表要求的值抄入清单；不要添加密码、令牌、DID、MAC 地址或小米
源地址，也不要把命令的完整输出保存到 issue 或报告中。

不要从完整 PoC 清单中删除测试失败的摄像头；完整 PoC 验收要求必须恰好存在 `xiaobai` 和
`xiaobai_25k`，并且从这两个名称中选择唯一的主摄像头。当前单摄像头 MVP 可以暂不生成
双摄像头清单。修改此文件会改变其指纹，并有意使之前的探测证据失效。

## 5. 运行单元测试和代码检查

```bash
docker build -f docker/poc.Dockerfile -t daihougou-poc:test .
docker run --rm --entrypoint pytest daihougou-poc:test -q
docker run --rm --entrypoint ruff daihougou-poc:test check src tests
docker compose -f compose.poc.yaml config --quiet
```

PoC 测试镜像已经复制仓库策略测试所需的 `.dockerignore`、Compose、Dockerfile 和 MVP 环境
示例，因此不需要宿主机只读挂载。pytest 输出末尾不得出现 `failed`；容器内具备 FFmpeg，生成
视频的集成测试也不得显示为 `skipped`。

## 6. 运行摄像头 30 分钟稳定性和恢复测试

在单独的终端启动主机资源采样：

```bash
set -a
source .env.poc
set +a
scripts/capture-host-stats.sh artifacts/poc/host-stats-30m.log 30 "$POC_CAMPAIGN_ID"
```

分别运行每台摄像头：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm probe camera decode --stream xiaobai --duration-seconds 1800
docker compose -f compose.poc.yaml --profile tools run --rm probe camera decode --stream xiaobai_25k --duration-seconds 1800
```

重启 go2rtc，并测试已配置主摄像头的恢复能力：

```bash
go2rtc_restart_id="go2rtc-$(date +%s)"
docker compose -f compose.poc.yaml restart go2rtc
docker compose -f compose.poc.yaml --profile tools run --rm probe camera wait \
  --stream xiaobai_25k --max-seconds 60 --restart-id "$go2rtc_restart_id"
```

两条解码命令都必须完成，且恢复必须在 60 秒内完成。

## 7. （可选）配置临时 Home Assistant 路径

选择 `direct` 路径时跳过本节。只有需要验证 HA 路径时才执行以下步骤。

```bash
mkdir -p deploy/homeassistant/state
cp deploy/homeassistant/configuration.yaml deploy/homeassistant/state/configuration.yaml
docker compose -f compose.poc.yaml --profile ha up -d homeassistant
```

在 `http://SERVER_IP:8123` 完成初始化，添加小米集成并绑定 L05C。创建一个专用的非管理员
用户，并为该用户创建长期访问令牌（Long-Lived Access Token）。在开发者工具中找到可用的
L05C 服务、实体、文本字段和其他数据，然后只更新 `.env.poc` 中相应的 `HA_*` 值。
非管理员操作失败必须记录为失败；不要改用所有者令牌。

重启 HA，等待其界面恢复正常，然后使用专用的非管理员令牌执行明确的重启后验证：

```bash
ha_restart_id="ha-$(date +%s)"
docker compose -f compose.poc.yaml restart homeassistant
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker validate-ha \
  --restart-id "$ha_restart_id" --non-admin-confirmed
```

确认参数表示操作者的现场确认。只有在 `.env.poc` 中确实填入专用非管理员用户令牌时，才可
使用此参数。

## 8. 运行并标注音箱路径各 30 次试播

每次试播都必须由成年人现场聆听。API 接受请求不等于音箱实际播放并被听见。

### 8.1 直连路径（不需要 Home Assistant）

先确认第 2 步已经从 MIoT 设备列表取得 L05C 的数值 `did`，并写入 `.env.poc` 的
`MI_DID`。如果需要复核设备身份，可以重新构建探测镜像并查询列表：

```bash
docker compose -f compose.poc.yaml --profile tools build probe
```

```bash
docker compose -f compose.poc.yaml --profile tools run --rm \
  --entrypoint python probe -m miservice list
```

输出的保密要求与第 2 步相同。然后可以用以下命令查看该型号的 MIoT 能力：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm \
  --entrypoint python probe -m miservice spec xiaomi.wifispeaker.l05c text
```

先执行一次单次播报，确认返回的 JSON 中 `code` 为 `0`，并且成年人实际听到音箱发声：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm \
  --entrypoint python probe -m miservice action \
  '{"did":"MI_DID_VALUE","siid":5,"aiid":3,"in":["这是直连测试"]}'
```

当前 PoC 对 L05C 固定使用 `siid=5`、`aiid=3`。如果 API 返回成功但音箱没有声音，必须将
该次记为未听见，不得仅凭 `code=0` 判定通过。

```bash
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker run --backend direct --count 30 --interval-seconds 8
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker annotate --run-id DIRECT_RUN_ID --count 30 --missed '2,7'
```

将示例中的未听见编号替换为实际未听见的试播编号。

### 8.2 Home Assistant 路径（可选）

只有完成第 7 步并决定验证 HA 时才运行以下命令：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker run --backend ha --count 30 --interval-seconds 8
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker annotate --run-id HA_RUN_ID --count 30 --missed ''
```

如果只验证直连路径，不要运行 `--backend ha`，也不需要填写 `HA_*`。

## 9. 停止测试失败的音箱路径

一条已运行的路径只有同时满足 30/30 次 API 接受和至少 29/30 次现场听见，才通过第一道
验收。不要为失败的路径运行扩展测试。如果所有已选择的路径都失败，请保留产物并停止 PoC。

## 10. 为通过的路径运行并标注 100 次试播

对第 9 步通过的每条路径执行：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker run --backend BACKEND --count 100 --interval-seconds 8
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker annotate --run-id RUN_ID --count 100 --missed ''
```

扩展验收要求至少 99/100 次 API 接受，以及 98/100 次现场确认听见。

## 11. 运行 8 小时主摄像头压力测试

在一个终端启动新的主机资源采样，再在另一个终端运行主摄像头解码：

```bash
scripts/capture-host-stats.sh artifacts/poc/host-stats-8h.log 60 "$POC_CAMPAIGN_ID"
docker compose -f compose.poc.yaml --profile tools run --rm probe camera decode --stream xiaobai_25k --duration-seconds 28800
```

如果任何 `MemAvailable` 采样低于 `768000 kB`，或日志中包含 OOM killer 事件，不得宣布
测试成功。验收同样会拒绝以下情况：缺少 OOM 可见性、采样间隔超过 90 秒，或采样窗口没有
覆盖主摄像头解码过程。先测较低质量的码流；不要为了通过测试而添加 swap。

## 12. 生成并审核验收报告

```bash
docker compose -f compose.poc.yaml --profile tools run --rm probe report gate \
  --inventory /workspace/config/poc-devices.json \
  --host-stats /workspace/artifacts/poc/host-stats-8h.log
```

审核 `artifacts/poc/gate.md`。记录以下决定之一：`CONTINUE_GO2RTC_HA`、
`CONTINUE_GO2RTC_DIRECT`、`REPLACE_CAMERA`、`FIX_SPEAKER_INTEGRATION` 或
`UPGRADE_SERVER`。只有 `CONTINUE_*` 决定才允许开始规划 MVP。

## 13. 只停止 PoC 容器

```bash
docker compose -f compose.poc.yaml --profile ha down
```

保留 `artifacts/poc` 以便记录最终决定。不要使用 `docker compose down -v`；删除卷或状态会
增加问题排查和后续重跑的难度。

## 14. 启动“人员进入欢迎”MVP

### 14.1 创建私有状态目录和环境文件

MVP 继续使用第 3 节已经验证的 `xiaobai`。先创建不会进入 Git 或 Docker 构建上下文的状态
目录：

```bash
mkdir -p deploy/app/state deploy/miservice/state
chmod 700 deploy/app/state deploy/miservice/state
test -e .env.mvp || cp .env.mvp.example .env.mvp
chmod 600 .env.mvp
```

如果 `.env.mvp` 已存在，不要再次执行 `cp`。在服务器本地填写 `MI_USER`、`MI_PASS` 和
`MI_DID`；它们必须与 `.env.poc` 中已经通过直连播报的同一账号和 L05C 数值 DID 一致。
同时运行 `ip -4 -brief address`，把 `WEB_HOST=SERVER_LAN_IP` 中的占位值替换为服务器在家庭
局域网中的固定 IPv4 地址。不要填写 `0.0.0.0`，也不要使用 Docker 网桥地址。只检查不含
凭据的绑定值：

```bash
grep '^WEB_HOST=' .env.mvp
```

不要在终端输出三个小米凭据。其他参数先保留默认值。

### 14.2 持久化 MiService 登录 Token

MiService 把登录状态写入 `$HOME/.mi.token`。Compose 已将 probe 和 app 的 `HOME` 都设为
`/var/lib/daihougou/mi`，并把宿主机 `deploy/miservice/state/` 挂载到该位置。这样
`docker compose run --rm` 删除临时容器时不会再删除 Token。

先重建包含该挂载的 probe，然后在有 TTY 的终端完成一次登录；小米要求时输入手机验证码：

```bash
docker compose -f compose.poc.yaml --profile tools build probe
docker compose -f compose.poc.yaml --profile tools run --rm \
  --entrypoint python probe -m miservice list
```

不要保存或粘贴设备列表。确认 Token 已持久化并收紧权限：

```bash
sudo test -s deploy/miservice/state/.mi.token
sudo chmod 600 deploy/miservice/state/.mi.token
sudo stat -c '%a %n' deploy/miservice/state deploy/miservice/state/.mi.token
```

预期两行权限分别为 `700` 和 `600`。随后再次运行同一条 `miservice list` 命令；正常情况下
不再要求手机验证码。若仍要求验证码，先执行下面的挂载检查，不要重复登录：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm \
  --entrypoint sh probe -c 'printf "HOME=%s\n" "$HOME"; test -s "$HOME/.mi.token"'
```

预期输出 `HOME=/var/lib/daihougou/mi` 且退出码为 `0`。不要执行 `cat .mi.token`。

### 14.3 构建和启动

```bash
docker compose -f compose.poc.yaml build app
docker compose -f compose.poc.yaml up -d go2rtc app
docker compose -f compose.poc.yaml ps go2rtc app
docker compose -f compose.poc.yaml logs --tail=50 app
```

app 首次加载模型和等待摄像头帧时可以短暂显示 `health: starting`。60 秒后检查：

```bash
server_lan_ip=$(sed -n 's/^WEB_HOST=//p' .env.mvp)
curl --fail --silent --show-error "http://${server_lan_ip}:8080/healthz" \
  | python3 -m json.tool
```

摄像头在线时 `camera` 和 `detector` 应最终为 `ready`。摄像头关闭或临时断线时 HTTP 仍返回
成功，但 `status`/`camera` 为 `degraded`；这表示管理进程还活着，不应通过反复重启 app
来掩盖摄像头问题。

从局域网浏览器打开 `http://SERVER_IP:8080/`。确认显示的是 `xiaobai` 对应的运行状态，且
“人员进入欢迎”初始为“已关闭”。页面不需要 Home Assistant。

### 14.4 认证失效处理

常驻 app 不读取 stdin，也不会等待手机验证码。管理页显示音箱认证
`reauth_required` 时，使用 14.2 的一次性 probe 命令重新完成认证，然后执行：

```bash
sudo chmod 600 deploy/miservice/state/.mi.token
docker compose -f compose.poc.yaml restart app
```

不需要重新构建镜像。重启后等待健康检查，再从管理页继续操作。不要删除 Token 作为普通
排障手段；只有确认登录状态损坏并准备立即重新认证时，才人工备份后处理该文件。

### 14.5 验证重建后不再要求验证码

先在成年人在场时完成一次第 8.1 节的单次播报，再连续重建 probe 和 app 容器：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm \
  -T --entrypoint python probe -m miservice list >/dev/null
docker compose -f compose.poc.yaml up -d --force-recreate app
docker compose -f compose.poc.yaml ps app
```

第一条命令退出码应为 `0`。`-T` 会关闭交互终端：Token 有效时设备列表被重定向到
`/dev/null`；需要验证码时命令会立即失败，而不会在不可见的提示上等待。第二条完成后
Token 文件仍存在，app 最终恢复健康。

### 14.6 功能验收

1. 保持规则关闭，先让画面稳定无人 10 秒，再有人进入；音箱不得播报。
2. 在管理页开启规则。若此时画面已经有人，启动校准不得立即播报。
3. 画面无人至少 10 秒，然后有人正常走入；从进入到听到欢迎语应不超过 5 秒，且只播一次。
4. 人持续留在画面 2 分钟；不得重复播报。
5. 人离开至少 10 秒，并等待上次播报 60 秒冷却结束；再次进入后应播报第二次。
6. 关闭摄像头；页面应在 30 秒后显示摄像头降级，音箱不得误播。重新打开摄像头后应自动恢复。
7. 检查应用状态目录只包含 SQLite 及其 WAL/SHM 文件，不得出现图片、视频或音频：

```bash
find deploy/app/state -maxdepth 1 -type f -printf '%f\n'
```

完成上述功能后保持 app 连续运行 30 分钟，期间至少完成三次“离开后再次进入”。记录是否有
漏报、误报、重复播报和超过 5 秒的响应。这个 30 分钟结果用于 MVP 开发验收，不替代第 6、
8 和 10 节尚未完成的长期稳定性测试。

### 14.7 停止与升级

停止 MVP 但保留 go2rtc：

```bash
docker compose -f compose.poc.yaml stop app
```

升级代码后只需重建 app；数据库和登录 Token 都保留在宿主机：

```bash
docker compose -f compose.poc.yaml build app
docker compose -f compose.poc.yaml up -d app
```

## 产物隐私警告

JSONL 中的密钥会按字段名脱敏，但 PoC 产物仍可能包含设备名称、固件版本、局域网 IP、服务
错误和时间信息。请将产物目录视为家庭隐私数据，不要未经处理直接公开。
