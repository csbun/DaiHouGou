#!/usr/bin/env bash
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly CHECKPOINT_URL="https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-objv1-150.pt"
readonly CHECKPOINT_SHA384="67104718c37bd2277a98390bcf5bf841d36de3db8b92abadb40f4db05e3710433ce8145d62aa6eda373fa79399b506f9"
readonly CHECKPOINT_FILENAME="yolo26n-objv1-150.pt"
readonly MODEL_FILENAME="object_detection_objects365_yolo26n_416.onnx"
readonly CONTAINER_MODEL_PATH="/opt/guduck/models/custom/$MODEL_FILENAME"
readonly MODELS_DIR="$REPO_ROOT/deploy/app/models"
readonly MODEL_PATH="$MODELS_DIR/$MODEL_FILENAME"
readonly ENV_PATH="$REPO_ROOT/.env"

install_tmp_root="${TMPDIR:-/tmp}"
work_dir=""
backup_path=""
staged_path=""
env_tmp=""

die() {
  printf 'Objects365 安装失败：%s\n' "$1" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "缺少命令 $1，请先安装后重试"
}

cleanup() {
  if [[ -n "$staged_path" ]]; then
    rm -f -- "$staged_path"
  fi
  if [[ -n "$env_tmp" ]]; then
    rm -f -- "$env_tmp"
  fi
  if [[ -n "$work_dir" ]]; then
    rm -rf -- "$work_dir"
  fi
}
trap cleanup EXIT

require_command curl
require_command install
require_command python3
require_command sha384sum
require_command docker

docker compose version >/dev/null 2>&1 || die "Docker Compose v2 不可用"
docker info >/dev/null 2>&1 || die "无法连接 Docker daemon，请确认 Docker 已启动且当前用户有权限"
python3 -m venv --help >/dev/null 2>&1 || die "当前 Python 缺少 venv 模块，请安装 python3-venv"

cd -- "$REPO_ROOT"
[[ -f tools/export_objects365_model.py ]] || die "找不到 tools/export_objects365_model.py"
[[ -f .env.example ]] || die "找不到 .env.example"

work_dir="$(mktemp -d "$install_tmp_root/guduck-objects365.XXXXXX")"
readonly venv_dir="$work_dir/venv"
readonly checkpoint_path="$work_dir/$CHECKPOINT_FILENAME"
readonly exported_path="$work_dir/$MODEL_FILENAME"

printf '%s\n' '正在下载官方 Objects365 YOLO26n checkpoint...'
curl --fail --show-error --location --retry 3 --retry-delay 2 \
  --output "$checkpoint_path" "$CHECKPOINT_URL"

printf '%s  %s\n' "$CHECKPOINT_SHA384" "$checkpoint_path" |
  sha384sum --check --status || die "checkpoint SHA384 校验失败"

printf '%s\n' '正在创建临时导出环境并安装锁定依赖...'
python3 -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install \
  --disable-pip-version-check \
  'ultralytics==8.4.138' \
  'onnx==1.19.1'

printf '%s\n' '正在导出 416x416 静态 ONNX...'
"$venv_dir/bin/python" tools/export_objects365_model.py \
  --checkpoint "$checkpoint_path" \
  --output "$exported_path"
[[ -s "$exported_path" ]] || die "导出结果为空"

mkdir -p -- "$MODELS_DIR"
if [[ -e "$MODEL_PATH" && ! -f "$MODEL_PATH" ]]; then
  die "目标模型路径不是普通文件：$MODEL_PATH"
fi

if [[ -f "$MODEL_PATH" ]]; then
  backup_path="$MODELS_DIR/.$MODEL_FILENAME.backup.$(date +%Y%m%d%H%M%S)-$$"
  cp -p -- "$MODEL_PATH" "$backup_path"
  printf '已备份现有模型：%s\n' "$backup_path"
fi

staged_path="$MODELS_DIR/.$MODEL_FILENAME.new.$$"
install -m 0444 -- "$exported_path" "$staged_path"
mv -f -- "$staged_path" "$MODEL_PATH"
staged_path=""

if [[ ! -f "$ENV_PATH" ]]; then
  cp -- .env.example "$ENV_PATH"
  chmod 600 "$ENV_PATH"
fi

env_tmp="$ENV_PATH.tmp.$$"
awk -v model_path="$CONTAINER_MODEL_PATH" '
  BEGIN { found = 0 }
  /^OBJECTS365_MODEL=/ {
    print "OBJECTS365_MODEL=" model_path
    found = 1
    next
  }
  { print }
  END {
    if (!found) print "OBJECTS365_MODEL=" model_path
  }
' "$ENV_PATH" > "$env_tmp"
chmod --reference="$ENV_PATH" "$env_tmp" 2>/dev/null || chmod 600 "$env_tmp"
mv -f -- "$env_tmp" "$ENV_PATH"

restore_model() {
  if [[ -n "$backup_path" && -f "$backup_path" ]]; then
    install -m 0444 -- "$backup_path" "$MODEL_PATH"
    printf '已恢复旧模型：%s\n' "$MODEL_PATH" >&2
  else
    rm -f -- "$MODEL_PATH"
    printf '已移除未通过验证的新模型：%s\n' "$MODEL_PATH" >&2
  fi
}

printf '%s\n' '正在使用 app 镜像验证 OpenCV DNN 可加载模型...'
if ! docker compose run --rm --no-deps --entrypoint python app \
  -c "import cv2; cv2.dnn.readNetFromONNX('$CONTAINER_MODEL_PATH')" >/dev/null; then
  restore_model
  die "容器内 OpenCV DNN 无法加载 ONNX"
fi

printf '%s\n' '正在启动 GuDuck app...'
docker compose up -d --force-recreate app

printf '\nObjects365 安装完成。\n模型文件：%s\n容器路径：%s\n' "$MODEL_PATH" "$CONTAINER_MODEL_PATH"
printf '%s\n' '请打开 GuDuck「设置」，选择 Objects365，然后在目标摄像头启用绘本物体播报。'
if [[ -n "$backup_path" ]]; then
  printf '旧模型备份：%s\n' "$backup_path"
fi
