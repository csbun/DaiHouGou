from pathlib import Path


def test_mvp_image_pins_and_verifies_person_model() -> None:
    dockerfile = Path("docker/mvp.Dockerfile").read_text(encoding="utf-8")

    assert (
        "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
        "47534e27c9851bb1128ccc0102f1145e27f23f98/"
        "models/person_detection_mediapipe/person_detection_mediapipe_2023mar.onnx"
    ) in dockerfile
    assert "sha384sum --check" in dockerfile
    assert (
        "cdc21e3741c46ae24e4d2fa3c368886bd7dadcd23d98b6acdc0db966d2d9ecc"
        "5624c095fa05d5f949cce69ef1029f9ef"
    ) in dockerfile
    assert "person-detection-0200" not in dockerfile
    assert 'ENTRYPOINT ["daihougou"]' in dockerfile
