# 设备兼容性 PoC 运行手册

请在目标 Debian 系服务器上运行本 PoC，不要在开发电脑上运行。服务器和所有小米设备
必须处于同一个可信局域网内。不要通过路由器将 go2rtc 的 1984 端口或 RTSP 的 8554 端口
暴露到局域网之外。

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

要求 Docker Engine 23 或更高版本、Docker Compose v2，以及至少 10 GB 可用磁盘空间。

```bash
docker version
docker compose version
df -h .
```

## 2. 创建本地环境配置

```bash
cp .env.poc.example .env.poc
chmod 600 .env.poc
```

在服务器本地填写 `.env.poc`。使用 `direct` 路径时，必须填写 `MI_USER`、`MI_PASS` 和
`MI_DID`；使用 `ha` 路径时，填写第 7 步找到的 `HA_*` 值。不要提交此文件，也不要把其
内容粘贴到 issue 或报告中。每次完整 PoC 开始前，为 `POC_CAMPAIGN_ID` 换一个新的唯一
标识符；在生成 `gate.md` 前不要修改它。除非同步修改 Compose 的挂载配置，否则保持
`POC_INVENTORY_PATH=/workspace/config/poc-devices.json` 不变。

## 3. 启动 go2rtc 并配置两台摄像头

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

完成两路预览后，在服务器上确认两个固定名称都已注册：

```bash
curl --fail --silent --show-error http://127.0.0.1:1984/api/streams
```

输出必须同时包含 `xiaobai` 和 `xiaobai_25k`。然后使用项目现有的 probe 镜像分别检查两路
RTSP 输出：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm --entrypoint ffprobe probe \
  -v error -select_streams v:0 -show_entries stream=index,codec_type,codec_name,width,height \
  -of json rtsp://127.0.0.1:8554/xiaobai
docker compose -f compose.poc.yaml --profile tools run --rm --entrypoint ffprobe probe \
  -v error -select_streams v:0 -show_entries stream=index,codec_type,codec_name,width,height \
  -of json rtsp://127.0.0.1:8554/xiaobai_25k
```

两条命令都必须以退出码 `0` 结束，并且各自输出的 `streams` 数组至少包含一项
`codec_type` 为 `video` 的流。若 API 输出缺少名称，检查 Web UI 中是否使用了上面的固定名称；
若名称存在但预览或 ffprobe 没有视频，查看
`docker compose -f compose.poc.yaml logs --tail=100 go2rtc`，并按上述顺序尝试下一个
`subtype`。任一路仍未通过时都不要进入第 4 步。

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
| 音箱 `miot_model` | 模板已按目标“小米小爱音箱 Play 增强版”预填 `xiaomi.wifispeaker.l05c`。先在米家设备信息中核对，随后用第 8.1 节的 `miservice list` 再确认。 |
| 音箱 `firmware` | 在米家或小爱音箱 App 的设备设置、设备信息或固件升级页面记录当前已安装版本。 |

`miservice list` 的完整输出，以及完整的 `xiaomi://` 源地址，可能包含令牌、DID、账号标识
或其他家庭隐私数据。只把上表要求的值抄入清单；不要添加密码、令牌、DID、MAC 地址或小米
源地址，也不要把命令的完整输出保存到 issue 或报告中。

不要从清单中删除测试失败的摄像头；验收要求必须恰好存在 `xiaobai` 和 `xiaobai_25k`，
并且从这两个名称中选择唯一的主摄像头。修改此文件会改变其指纹，并有意使之前的探测证据
失效。

## 5. 运行单元测试和代码检查

```bash
docker build -f docker/poc.Dockerfile -t daihougou-poc:test .
docker run --rm --entrypoint pytest daihougou-poc:test -q
docker run --rm --entrypoint ruff daihougou-poc:test check src tests
docker compose -f compose.poc.yaml config --quiet
```

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

先确认探测镜像可用：

```bash
docker compose -f compose.poc.yaml --profile tools build probe
```

使用同一个小米账号查询设备列表：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm \
  --entrypoint python probe -m miservice list
```

在输出中找到“小米小爱音箱 Play 增强版”，确认型号为 `xiaomi.wifispeaker.l05c`，将其
对应的数值 DID 填入 `.env.poc` 的 `MI_DID`。输出可能包含令牌等敏感信息，不要保存、
提交或粘贴到报告中。也可以用以下命令查看该型号的 MIoT 能力：

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

## 产物隐私警告

JSONL 中的密钥会按字段名脱敏，但 PoC 产物仍可能包含设备名称、固件版本、局域网 IP、服务
错误和时间信息。请将产物目录视为家庭隐私数据，不要未经处理直接公开。
