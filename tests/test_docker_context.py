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


def test_go2rtc_does_not_replace_the_image_command_with_bare_flags() -> None:
    compose = Path("compose.poc.yaml").read_text(encoding="utf-8")

    assert 'command: ["-c", "/config/go2rtc.yaml"]' not in compose
