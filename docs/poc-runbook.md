# 设备兼容性 PoC 运行手册

请在目标 Debian 系服务器上运行本 PoC，不要在开发电脑上运行。服务器和所有小米设备
必须处于同一个可信局域网内。不要通过路由器将 go2rtc 的 1984 端口或 RTSP 的 8554 端口
暴露到局域网之外。

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

在服务器本地填写 `.env.poc` 中的小米账号信息和可选的 Home Assistant 密钥。不要提交此
文件，也不要把其内容粘贴到 issue 或报告中。每次完整 PoC 开始前，为 `POC_CAMPAIGN_ID`
换一个新的唯一标识符；在生成 `gate.md` 前不要修改它。除非同步修改 Compose 的挂载配置，
否则保持 `POC_INVENTORY_PATH=/workspace/config/poc-devices.json` 不变。

## 3. 启动 go2rtc 并配置两台摄像头

```bash
mkdir -p deploy/go2rtc/state
cp deploy/go2rtc/go2rtc.example.yaml deploy/go2rtc/state/go2rtc.yaml
docker compose -f compose.poc.yaml up -d go2rtc
```

在另一台电脑上创建 SSH 隧道，然后访问 `http://127.0.0.1:1984`：

```bash
ssh -L 1984:127.0.0.1:1984 SERVER_USER@SERVER_IP
```

在 go2rtc 中添加小米账号，并将两台摄像头当前可用且码率最低的码流分别配置为
`xiaobai` 和 `xiaobai_25k`。先尝试 `subtype=sd`。确认这两个名称都出现在
`/api/streams` 中。

## 4. 记录设备清单

```bash
mkdir -p config
cp config/poc-devices.example.json config/poc-devices.json
```

为两台摄像头填写实际的 MIoT 型号、固件版本、局域网 IP 和当前使用的编码格式；为音箱
填写型号和固件版本。不要添加密码、令牌、DID 或小米源地址。

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

## 7. 配置临时 Home Assistant 路径

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

## 8. 运行并标注两条音箱路径各 30 次试播

每次试播都必须由成年人现场聆听。API 接受请求不等于音箱实际播放并被听见。

```bash
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker run --backend direct --count 30 --interval-seconds 8
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker annotate --run-id DIRECT_RUN_ID --count 30 --missed '2,7'
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker run --backend ha --count 30 --interval-seconds 8
docker compose -f compose.poc.yaml --profile tools run --rm probe speaker annotate --run-id HA_RUN_ID --count 30 --missed ''
```

将示例中的未听见编号替换为实际未听见的试播编号。

## 9. 停止测试失败的音箱路径

一条路径只有同时满足 30/30 次 API 接受和至少 29/30 次现场听见，才通过第一道验收。
不要为失败的路径运行扩展测试。如果两条路径都失败，请保留产物并停止 PoC。

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
