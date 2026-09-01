# 通用物体识别 PoC 运行手册

本手册只验收“画面变化后识别并播报通用物体”的候选能力。人员进入检测已经属于上一个
MVP，不是本 PoC 的初始化条件。CLI 默认只加载 NanoDet 物体模型；人员模型和双摄等效负载
测试属于可选的联调回归。

## 1. 先看结论

测试集可以在开发机完成准备和日常操作，包括：从绘本中截取页面、人工标注、编写
`manifest.json`、检查路径和类别，以及用本机迭代识别准确率。开发机上的耗时和内存不能作为
目标机门禁，因为目标机是 i3-3217U、4 GiB、CPU-only，硬件和 OpenCV 版本都会影响结果。

因此分成两段：

1. 在开发机准备并反复验证私有语料，得到可复现的 manifest 和模型文件。
2. 将经批准的语料副本和模型放到目标机，在目标机执行最终的物体准确率、物体推理 p95、
   峰值 RSS，以及可选的双摄人员/物体周期门禁。

不要把绘本图片、完整 manifest、人物或家庭场景截图提交 Git、上传到第三方服务或放进
报告。CLI 只输出聚合指标，不保存标注图。

## 2. 目录和语料要求

CLI 只接受固定的私有目录：

```text
/tmp/daihougou-object-validation/
  manifest.json
  page-001.jpg
  page-002.jpg
  ...
```

目录必须在仓库外。manifest 的最小格式如下，`file` 相对于 manifest 所在目录：

```json
{
  "pages": [
    {
      "file": "page-001.jpg",
      "primary": "cat",
      "expected": ["cat", "book"]
    },
    {
      "file": "page-002.jpg",
      "primary": null,
      "expected": []
    }
  ]
}
```

语料至少 30 页，其中至少 20 页设置非空 `primary`。`primary` 和 `expected` 只能使用
NanoDet COCO 80 类中的标签；`primary` 必须同时出现在 `expected`。建议覆盖单物体、多物体、
重复类别、不同光线和没有可播报物体的页面。不要为了凑页数重复同一张图片。

在开发机准备副本并检查数量：

```bash
mkdir -p /tmp/daihougou-object-validation
# 将已获授权的页面图片和 manifest 放入上面的目录；不要把原始绘本移走
test -f /tmp/daihougou-object-validation/manifest.json
test "$(realpath /tmp/daihougou-object-validation)" = /tmp/daihougou-object-validation
```

用 CLI 的纯校验函数检查 manifest。这个步骤不会加载模型，也不会读入所有图片像素：

```bash
.venv/bin/python -c '
from pathlib import Path
from tools.object_detection_poc import load_manifest
pages = load_manifest(Path("/tmp/daihougou-object-validation/manifest.json"))
print(f"pages={len(pages)} primary={sum(p.primary is not None for p in pages)}")
'
```

输出应至少为 `pages=30 primary=20`。路径穿越、绝对路径、软链接逃逸、缺失图片、重复文件
名和不支持类别都会被拒绝。

## 3. 模型下载和校验

NanoDet 使用 OpenCV Zoo 固定提交的预训练 COCO 权重，不需要训练才能初始化或运行。当前
候选文件和 SHA384 如下：

```text
URL: https://media.githubusercontent.com/media/opencv/opencv_zoo/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_nanodet/object_detection_nanodet_2022nov.onnx
SHA384: 84ee6a6dd605f7019f25a81615a8fff886b235e8d3924930ca367c6e239a8c6d9c14a7e60b8bae54edca040cbf7b86e7
```

在需要运行的每台机器单独下载并校验。Linux 使用：

```bash
curl --fail --show-error --location \
  https://media.githubusercontent.com/media/opencv/opencv_zoo/47534e27c9851bb1128ccc0102f1145e27f23f98/models/object_detection_nanodet/object_detection_nanodet_2022nov.onnx \
  --output /tmp/object_detection_nanodet_2022nov.onnx
printf '%s  %s\n' \
  84ee6a6dd605f7019f25a81615a8fff886b235e8d3924930ca367c6e239a8c6d9c14a7e60b8bae54edca040cbf7b86e7 \
  /tmp/object_detection_nanodet_2022nov.onnx | sha384sum --check
```

macOS 没有 `sha384sum` 时使用：

```bash
echo '84ee6a6dd605f7019f25a81615a8fff886b235e8d3924930ca367c6e239a8c6d9c14a7e60b8bae54edca040cbf7b86e7  /tmp/object_detection_nanodet_2022nov.onnx' \
  | shasum -a 384 -c -
```

校验失败时删除该模型并重新下载，不要继续测试。人员模型只在第 5 节的可选联调中使用，
沿用上一个 MVP 已验证的 `/opt/daihougou/models/person_detection_mediapipe_2023mar.onnx`。

## 4. 开发机物品单模型验证

这是默认模式，不需要人员模型，也不需要摄像头。`--camera-count` 默认是 `1`；省略
`--person-model` 时，CLI 不会构造或预热人员检测器，不会运行人员/物体周期，并且报告中不
包含 `cycle_p95_ms`。

```bash
.venv/bin/python tools/object_detection_poc.py \
  --corpus /tmp/daihougou-object-validation \
  --object-model /tmp/object_detection_nanodet_2022nov.onnx \
  --output /tmp/object-category-detection-local.json
```

退出码含义：

- `0`：物体准确率、误播报比例、物体推理 p95 和峰值 RSS 均通过。
- `1`：输入有效且完成测量，但至少一个门禁失败；读取 JSON 中的聚合指标定位问题。
- `2`：输入无效，例如 manifest、图片、模型缺失，或参数组合非法；这不是一次有效测量。

开发机的 `primary_accuracy` 和 `false_announcement_ratio` 可用于快速发现标注、类别或
阈值问题。开发机的 `object_p95_ms` 和 `peak_rss_bytes` 只能做趋势参考，不能替代目标机
结论。报告不会包含页面文件名、路径、预测标签或图片。

## 5. 目标机最终门禁

目标机必须是计划中的 i3-3217U、4 GiB、CPU-only Debian 环境。将语料副本放到目标机的
同一路径 `/tmp/daihougou-object-validation`，模型也在目标机完成 SHA384 校验。传输绘本
前先确认数据授权，使用组织批准的加密通道；不要把账号、令牌、DID 或完整家庭画面写入
命令历史、issue 或报告。

在目标机运行与开发机相同的物品单模型命令：

```bash
python tools/object_detection_poc.py \
  --corpus /tmp/daihougou-object-validation \
  --object-model /tmp/object_detection_nanodet_2022nov.onnx \
  --output /tmp/object-category-detection-target.json
```

只有这次目标机运行才能决定 CPU 推理 p95 和进程峰值 RSS 是否通过。若物品单模型门禁失败，
停止生产实现，回到模型或类别范围选择。

## 6. 可选的人员/物体联调回归

这不是通用物体识别的初始化步骤，只用于确认新旧规则在同一进程、双摄等效负载下仍满足资源
预算。必须显式提供上一个 MVP 的人员模型，并显式设置双摄：

```bash
python tools/object_detection_poc.py \
  --corpus /tmp/daihougou-object-validation \
  --object-model /tmp/object_detection_nanodet_2022nov.onnx \
  --person-model /opt/daihougou/models/person_detection_mediapipe_2023mar.onnx \
  --camera-count 2 \
  --output /tmp/object-category-detection-combined.json
```

该模式额外输出 `cycle_p95_ms`，并额外检查双模型峰值 RSS 和双摄周期 p95。没有人员模型时
传入 `--camera-count 2` 会直接返回退出码 `2`，不会偷偷退化成单模型测试。`--camera-count 1`
可用于单路联调，但不能声称通过双摄门禁。

## 7. 通过条件和清理

门禁阈值固定为：`primary_accuracy >= 0.80`、`false_announcement_ratio < 0.05`、物体
推理 p95 `<= 1000 ms`、峰值 RSS `<= 1 GiB`。只有可选双摄联调才检查周期 p95 `<= 1000 ms`。

在确认聚合 JSON 已审阅并且需要保留的指标已经抄入脱敏报告后，删除临时副本和下载文件；不
要删除用户原始绘本：

```bash
rm -f /tmp/object_detection_nanodet_2022nov.onnx
rm -f /tmp/object-category-detection-local.json
rm -f /tmp/object-category-detection-target.json
rm -f /tmp/object-category-detection-combined.json
rm -rf /tmp/daihougou-object-validation
```

如果准确率不足，后续可以针对绘本域做增量标注、类别取舍或 NanoDet 微调。那是提升识别率
的后续实验，不是本 PoC 的必要初始化训练，也不能用来替代当前固定预训练模型的基线记录。
