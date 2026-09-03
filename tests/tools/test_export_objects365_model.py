import sys
from types import SimpleNamespace

from tools.export_objects365_model import main


def test_export_uses_grid_output_and_copies_the_onnx_file(tmp_path, monkeypatch) -> None:
    checkpoint = tmp_path / "yolo26n-objv1-150.pt"
    checkpoint.write_bytes(b"checkpoint")
    generated = tmp_path / "generated.onnx"
    output = tmp_path / "models" / "objects365.onnx"

    class FakeYOLO:
        def __init__(self, model: str) -> None:
            assert model == str(checkpoint)

        def export(self, **options: object) -> str:
            assert options == {
                "format": "onnx",
                "imgsz": 416,
                "end2end": False,
                "dynamic": False,
                "simplify": False,
                "opset": 17,
            }
            generated.write_bytes(b"onnx")
            return str(generated)

    monkeypatch.setitem(sys.modules, "ultralytics", SimpleNamespace(YOLO=FakeYOLO))

    exit_code = main(
        [
            "--checkpoint",
            str(checkpoint),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    assert output.read_bytes() == b"onnx"
