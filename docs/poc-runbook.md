# GuDuck 部署与小米音箱验收手册

本文用于目标服务器上的部署验收。小米账号登录和真实音箱播报必须由用户在浏览器中手动验证；自动化测试不接触真实账号。

## 1. 验收边界

本次验收覆盖：

- 应用在没有小米账号环境配置时正常启动；
- 设置页完成账号授权及验证码流程；
- 页面使用 AJAX 轮询，不发生整页刷新；
- MiNA 返回的全部音箱出现在列表中并可选择；
- 绑定、显示名称和测试状态写入数据库；
- 授权 token 过期后可在同一页面重新授权；
- 取消摄像头正在使用的音箱时先提示，确认保存后摄像头标记音箱不可用；
- 不可用期间保留规则启用意图，恢复同一绑定后自动继续。

管理界面无独立登录，仅允许从可信局域网访问，禁止直接发布到公网。

## 2. 部署准备

```bash
mkdir -p deploy/go2rtc/state deploy/app/state deploy/app/models
chmod 700 deploy/app/state
cp deploy/go2rtc/go2rtc.example.yaml deploy/go2rtc/state/go2rtc.yaml
cp .env.example .env
```

在 `.env` 中设置服务器局域网监听地址。在 `deploy/go2rtc/state/go2rtc.yaml` 中配置摄像头流。配置文件不得提交到版本库。

如果服务器上已经运行 app，先用当前编排停止应用服务：

```bash
docker compose stop app
```

当前版本启动命令：

```bash
docker compose up -d --build go2rtc app
docker compose ps
docker compose logs --tail=100 app
```

确认 app 健康后打开 `http://SERVER_LAN_IP:8080/settings`。

## 3. 小米授权验收

1. 输入真实小米账号和密码，点击授权。
2. 观察账号状态在页面内变化；浏览器地址和其他页面内容不应刷新。
3. 若出现验证码要求，在页面内提交正确验证码。
4. 授权成功后刷新设备列表。
5. 核对账号下的音箱均已列出；设备底层标识不应出现在页面或网络响应中。

密码和验证码仅在当前授权流程的内存中存在。关闭、取消或完成流程后，页面不应回填密码。数据库只持久化授权 token；日志、事件和 API 响应不得包含 token、密码、验证码或认证库异常原文。

## 4. 音箱绑定验收

1. 勾选至少两台音箱并设置容易识别的显示名称。
2. 分别手动点击测试，确认界面只显示“测试成功”或“测试失败”。
3. 无论测试结果如何都应允许保存。
4. 刷新浏览器，确认之前绑定的设备默认勾选、显示名称保留。
5. 重启 app，确认绑定仍然存在，且启动过程不依赖重新获取完整设备列表。

设备列表刷新失败时，既有列表和可用状态应保持原快照，不应清空。MiNA 暂时不返回既有绑定时，该绑定仍保留但标记不可用。

## 5. 摄像头联动验收

1. 新发现的摄像头应显示未绑定音箱，直接启用规则应被拒绝。
2. 为摄像头选择当前可用的绑定音箱，再启用人员进入欢迎规则。
3. 回到设置页，取消勾选该音箱并保存。
4. 第一次保存应列出受影响摄像头并要求确认。
5. 确认保存后应成功；摄像头继续显示规则启用，但标记音箱不可用，检测运行暂停。
6. 重新绑定同一设备或给摄像头选择其他可用音箱，确认运行恢复。

同样验证物体类别播报规则。不可用期间不应积压旧播报，恢复后只处理新的检测结果。

## 6. token 过期与重新授权

在可控测试环境中使当前授权失效，然后触发设备刷新或一次播报：

- 页面应进入需要重新授权状态；
- 相关摄像头暂停，不改变规则启用选择；
- 重新在设置页完成登录后，既有绑定继续使用；
- 同一绑定恢复可用后摄像头自动恢复。

本版本不迁移旧账号配置或外部 token 文件。旧部署升级后直接通过设置页重新授权。

## 7. 权限与备份

检查状态目录和数据库文件：

```bash
stat -f '%Lp %N' deploy/app/state deploy/app/state/guduck.db
```

Linux 可使用：

```bash
stat -c '%a %n' deploy/app/state deploy/app/state/guduck.db
```

预期目录为 `700`、数据库为 `600`。备份包含小米授权 token，应当按敏感凭据保护。

一致性备份：

```bash
docker compose stop app
cp -a deploy/app/state deploy/app/state.backup
docker compose start app
```

## 8. 自动化回归

在开发机运行：

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests
node --test tests/js/region-editor.test.js
git diff --check
```

自动化通过不能替代第 3 至第 6 节的真实账号与真实音箱验收。

## 9. 可选摄像头与 Home Assistant 探针

PoC 工具保留摄像头稳定性和 Home Assistant 音箱路径，用于独立诊断。它不再提供小米账号直连入口。

```bash
cp .env.poc.example .env.poc
docker compose --profile tools run --rm probe camera decode \
  --stream nursery --duration-seconds 1800
docker compose --profile tools run --rm probe speaker run \
  --backend ha --count 3 --interval-seconds 8
```

探针产物写入 `artifacts/`，不要在其中记录账号凭据、摄像头 URL 或授权 token。
