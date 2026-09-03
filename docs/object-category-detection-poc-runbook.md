# Objects365 通用物体检测 PoC 运行手册

本手册验收“画面明显变化后，用固定词表检测绘本中的物体并播报英文类别”。人员进入检测属于
已有 GuDuck 正式应用，不是本 PoC 的前置条件。默认测试只加载物体模型；人员模型仅用于最后的可选联调。

Objects365 YOLO26n 是已经训练好的 365 类模型，不需要先训练，也不需要下载完整 Objects365
数据集。它不是开放世界识别：输入只有画面，不需要提示词，但输出仍限定在模型的 365 类中。
工程统一排除 `person`，因此可播报 364 类。

## 1. 验收路径

1. 在开发机准备至少 30 页私有绘本语料并导出 416x416 ONNX。
2. 在开发机运行同一 CLI，迭代 manifest 和置信度效果。
3. 将语料副本和 ONNX 传到 i3-3217U 目标机，执行最终准确率、p95 和 RSS 门禁。
4. 目标机通过后，把 ONNX 放入 Compose 的外部模型目录，并在管理页选择 Objects365。

开发机的准确率结果可用于迭代，但耗时和内存不能代替目标机结论。不要把绘本图片、完整
manifest、人物或家庭场景截图提交 Git、上传到第三方服务或写入报告。CLI 只输出聚合指标。

## 2. 准备私有语料

CLI 只接受仓库外的固定目录：

```text
/tmp/guduck-object-validation/
  manifest.json
  page-001.jpg
  page-002.jpg
  ...
```

语料至少 30 页，其中至少 20 页设置非空 `primary`。建议包含单物体、多物体、重复类别、
不同光线和没有可播报物体的页面，不要重复同一张图片凑数。manifest 示例：

```json
{
  "pages": [
    {
      "file": "page-001.jpg",
      "primary": "rabbit",
      "expected": ["rabbit", "book"]
    },
    {
      "file": "page-002.jpg",
      "primary": null,
      "expected": []
    }
  ]
}
```

`file` 必须是相对路径，`primary` 必须同时出现在 `expected`。Objects365 测试中的标签必须
来自 `OBJECTS365_CATEGORIES`；`person` 虽在模型词表内，但统一策略不会播报。路径穿越、
绝对路径、软链接逃逸、缺失图片、重复文件名和词表外标签都会被拒绝。

只校验 manifest，不加载模型：

```bash
.venv/bin/python -c '
from pathlib import Path
from tools.object_detection_poc import load_manifest
from guduck.vision.objects365_detector import OBJECTS365_CATEGORIES
pages = load_manifest(
    Path("/tmp/guduck-object-validation/manifest.json"),
    categories=OBJECTS365_CATEGORIES,
    vocabulary_name="Objects365",
)
print(f"pages={len(pages)} primary={sum(p.primary is not None for p in pages)}")
'
```

输出至少应为 `pages=30 primary=20`。

## 3. 在开发机导出模型

导出使用固定版本 `ultralytics==8.4.138`，只安装在 `/tmp` 的一次性环境中。目标机和主应用
不安装 PyTorch 或 Ultralytics。官方 checkpoint 下载地址与本次校验值：

```text
URL: https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-objv1-150.pt
SHA384: 67104718c37bd2277a98390bcf5bf841d36de3db8b92abadb40f4db05e3710433ce8145d62aa6eda373fa79399b506f9
```

在仓库根目录执行：

```bash
python3 -m venv /tmp/guduck-objects365-export
/tmp/guduck-objects365-export/bin/pip install \
  ultralytics==8.4.138 onnx==1.19.1
curl --fail --show-error --location \
  https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-objv1-150.pt \
  --output /tmp/yolo26n-objv1-150.pt
printf '%s  %s\n' \
  67104718c37bd2277a98390bcf5bf841d36de3db8b92abadb40f4db05e3710433ce8145d62aa6eda373fa79399b506f9 \
  /tmp/yolo26n-objv1-150.pt | shasum -a 384 -c -
/tmp/guduck-objects365-export/bin/python \
  tools/export_objects365_model.py \
  --checkpoint /tmp/yolo26n-objv1-150.pt \
  --output /tmp/object_detection_objects365_yolo26n_416.onnx
```

导出脚本固定 `imgsz=416`、FP32、静态输入、opset 17 和 `end2end=False`。最后一项很重要：
OpenCV DNN 使用 one-to-many 网格输出，不能直接使用 YOLO26 的 end-to-end 图。本次已验证的
输出形状为 `[1, 369, 3549]`，文件约 9.8 MB。

模型与 Ultralytics 工具存在独立许可条件。PoC 文件不提交 Git、不打进应用镜像；在分发或
商用前由项目负责人确认适用许可。

## 4. 开发机运行 Objects365

这一步不需要人员模型或摄像头：

```bash
.venv/bin/python tools/object_detection_poc.py \
  --adapter objects365 \
  --corpus /tmp/guduck-object-validation \
  --object-model /tmp/object_detection_objects365_yolo26n_416.onnx \
  --output /tmp/object-category-detection-local.json
```

CLI 会执行与生产相同的统一策略：排除 `person`、过滤占画面至少一半的大面积 `book`、同类
去重、按置信度排序并最多保留三个英文标签。报告中的 `adapter` 应为 `objects365`。

退出码：

- `0`：全部门禁通过。
- `1`：完成有效测量，但至少一个门禁失败。
- `2`：manifest、图片、模型或参数无效，不构成一次有效测量。

## 5. 在 i3 目标机做最终门禁

通过组织批准的加密通道，将语料副本放到目标机同一路径，并把 ONNX 放到 `/tmp`。在目标机
重新计算 SHA384，与开发机输出记录比对，然后执行：

```bash
python tools/object_detection_poc.py \
  --adapter objects365 \
  --corpus /tmp/guduck-object-validation \
  --object-model /tmp/object_detection_objects365_yolo26n_416.onnx \
  --output /tmp/object-category-detection-target.json
```

门禁固定为：`primary_accuracy >= 0.80`、`false_announcement_ratio < 0.05`、物体推理 p95
`<= 1000 ms`、峰值 RSS `<= 1 GiB`。只有目标机的这次运行可以确认 i3-3217U 是否可用。

## 6. 集成主应用

目标机门禁通过后，在仓库根目录执行：

```bash
mkdir -p deploy/app/models
cp /tmp/object_detection_objects365_yolo26n_416.onnx \
  deploy/app/models/object_detection_objects365_yolo26n_416.onnx
chmod 0444 deploy/app/models/object_detection_objects365_yolo26n_416.onnx
```

在 `.env` 中设置模型路径：

```dotenv
OBJECTS365_MODEL=/opt/guduck/models/custom/object_detection_objects365_yolo26n_416.onnx
```

Compose 会把 `deploy/app/models` 只读挂载到容器。模型目录同时被 Git 和 Docker build context
排除，不会进入提交或镜像。重建并重启应用：

```bash
docker compose build app
docker compose up -d app
docker compose logs --tail=100 app
```

打开“设置”页，在“物体检测器”中选择 `Objects365 YOLO26n` 并应用。选择是全局配置，对所有
摄像头生效并持久化到 SQLite；应用重启后无需重新选择。切换前会先加载新模型，加载成功后
再替换当前检测器；失败时页面显示错误、继续使用原检测器，并且不保存失败的选择。未部署
对应模型文件时，该选项显示为不可用。新数据库默认选择 `NanoDet COCO`。

## 7. 可选的双模型回归

仅在需要确认新旧规则共存资源预算时，显式带入已有人员模型：

```bash
python tools/object_detection_poc.py \
  --adapter objects365 \
  --corpus /tmp/guduck-object-validation \
  --object-model /tmp/object_detection_objects365_yolo26n_416.onnx \
  --person-model /opt/guduck/models/person_detection_mediapipe_2023mar.onnx \
  --camera-count 2 \
  --output /tmp/object-category-detection-combined.json
```

该模式额外检查双摄等效周期 p95 `<= 1000 ms`。它不是 Objects365 初始化步骤，也不能替代
物品单模型门禁。

## 8. 清理

记录脱敏聚合指标后，删除临时副本；不要删除用户原始绘本：

```bash
rm -f /tmp/yolo26n-objv1-150.pt
rm -f /tmp/object_detection_objects365_yolo26n_416.onnx
rm -f /tmp/object-category-detection-local.json
rm -f /tmp/object-category-detection-target.json
rm -f /tmp/object-category-detection-combined.json
rm -rf /tmp/guduck-object-validation
rm -rf /tmp/guduck-objects365-export
```

若准确率不足，后续可以针对绘本域微调或重新选择类别范围；这属于提升识别率的独立实验，
不是运行预训练 Objects365 模型的必要初始化操作。
