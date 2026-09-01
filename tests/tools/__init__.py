"""Tests for local-only validation commands."""

from pathlib import Path

# Pytest imports this package before the repository-root tools package.
__path__.append(str(Path(__file__).parents[2] / "tools"))
