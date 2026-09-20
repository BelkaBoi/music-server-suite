from pathlib import Path

from ultimate_music_sync.db import connect, migrate
from ultimate_music_sync.legacy import import_dotify_snapshot


def test_import_dotify_snapshot_is_non_destructive_and_reports_duplicates(tmp_path: Path) -> None:
    library = tmp_path / "library"
    library.mkdir()
    first = library / "first.ogg"
    first.write_bytes(b"audio-one")
    queue = {
        "version": 1,
        "items": {
            "hash-a": {
                "media_id": "spotify-a",
                "status": "success",
                "error": None,
                "final_path": str(first),
            },
            "hash-b": {
                "media_id": "spotify-b",
                "status": "failed",
                "error": "audio key rejected",
                "final_path": str(library / "missing.ogg"),
            },
        },
    }
    legacy_db = tmp_path / "dotify.sqlite"
    legacy = connect(legacy_db)
    legacy.execute("CREATE TABLE media (id TEXT PRIMARY KEY, path TEXT NOT NULL)")
    legacy.execute("INSERT INTO media VALUES (?, ?)", ("spotify-a", str(first)))
    legacy.execute("INSERT INTO media VALUES (?, ?)", ("spotify-b", str(first)))
    legacy.commit()
    legacy.close()

    state = connect(tmp_path / "state.sqlite")
    migrate(state)
    report = import_dotify_snapshot(state, queue, legacy_db)

    assert report.queue_total == 2
    assert report.queue_statuses == {"success": 1, "failed": 1}
    assert report.database_rows == 2
    assert report.database_missing_paths == 0
    assert report.database_duplicate_paths == 1
    assert state.execute("SELECT COUNT(*) FROM legacy_dotify").fetchone()[0] == 2
    assert first.read_bytes() == b"audio-one"
