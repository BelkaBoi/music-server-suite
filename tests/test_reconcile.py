import csv
from pathlib import Path

from ultimate_music_sync.reconcile import reconcile_saved_with_library


def test_reconcile_matches_isrc_before_metadata(tmp_path: Path):
    saved = [{"spotify_id": "s1", "isrc": "USAAA0000001", "artist": "A", "title": "T", "album": "X", "duration_ms": 100000}]
    inventory = [{"path": str(tmp_path / "one.flac"), "artist": "Other", "title": "Other", "isrc": "USAAA0000001", "duration_ms": 100100}]
    result = reconcile_saved_with_library(saved, inventory)
    assert result["matched"][0]["match_basis"] == "isrc"
    assert result["missing"] == []


def test_reconcile_does_not_accept_version_conflict(tmp_path: Path):
    saved = [{"spotify_id": "s1", "isrc": "", "artist": "A", "title": "Song (Live)", "album": "X", "duration_ms": 100000}]
    inventory = [{"path": str(tmp_path / "one.mp3"), "artist": "A", "title": "Song", "isrc": "", "duration_ms": 100100}]
    result = reconcile_saved_with_library(saved, inventory)
    assert result["matched"] == []
    assert result["missing"][0]["spotify_id"] == "s1"
