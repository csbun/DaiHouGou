import sqlite3
from pathlib import Path

import pytest

from guduck.database import DatabaseMigrationError, application_database_path


def create_legacy_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE marker(value TEXT NOT NULL)")
        connection.execute("INSERT INTO marker VALUES ('preserved')")


def test_fresh_install_uses_guduck_database_path(tmp_path: Path) -> None:
    path = application_database_path(tmp_path)

    assert path == tmp_path / "guduck.db"
    assert tmp_path.is_dir()
    assert not path.exists()


def test_legacy_database_and_sidecars_are_migrated_with_data(tmp_path: Path) -> None:
    legacy = tmp_path / "daihougou.db"
    create_legacy_database(legacy)
    legacy.with_name("daihougou.db-wal").write_bytes(b"wal")
    legacy.with_name("daihougou.db-shm").write_bytes(b"shm")

    current = application_database_path(tmp_path)

    assert current == tmp_path / "guduck.db"
    assert not legacy.exists()
    assert current.with_name("guduck.db-wal").read_bytes() == b"wal"
    assert current.with_name("guduck.db-shm").read_bytes() == b"shm"
    with sqlite3.connect(current) as connection:
        assert connection.execute("SELECT value FROM marker").fetchone() == ("preserved",)


def test_migration_is_idempotent(tmp_path: Path) -> None:
    legacy = tmp_path / "daihougou.db"
    create_legacy_database(legacy)

    first = application_database_path(tmp_path)
    second = application_database_path(tmp_path)

    assert first == second == tmp_path / "guduck.db"


def test_interrupted_sidecar_migration_is_resumed(tmp_path: Path) -> None:
    legacy = tmp_path / "daihougou.db"
    create_legacy_database(legacy)
    current_wal = tmp_path / "guduck.db-wal"
    current_wal.write_bytes(b"already-moved")

    current = application_database_path(tmp_path)

    assert current.exists()
    assert not legacy.exists()
    assert current_wal.read_bytes() == b"already-moved"


def test_interrupted_hardlink_migration_removes_the_legacy_sidecar_name(
    tmp_path: Path,
) -> None:
    legacy = tmp_path / "daihougou.db"
    create_legacy_database(legacy)
    legacy_wal = tmp_path / "daihougou.db-wal"
    legacy_wal.write_bytes(b"linked-state")
    current_wal = tmp_path / "guduck.db-wal"
    current_wal.hardlink_to(legacy_wal)

    application_database_path(tmp_path)

    assert not legacy_wal.exists()
    assert current_wal.read_bytes() == b"linked-state"


def test_existing_guduck_database_takes_precedence(tmp_path: Path) -> None:
    legacy = tmp_path / "daihougou.db"
    current = tmp_path / "guduck.db"
    legacy.write_bytes(b"legacy")
    current.write_bytes(b"current")

    assert application_database_path(tmp_path) == current
    assert legacy.read_bytes() == b"legacy"
    assert current.read_bytes() == b"current"


def test_migration_rejects_sidecar_destination_collision(tmp_path: Path) -> None:
    legacy = tmp_path / "daihougou.db"
    create_legacy_database(legacy)
    legacy.with_name("daihougou.db-wal").write_bytes(b"legacy-wal")
    current_wal = tmp_path / "guduck.db-wal"
    current_wal.write_bytes(b"current-wal")

    with pytest.raises(DatabaseMigrationError, match="sidecar destination already exists"):
        application_database_path(tmp_path)

    assert legacy.exists()
    assert legacy.with_name("daihougou.db-wal").read_bytes() == b"legacy-wal"
    assert current_wal.read_bytes() == b"current-wal"


def test_migration_does_not_overwrite_destination_created_during_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "daihougou.db"
    create_legacy_database(legacy)
    current = tmp_path / "guduck.db"
    hardlink_to = Path.hardlink_to

    def create_destination_before_link(path: Path, target: Path) -> None:
        if path == current:
            path.write_bytes(b"concurrent-state")
        hardlink_to(path, target)

    monkeypatch.setattr(Path, "hardlink_to", create_destination_before_link)

    with pytest.raises(DatabaseMigrationError, match="destination already exists"):
        application_database_path(tmp_path)

    assert legacy.exists()
    assert current.read_bytes() == b"concurrent-state"
