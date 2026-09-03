from pathlib import Path


def test_app_dockerfile_contains_test_suite_for_offline_container_verification() -> None:
    dockerfile = Path("docker/app.Dockerfile").read_text(encoding="utf-8")

    assert "COPY tests ./tests" in dockerfile
    assert "COPY docker ./docker" in dockerfile
    assert "compose.yaml .env.example" in dockerfile


def test_poc_dockerfile_contains_app_test_metadata_for_offline_verification() -> None:
    dockerfile = Path("docker/poc.Dockerfile").read_text(encoding="utf-8")

    assert "COPY docker ./docker" in dockerfile
    assert "compose.yaml .env.example" in dockerfile


def test_container_dockerfiles_reuse_pip_download_cache() -> None:
    for path in (Path("docker/app.Dockerfile"), Path("docker/poc.Dockerfile")):
        dockerfile = path.read_text(encoding="utf-8")

        assert dockerfile.startswith("# syntax=docker/dockerfile:1\n")
        assert (
            "RUN --mount=type=cache,target=/root/.cache/pip \\\n"
            "    pip install '.[dev]'"
        ) in dockerfile
        assert "--no-cache-dir" not in dockerfile
