from pathlib import Path


def test_docker_context_excludes_sensitive_and_generated_paths() -> None:
    ignored_paths = {
        line.strip()
        for line in Path(".dockerignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }

    assert {
        ".git",
        ".worktrees",
        ".env.poc",
        "artifacts",
        "config/poc-devices.json",
        "deploy/go2rtc/state",
        "deploy/homeassistant/state",
        "**/__pycache__",
        "**/.pytest_cache",
        "**/.ruff_cache",
        "**/*.pyc",
    } <= ignored_paths
    assert {"pyproject.toml", "src", "tests"}.isdisjoint(ignored_paths)
