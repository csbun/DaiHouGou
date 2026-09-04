# GuDuck

GuDuck 是一个运行在家庭局域网内的摄像头事件与音箱播报应用。它从 go2rtc 读取实时画面，在本机完成检测，并通过小米 MiNA `text_to_speech` 向用户在界面中绑定的音箱播报。

当前功能包括：

- 自动发现 go2rtc 摄像头流；
- 按摄像头配置“人员进入欢迎”和“物体类别播报”规则；
- 在管理界面完成小米账号授权、验证码提交和重新授权；
- 列出账号下 MiNA 返回的全部音箱，允许选择任意多个作为绑定设备；
- 手动测试每台已选择音箱，并用“测试成功”或“测试失败”显示结果；
- 音箱暂时不可用或授权过期时暂停相关摄像头规则，恢复后自动继续。

## 安全边界

管理界面没有独立的应用登录，只应暴露在可信家庭局域网，不能直接映射到公网。摄像头账号、RTSP 地址及其他秘密应保留在本机的 go2rtc 配置中。

小米账号密码和验证码只在一次授权流程的内存中使用，不保存到配置文件或数据库。授权 token、音箱绑定和摄像头配置保存在应用 SQLite 数据库中。界面和 API 不展示底层设备标识、token 或认证异常原文。

应用数据库目录应为 `0700`，数据库文件应为 `0600`。部署前执行：

```bash
mkdir -p deploy/go2rtc/state deploy/app/state deploy/app/models
chmod 700 deploy/app/state
```

## 部署

要求：

- Docker Engine 与 Docker Compose v2；
- 摄像头和目标主机处于可访问的局域网；
- 目标主机可访问小米服务；
- 应用使用单进程运行，以保证授权状态、任务队列和设备工作线程的一致性。

复制示例配置：

```bash
cp deploy/go2rtc/go2rtc.example.yaml deploy/go2rtc/state/go2rtc.yaml
cp .env.example .env
```

在 `deploy/go2rtc/state/go2rtc.yaml` 中加入摄像头流。例如：

```yaml
streams:
  nursery: rtsp://camera-user:camera-password@192.168.1.20/live
```

编辑 `.env`，至少把 `WEB_HOST` 设置为服务器的局域网地址。其他检测参数可沿用默认值。然后启动：

```bash
docker compose up -d --build go2rtc app
docker compose ps
docker compose logs --tail=100 app
```

浏览器打开：

```text
http://SERVER_LAN_IP:8080/settings
```

## 小米账号与音箱绑定

首次使用时，在设置页完成以下操作：

1. 输入小米账号和密码并开始授权；页面会每秒通过 AJAX 查询状态，不会整页刷新。
2. 如果小米要求验证码，在当前页面输入验证码并提交。
3. 授权成功后刷新设备列表。列表以 MiNA 返回结果为准，全部音箱均可选择。
4. 勾选要绑定的音箱，可修改界面显示名称；之前已绑定的设备默认保持勾选。
5. 可手动点击“测试”。测试结果不阻止保存。
6. 保存选择。

如果取消勾选的音箱正在被摄像头使用，界面会先列出受影响摄像头。确认后仍可保存，这些摄像头会保留原绑定引用和规则启用意图，但显示音箱不可用并暂停运行。再次勾选同一音箱，或为摄像头改绑其他可用音箱后会恢复。

MiNA 暂时没有返回某台既有绑定设备时，应用会保留该绑定记录并标记不可用，不会因为一次刷新失败而删除。授权 token 过期后，设置页会提示重新授权；重新授权成功后绑定和规则继续使用。

本版本不导入旧的账号配置或外部 token 文件。升级后直接在设置页重新授权并选择音箱。

## 摄像头与规则

打开首页后，应用会从 go2rtc 自动发现流。新摄像头默认没有音箱绑定，必须先在设置页选择一台当前可用的音箱，再启用规则。

规则启用后，如果对应音箱离线、未绑定或需要重新授权：

- 摄像头规则的启用选择保持不变；
- 检测运行会暂停，不会继续积压播报；
- 同一绑定恢复可用后自动恢复运行。

摄像头快照仅用于当前界面的即时预览和本地检测，不作为运行时图片档案保存。

## 模型

默认人员检测和 NanoDet 模型随应用镜像路径配置。可选的 Objects365 模型安装到 `deploy/app/models`：

```bash
./scripts/install-objects365.sh
```

模型切换在管理界面完成。新模型只有在加载成功后才会成为当前选择；失败时保留原选择。

## 数据备份与恢复

SQLite 数据库是小米授权 token、音箱绑定、摄像头设置和事件记录的唯一生产持久化来源。备份前短暂停止应用，复制整个状态目录，再启动：

```bash
docker compose stop app
cp -a deploy/app/state deploy/app/state.backup
docker compose start app
```

恢复时同样先停止应用，用可信备份替换状态目录，并重新检查目录和文件权限。备份包含授权 token，应按密码材料保护。

## 开发与验证

创建开发环境后运行：

```bash
.venv/bin/pytest -q
.venv/bin/ruff check src tests
node --test tests/js/region-editor.test.js
```

真实小米账号登录和音箱试听需要在部署环境中手动完成。自动化测试使用假的账号与 MiNA 服务，不会向真实设备发送播报。

## 许可证

见 [LICENSE](LICENSE)。
