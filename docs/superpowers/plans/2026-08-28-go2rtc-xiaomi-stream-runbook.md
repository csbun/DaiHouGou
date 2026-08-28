# go2rtc 小米摄像头码流 Runbook Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 go2rtc 小米摄像头码流配置从一句概述扩展为可执行、可验证且不泄露设备凭据的中文操作说明。

**Architecture:** 只修改 `docs/poc-runbook.md` 第 3 节。保留已有启动、健康检查和 SSH 隧道步骤，从小米账号已经登录且设备列表已经显示的状态开始，依次说明设备识别、固定命名、质量降级测试、预览、API/RTSP 验证和配置脱敏检查。

**Tech Stack:** Markdown、go2rtc 1.9.14 Web UI、HTTP API、RTSP、Shell 校验命令。

---

### Task 1: 展开两台小米摄像头的码流配置步骤

**Files:**
- Modify: `docs/poc-runbook.md:72-74`

- [ ] **Step 1: 记录修改前的可验证边界**

运行：

```bash
sed -n '66,76p' docs/poc-runbook.md
```

预期：输出只包含“添加小米账号”“命名为 `xiaobai`/`xiaobai_25k`”“尝试 `subtype=sd`”和“检查 `/api/streams`”的简略描述，没有逐步操作、降级顺序或 RTSP 验证命令。

- [ ] **Step 2: 用完整操作流程替换简略描述**

在 `docs/poc-runbook.md` 第 3 节加入以下内容：

````markdown
小米账号登录成功并显示摄像头列表后，按以下步骤逐台添加。不要依据设备列表顺序判断型号；
应同时核对米家中的设备名称、型号和 go2rtc 预览画面，避免把两台摄像头的固定名称写反。

1. 选择“小白智能摄像机”，进入添加流的表单，将流名称改为 `xiaobai`。
2. 保留 Web UI 自动生成的 `xiaomi://` 源地址，不要手工重写其中的账号、地区、IP、DID 或
   `model` 参数。在地址末尾添加 `&subtype=sd`；如果地址中还没有 `?`，使用
   `?subtype=sd`。保存后返回 Streams 页面并打开预览。
3. 选择“小白智能摄像机 2.5K 版”，按相同方式将流名称改为 `xiaobai_25k`，先使用
   `subtype=sd` 保存并预览。
4. 每路预览至少观察 30 秒。画面应持续更新，不能停在首帧，也不能持续显示连接错误。

如果某一路使用 `sd` 无法显示，依次尝试 `auto`、`1`、`2`。每次只修改该路源地址中的一个
`subtype` 值，保存后重新打开预览；找到最低且能连续显示的值后停止，不要因为高画质可用就
优先选择高码率。把两路最终采用的 `subtype` 值记入本次 PoC 的私人操作记录，但不要记录
完整 `xiaomi://` 地址。

Web UI 保存后的配置结构应类似下面的脱敏示例：

```yaml
xiaomi:
  "<ACCOUNT_ID>": "<REDACTED_TOKEN>"
streams:
  xiaobai:
    - xiaomi://<REDACTED>?did=<REDACTED>&model=<MODEL>&subtype=sd
  xiaobai_25k:
    - xiaomi://<REDACTED>?did=<REDACTED>&model=<MODEL>&subtype=sd
```

实际文件中的账号标识、令牌、DID、局域网 IP 和完整 `xiaomi://` 地址属于家庭隐私数据，
不得提交到 Git，也不要粘贴到 issue 或 PoC 报告中。

在服务器上确认两个固定名称已注册：

```bash
curl --fail --silent --show-error http://127.0.0.1:1984/api/streams
```

输出必须同时包含 `xiaobai` 和 `xiaobai_25k`。然后分别验证 go2rtc 的 RTSP 输出能被读取：

```bash
docker compose -f compose.poc.yaml --profile tools run --rm --entrypoint ffprobe probe \
  -v error -show_entries stream=index,codec_name,codec_type \
  -of json rtsp://127.0.0.1:8554/xiaobai
docker compose -f compose.poc.yaml --profile tools run --rm --entrypoint ffprobe probe \
  -v error -show_entries stream=index,codec_name,codec_type \
  -of json rtsp://127.0.0.1:8554/xiaobai_25k
```

两条命令都必须退出为 `0`，并至少返回一条 `codec_type` 为 `video` 的流。如果名称缺失，回到
Web UI 检查流名称是否完全一致；如果名称存在但没有视频，查看 go2rtc 日志并继续尝试该路的
下一个 `subtype`，不要进入第 4 步。
````

- [ ] **Step 3: 检查 Markdown、敏感值和差异**

运行：

```bash
awk '/^```/{n++} END{print "code_fence_count=" n; exit(n % 2)}' docs/poc-runbook.md
git diff --check
rg -n 'xiaomi://[A-Za-z0-9]|V1:|did=[0-9]|@(10\.|192\.168\.|172\.)' docs/poc-runbook.md
git diff -- docs/poc-runbook.md
```

预期：代码围栏数量为偶数，`git diff --check` 成功，敏感值扫描没有匹配，差异只展开第 3 节的摄像头配置说明。

- [ ] **Step 4: 提交 Runbook 修改**

```bash
git add docs/poc-runbook.md docs/superpowers/plans/2026-08-28-go2rtc-xiaomi-stream-runbook.md
git commit -m "docs: explain Xiaomi camera stream setup"
```
