import subprocess
from pathlib import Path

SCRIPT = Path("scripts/install-objects365.sh")


def test_objects365_installer_is_valid_and_uses_pinned_export_flow() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    script = SCRIPT.read_text(encoding="utf-8")
    assert "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n-objv1-150.pt" in script
    assert "67104718c37bd2277a98390bcf5bf841d36de3db8b92abadb40f4db05e3710433ce8145d62aa6eda373fa79399b506f9" in script
    assert "ultralytics==8.4.138" in script
    assert "onnx==1.19.1" in script
    assert "tools/export_objects365_model.py" in script
    assert "object_detection_objects365_yolo26n_416.onnx" in script
    assert "docker compose run --rm --no-deps --entrypoint python app" in script
    assert "docker compose up -d --force-recreate app" in script
