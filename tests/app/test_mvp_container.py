from pathlib import Path


def test_mvp_dockerfile_contains_test_suite_for_offline_container_verification() -> None:
    dockerfile = Path("docker/mvp.Dockerfile").read_text(encoding="utf-8")

    assert "COPY tests ./tests" in dockerfile
    assert "COPY docker ./docker" in dockerfile
    assert "compose.poc.yaml .env.mvp.example" in dockerfile
    assert "pip install --no-cache-dir '.[dev]'" in dockerfile
