from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

from ultimate_music_sync.db import connect, migrate
from ultimate_music_sync.cli import main


def test_migrate_creates_schema_and_wal(tmp_path: Path) -> None:
    db_path = tmp_path / "library.sqlite"
    db = connect(db_path)
    migrate(db)
    assert db.execute("SELECT version FROM schema_meta").fetchone()[0] == 1
    assert db.execute("SELECT name FROM sqlite_master WHERE name='tracks'").fetchone()
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    db.close()


def test_init_cli_creates_database(tmp_path: Path, capsys) -> None:
    assert main(["--state-dir", str(tmp_path), "init"]) == 0
    assert (tmp_path / "library.sqlite").exists()
    assert "Initialized" in capsys.readouterr().out
