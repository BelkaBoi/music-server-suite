from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_meta (
    version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tracks (
    id TEXT PRIMARY KEY,
    spotify_id TEXT UNIQUE,
    isrc TEXT,
    title TEXT,
    artists_json TEXT,
    album TEXT,
    duration_ms INTEGER,
    explicit INTEGER,
    liked INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tracks_isrc_idx ON tracks(isrc);
CREATE TABLE IF NOT EXISTS provider_tracks (
    provider TEXT NOT NULL,
    provider_track_id TEXT NOT NULL,
    track_id TEXT REFERENCES tracks(id),
    isrc TEXT,
    metadata_json TEXT NOT NULL,
    metadata_hash TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(provider, provider_track_id)
);
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    track_id TEXT NOT NULL REFERENCES tracks(id),
    provider TEXT NOT NULL,
    provider_track_id TEXT NOT NULL,
    isrc_match INTEGER NOT NULL,
    duration_delta_ms INTEGER,
    confidence REAL NOT NULL,
    decision TEXT NOT NULL CHECK(decision IN ('pending','auto_accepted','accepted','rejected')),
    details_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS acquisitions (
    id TEXT PRIMARY KEY,
    track_id TEXT NOT NULL REFERENCES tracks(id),
    candidate_id TEXT REFERENCES candidates(id),
    connector TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    rights_basis TEXT NOT NULL,
    receipt_reference TEXT,
    terms_reference TEXT,
    acquired_at TEXT NOT NULL,
    provenance_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS media_files (
    id TEXT PRIMARY KEY,
    track_id TEXT REFERENCES tracks(id),
    acquisition_id TEXT REFERENCES acquisitions(id),
    path TEXT NOT NULL UNIQUE,
    sha256 TEXT,
    size_bytes INTEGER NOT NULL,
    container TEXT,
    codec TEXT,
    lossless INTEGER,
    bit_depth INTEGER,
    sample_rate_hz INTEGER,
    channels INTEGER,
    bitrate_bps INTEGER,
    duration_ms INTEGER,
    quality_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('staged','active','backup','quarantine','deleted')),
    created_at TEXT NOT NULL,
    verified_at TEXT
);
CREATE INDEX IF NOT EXISTS media_track_state_idx ON media_files(track_id, state);
CREATE TABLE IF NOT EXISTS legacy_dotify (
    spotify_id TEXT PRIMARY KEY,
    queue_status TEXT,
    queue_error TEXT,
    database_path TEXT,
    final_path TEXT,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    dedupe_key TEXT NOT NULL UNIQUE,
    track_id TEXT REFERENCES tracks(id),
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('queued','running','retry_wait','blocked','succeeded','failed','cancelled')),
    attempt INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    not_before TEXT,
    resume_token TEXT,
    last_error_class TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS jobs_state_idx ON jobs(state, not_before);
CREATE TABLE IF NOT EXISTS replacement_journal (
    id TEXT PRIMARY KEY,
    track_id TEXT NOT NULL REFERENCES tracks(id),
    old_media_id TEXT REFERENCES media_files(id),
    new_media_id TEXT NOT NULL REFERENCES media_files(id),
    target_path TEXT NOT NULL,
    staged_path TEXT NOT NULL,
    backup_path TEXT,
    phase TEXT NOT NULL CHECK(phase IN ('prepared','backup_created','published','database_committed','cleaned','rolled_back')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    details_json TEXT NOT NULL
);
"""


def connect(path: str | Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA synchronous=FULL")
    db.execute("PRAGMA busy_timeout=10000")
    return db


def migrate(db: sqlite3.Connection) -> None:
    db.executescript(SCHEMA)
    row = db.execute("SELECT version FROM schema_meta").fetchone()
    if row is None:
        db.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
    elif row[0] != SCHEMA_VERSION:
        raise RuntimeError(f"Unsupported schema version {row[0]}")
    db.commit()
