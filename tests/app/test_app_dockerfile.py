from pathlib import Path


def test_app_image_pins_and_verifies_person_model() -> None:
    dockerfile = Path("docker/app.Dockerfile").read_text(encoding="utf-8")

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
    assert 'ENTRYPOINT ["guduck"]' in dockerfile


def test_app_image_pins_and_verifies_nanodet_object_model_and_license() -> None:
    dockerfile = Path("docker/app.Dockerfile").read_text(encoding="utf-8")

    assert (
        "47534e27c9851bb1128ccc0102f1145e27f23f98/"
        "models/object_detection_nanodet/object_detection_nanodet_2022nov.onnx"
    ) in dockerfile
    assert "84ee6a6dd605f7019f25a81615a8fff886b235e8d3924930ca367c6e239a8c6d9c14a7e60b8bae54edca040cbf7b86e7" in dockerfile
    assert dockerfile.count("sha384sum --check") >= 2
    assert "COPY third_party/nanodet/LICENSE" in dockerfile
