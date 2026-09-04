# 小米音箱 MiService / MIoT / MiNA 文本播报兼容性调查

调查日期：2026-09-04

## 结论

1. **不能把 MIoT 的 `siid=5, aiid=3, in:[text]` 当作所有或大部分小米音箱的固定调用约定。**
   `play-text` 的语义和入参类型在有该能力的官方规格中相当统一，但 `siid` / `aiid` 是产品
   实例内的编号，会随型号和规格版本变化。
2. 对 Xiaomi 前缀且已有 released MIoT speaker 规格的 40 个型号，取每个型号最新 released
   版本后：35 个声明了 `intelligent-speaker / play-text`，5 个没有声明该能力；35 个有能力的
   型号存在四种 `(siid, aiid)`：`(3,1)`、`(5,1)`、`(5,3)`、`(7,3)`。只有 6/40 个型号恰好
   是当前项目硬编码的 `(5,3)`。
3. 若扩大到官方规格库所有品牌的 72 个 released speaker 型号（仍取每个型号最新 released
   版本），只有 44 个声明 `play-text`，并出现第五种 `(5,2)` 映射。这进一步说明设备类型为
   speaker 并不等于支持统一 MIoT TTS 动作。
4. **MiNA 的 `text_to_speech(deviceId, text)` 更适合作为本项目的主要播报路径。**MiService
   3.0.1 对 TTS 统一调用 `/remote/ubus` 的 `mibrain.text_to_speech`，不读取型号、不使用
   `siid/aiid`；型号分支只用于“播放 URL”，不用于文本播报。
5. 因此界面可以列出 MiNA 返回的全部音箱并允许选择，不需要维护“已验证型号白名单”；但
   “出现在 MiNA 列表中”仍不是“本机 TTS 一定可用”的证明。保存前应对选中设备逐台执行短
   文本测试，记录 `available / test_failed / auth_required` 等实际能力状态。

## 证据与方法

### 1. MIoT 的调用地址是实例数据，不是固定型号无关常量

MiService 的 MIoT 实现把调用请求直接组装为
`{"did": ..., "siid": iid[0], "aiid": iid[1], "in": ...}`；它不会根据动作名称自动转换
IID。相反，`miot_spec()` 会先从 Xiaomi 规格索引按 model 找到该型号的 URN，再下载该 URN
的 instance。也就是说，调用方必须提供与**该型号规格实例**相符的地址，而不能只凭
speaker 品类假设一个地址（[MiService `MiIOService` 源码](https://github.com/Yonsm/MiService/blob/main/miservice/miioservice.py#L114-L138)、[规格解析源码](https://github.com/Yonsm/MiService/blob/main/miservice/miioservice.py#L155-L203)）。

Xiaomi 官方 Home Assistant 集成采用同样模型：它从
`https://miot-spec.org/miot-spec-v2/instance?type=<URN>` 下载具体实例；解析 action 时，把
action 的 `iid` 与其所属 service 的 `iid` 分别保留，并根据 action `in` 中的 PIID 找入参
属性。这是“按类型语义定位、使用实例 IID 调用”的直接实现证据，而不是固定数字映射
（[Xiaomi 官方 MIoT parser：下载实例](https://github.com/XiaoMi/ha_xiaomi_home/blob/main/custom_components/xiaomi_home/miot/miot_spec.py#L1294-L1304)、[解析 service/action/IID 与入参](https://github.com/XiaoMi/ha_xiaomi_home/blob/main/custom_components/xiaomi_home/miot/miot_spec.py#L1340-L1358)、[action 解析](https://github.com/XiaoMi/ha_xiaomi_home/blob/main/custom_components/xiaomi_home/miot/miot_spec.py#L1479-L1518)）。

当前项目锁定 `miservice==3.0.1`，却在
[`src/guduck/speaker.py`](../../src/guduck/speaker.py) 中固定发送 `siid=5, aiid=3`；这只与
L05C 的实例一致。

### 2. L05C 验证了当前调用，但不能外推

[L05C 官方 MIoT instance](https://miot-spec.org/miot-spec-v2/instance?type=urn%3Amiot-spec-v2%3Adevice%3Aspeaker%3A0000A015%3Axiaomi-l05c%3A1)
声明：

- `intelligent-speaker` 的 service IID 是 5；
- `play-text` 的 action IID 是 3；
- action 的 `in` 是 `[1]`；该 service 内 PIID 1 是 string 类型 `text-content`。

所以 `{"siid":5,"aiid":3,"in":[text]}` 对 L05C 是规格正确的；它只是一个型号实例的正确
映射。

### 3. released Xiaomi 音箱规格的枚举结果

枚举方法：下载 Xiaomi MIoT 官方
[`instances?status=all`](https://miot-spec.org/miot-spec-v2/instances?status=all) 索引；筛选
`status=released`、model 以 `xiaomi.wifispeaker.` 开头的记录；同 model 有多个 released
版本时取最高 version；逐一下载官方 `instance?type=<URN>`，再按 URN 语义名称
`service:intelligent-speaker` 与 `action:play-text` 匹配，而不是按显示文案或固定 IID 匹配。

截至调查时，40 个型号的结果为：

| `play-text` 映射 | 型号数 | 代表型号及官方 instance |
|---|---:|---|
| `siid=3, aiid=1` | 2 | [`xiaomi.wifispeaker.l09a`](https://miot-spec.org/miot-spec-v2/instance?type=urn%3Amiot-spec-v2%3Adevice%3Aspeaker%3A0000A015%3Axiaomi-l09a%3A1)、[`xiaomi.wifispeaker.x08c`](https://miot-spec.org/miot-spec-v2/instance?type=urn%3Amiot-spec-v2%3Adevice%3Aspeaker%3A0000A015%3Axiaomi-x08c%3A2) |
| `siid=5, aiid=1` | 10 | [`xiaomi.wifispeaker.lx04`](https://miot-spec.org/miot-spec-v2/instance?type=urn%3Amiot-spec-v2%3Adevice%3Aspeaker%3A0000A015%3Axiaomi-lx04%3A2)、[`xiaomi.wifispeaker.l7a`](https://miot-spec.org/miot-spec-v2/instance?type=urn%3Amiot-spec-v2%3Adevice%3Aspeaker%3A0000A015%3Axiaomi-l7a%3A1) |
| `siid=5, aiid=3` | 6 | [`xiaomi.wifispeaker.l05c`](https://miot-spec.org/miot-spec-v2/instance?type=urn%3Amiot-spec-v2%3Adevice%3Aspeaker%3A0000A015%3Axiaomi-l05c%3A1)、[`xiaomi.wifispeaker.l05b`](https://miot-spec.org/miot-spec-v2/instance?type=urn%3Amiot-spec-v2%3Adevice%3Aspeaker%3A0000A015%3Axiaomi-l05b%3A1) |
| `siid=7, aiid=3` | 17 | [`xiaomi.wifispeaker.x10a`](https://miot-spec.org/miot-spec-v2/instance?type=urn%3Amiot-spec-v2%3Adevice%3Aspeaker%3A0000A015%3Axiaomi-x10a%3A2)、[`xiaomi.wifispeaker.x8f`](https://miot-spec.org/miot-spec-v2/instance?type=urn%3Amiot-spec-v2%3Adevice%3Aspeaker%3A0000A015%3Axiaomi-x8f%3A1) |
| 无 `intelligent-speaker / play-text` | 5 | [`xiaomi.wifispeaker.m01a`](https://miot-spec.org/miot-spec-v2/instance?type=urn%3Amiot-spec-v2%3Adevice%3Aspeaker%3A0000A015%3Axiaomi-m01a%3A2)、`m01g`、`m02a`、`m02g`、`m06` |

35 个有 `play-text` 的型号，其 action `in` 都是 `[1]`，且 PIID 1 都是
`property:text-content`、format 为 `string`。因此“文本播报”这一**功能语义和输入形状**在
这些型号间统一；不统一的是实例内的 service/action 地址。可以说 MIoT Spec 提供了透明的
语义描述层，但不能说裸 `siid/aiid` 数字透明。

另外，对所有品牌的 72 个 released speaker 型号做同样分析，44 个具有 `play-text`：
`3/1` 2 个、`5/1` 11 个、`5/2` 1 个、`5/3` 13 个、`7/3` 17 个；28 个没有该动作。
这部分是规格库横截面，不等价于市场在售型号或真实设备成功率，也不能证明没有公开 MIoT
动作的型号无法通过 MiNA TTS。

### 4. MiNA TTS 对型号更透明

项目锁定版本 [MiService v3.0.1 的 `MiNAService`](https://github.com/Yonsm/MiService/blob/v3.0.1/miservice/minaservice.py#L26-L47)
有两层接口：

- `device_list()` 调用 `https://api2.mina.mi.com/admin/v2/device_list`；
- `text_to_speech(deviceId, text)` 对目标设备调用 `/remote/ubus`，固定 method
  `text_to_speech`、path `mibrain`、message `{"text": text}`。

该 TTS 路径不读取 model/hardware，也没有 IID；因此对于已经由 MiNA 返回的设备，它比 MIoT
裸 action 更接近本项目所需的“统一音箱播报 API”。MiService README 也把 MiNA 明确描述为
小爱音箱 TTS 接口，并给出了 `MiNAService.device_list()` 和
`text_to_speech(device_id, text)` 的库调用方式
（[MiService v3.0.1 发布说明/README](https://pypi.org/project/miservice/3.0.1/)）。

需要区分文本 TTS 与 URL 播放：同一份 v3.0.1 源码对 `play_by_url` 明确维护 hardware 集合，
部分设备走 `player_play_music`，其余设备走 `player_play_url`；但这个型号分支**没有用于
`text_to_speech`**（[v3.0.1 URL 播放分支](https://github.com/Yonsm/MiService/blob/v3.0.1/miservice/minaservice.py#L76-L128)）。
这支持“MiNA TTS 较通用”的判断，但并不是 Xiaomi 对所有历史/未来音箱的兼容性承诺。

### 5. MiNA 设备筛选依据

MiService CLI 的 `mina` 列表直接展示 MiNA `device_list()` 返回的每一项的 `deviceID`、
`miotDID`、`name`、`hardware`；设备级命令先用 `miotDID` 对齐用户的 MIoT DID，找不到时才按
name 回退，再取对应 `deviceID` 调 MiNA。它没有 `xiaomi.wifispeaker.*` 型号白名单
（[MiService v3.0.1 CLI 设备解析](https://github.com/Yonsm/MiService/blob/v3.0.1/miservice/__main__.py#L46-L64)、[MiNA 列表和调用路径](https://github.com/Yonsm/MiService/blob/v3.0.1/miservice/__main__.py#L68-L99)）。

源码里 `send_message` 的条件 `devno == -1 or devno == i + 1 or
capabilities.get('yunduantts')` 不能解释为严格的 TTS 能力过滤：CLI 全量播报传 `-1`，指定序号
又会命中 `devno == i + 1`，两种常见路径都不要求 `yunduantts`。所以不应只凭
`capabilities.yunduantts` 隐藏设备；应把 MiNA 列表视为“候选音箱集合”，再用实际
`text_to_speech` 测试确认
（[MiService v3.0.1 `send_message`](https://github.com/Yonsm/MiService/blob/v3.0.1/miservice/minaservice.py#L185-L200)）。

## 对 DaiHouGou 的设计建议

### 推荐路径：MiNA 为主

1. 登录成功后同时取得 `micoapi` 服务凭据，并调用 `MiNAService.device_list()`。
2. UI 列出 MiNA 返回的全部设备，全部允许勾选；展示 `name`、`hardware`、`miotDID`，数据库
   内保存稳定的 `deviceID` 作为 MiNA 调用主键，同时保存 `miotDID`、hardware、最后一次发现
   时间供展示和关联。不要以 name 作为唯一键。
3. 用户保存选中设备前（或保存后立即）逐台执行一条明确的短测试语，例如“音箱配置成功”。
   只有真实调用成功才标记 `available`；失败仍可保存，但显示 `test_failed` 与“重试测试”，
   不要把未知型号写死为不可选。
4. 正式播报调用 `MiNAService.text_to_speech(deviceID, text)`，从而无需为每个型号存储
   `siid/aiid`。Token 过期时统一把账号状态转为 `auth_required`，由界面重新授权。
5. 设备重新发现时按 `deviceID` 更新元数据；如果同一条记录的 `miotDID` 变化，保留旧值用于
   审计，不要静默按 name 重新绑定摄像头。

### 若保留 MIoT 后备路径

不得使用硬编码 `(5,3)`，也不建议维护静态型号映射表。应：

1. 用设备 list 返回的 model 查官方 instances 索引，选择该 model 的 released 规格（最高
   version）；
2. 下载 instance 后按 URN 语义匹配 `service:intelligent-speaker` 和
   `action:play-text`；
3. 校验 action 的唯一入参指向 string `property:text-content`；
4. 仅在上述校验成功后，用解析到的 `(service.iid, action.iid)` 调用并缓存该解析结果；缓存键
   应包含 model 与 spec URN/version；
5. 没有匹配动作时标记 `miot_tts_unavailable`，不能退回 `5/3` 猜测。

### 产品文案边界

界面可以说“已发现的小爱音箱”或“MiNA 返回的音箱”，不宜承诺“所有小米音箱都兼容”。
准确说法是：**不限制型号，所有发现设备均可选；以配置时的实际试听结果确认兼容性。**

## 局限

- 官方 MIoT 规格只能证明设备声明了哪些能力及 IID，不能代替真机/固件版本验证。
- MiNA 是 MiService 对 Xiaomi 私有云 API 的实现；这里找不到 Xiaomi 面向第三方开发者的
  “所有小爱音箱均支持 `mibrain.text_to_speech`”兼容性承诺。因此结论是“实现上更通用、适合
  动态发现加实测”，不是“理论上保证全型号”。
- 枚举统计是 2026-09-04 的官方规格库快照；新增型号或新的 released 规格会改变数字，故运行
  时解析优于把本文表格固化进代码。
