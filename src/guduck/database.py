from __future__ import annotations

import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)

DATABASE_NAME = "guduck.db"
LEGACY_DATABASE_NAME = "daihougou.db"
SQLITE_SIDECAR_SUFFIXES = ("-wal", "-shm")


class DatabaseMigrationError(RuntimeError):
    pass


def _same_file(source: Path, destination: Path) -> bool:
    try:
        return source.samefile(destination)
    except OSError:
        return False


def _move_without_overwrite(source: Path, destination: Path) -> None:
    try:
        destination.hardlink_to(source)
    except FileExistsError as error:
        if not _same_file(source, destination):
            raise DatabaseMigrationError(
                f"destination already exists: {destination.name}"
            ) from error
    except OSError as error:
        raise DatabaseMigrationError(f"could not migrate {source.name}") from error

    try:
        source.unlink()
    except OSError as error:
        raise DatabaseMigrationError(
            f"could not remove legacy filename: {source.name}"
        ) from error


def application_database_path(data_dir: Path) -> Path:
    data_dir.mkdir(parents=True, exist_ok=True)
    current = data_dir / DATABASE_NAME
    legacy = data_dir / LEGACY_DATABASE_NAME
    if current.exists() or not legacy.exists():
        return current

    sidecars = tuple(
        (
            legacy.with_name(f"{legacy.name}{suffix}"),
            current.with_name(f"{current.name}{suffix}"),
        )
        for suffix in SQLITE_SIDECAR_SUFFIXES
    )
    for source, destination in sidecars:
        if source.exists() and destination.exists() and not _same_file(source, destination):
            raise DatabaseMigrationError(
                f"sidecar destination already exists: {destination.name}"
            )

    for source, destination in sidecars:
        if source.exists():
            _move_without_overwrite(source, destination)
    _move_without_overwrite(legacy, current)
    LOGGER.info("Migrated the legacy GuDuck database filename")
    return current
