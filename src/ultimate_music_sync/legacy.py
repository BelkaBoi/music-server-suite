from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DotifyImportReport:
    queue_total: int
    queue_statuses: dict[str, int]
    database_rows: int
    database_missing_paths: int
    database_duplicate_paths: int


def load_queue(source: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(source, dict):
        return source
    return json.loads(Path(source).read_text(encoding="utf-8"))


def import_dotify_snapshot(
    state: sqlite3.Connection,
    queue_source: dict[str, Any] | str | Path,
    legacy_db_path: str | Path,
) -> DotifyImportReport:
    """Import legacy state only; never create, modify, or remove media files."""
    queue = load_queue(queue_source)
    queue_items = queue.get("items", {})
    if not isinstance(queue_items, dict):
        raise ValueError("Dotify queue items must be an object")

    legacy_uri = f"file:{Path(legacy_db_path).resolve().as_posix()}?mode=ro"
    legacy = sqlite3.connect(legacy_uri, uri=True)
    try:
        rows = legacy.execute("SELECT id, path FROM media").fetchall()
    finally:
        legacy.close()

    db_paths = {str(media_id): str(path) for media_id, path in rows}
    counts = Counter()
    paths = Counter(str(Path(path).resolve()).casefold() for _, path in rows)
    missing = sum(not Path(path).exists() for _, path in rows)
    duplicate_groups = sum(count > 1 for count in paths.values())
    now = datetime.now(timezone.utc).isoformat()

    for item in queue_items.values():
        if not isinstance(item, dict):
            continue
        spotify_id = item.get("media_id")
        if not spotify_id:
            continue
        status = str(item.get("status") or "unknown")
        counts[status] += 1
        state.execute(
            """
            INSERT INTO legacy_dotify(
                spotify_id, queue_status, queue_error, database_path,
                final_path, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(spotify_id) DO UPDATE SET
                queue_status=excluded.queue_status,
                queue_error=excluded.queue_error,
                database_path=excluded.database_path,
                final_path=excluded.final_path,
                imported_at=excluded.imported_at
            """,
            (
                spotify_id,
                status,
                item.get("error"),
                db_paths.get(str(spotify_id)),
                item.get("final_path"),
                now,
            ),
        )

    # Preserve database-only IDs as legacy successes instead of silently losing them.
    for spotify_id, path in db_paths.items():
        state.execute(
            """
            INSERT INTO legacy_dotify(
                spotify_id, queue_status, queue_error, database_path,
                final_path, imported_at
            ) VALUES (?, 'database_only', NULL, ?, ?, ?)
            ON CONFLICT(spotify_id) DO UPDATE SET
                database_path=excluded.database_path,
                imported_at=excluded.imported_at
            """,
            (spotify_id, path, path, now),
        )
    state.commit()

    return DotifyImportReport(
        queue_total=len(queue_items),
        queue_statuses=dict(counts),
        database_rows=len(rows),
        database_missing_paths=missing,
        database_duplicate_paths=duplicate_groups,
    )
