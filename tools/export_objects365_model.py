import argparse
import shutil
import sys
from pathlib import Path


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export an Objects365 YOLO26 checkpoint for OpenCV DNN.",
    )
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if not args.checkpoint.is_file():
        print("checkpoint is missing", file=sys.stderr)
        return 2
    if args.output.suffix.lower() != ".onnx":
        print("output must be an ONNX file", file=sys.stderr)
        return 2

    try:
        from ultralytics import YOLO
    except ImportError:
        print("ultralytics is required only on the export machine", file=sys.stderr)
        return 2

    model = YOLO(str(args.checkpoint))
    exported = Path(
        model.export(
            format="onnx",
            imgsz=416,
            end2end=False,
            dynamic=False,
            simplify=False,
            opset=17,
        )
    )
    if not exported.is_file():
        print("export did not produce an ONNX file", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    if exported.resolve() != args.output.resolve():
        shutil.copyfile(exported, args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
