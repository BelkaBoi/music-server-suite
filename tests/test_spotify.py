import json
from pathlib import Path

from ultimate_music_sync.spotify import (
    SpotifyTokenStore,
    build_saved_rows,
    fetch_all_saved_tracks,
)


class FakeSpotify:
    def __init__(self, pages):
        self.pages = pages

    def get_json(self, url, token):
        return self.pages[url]


def test_fetch_all_saved_tracks_follows_every_page():
    api = FakeSpotify({
        "first": {"items": [{"track": {"id": "a"}}], "next": "second"},
        "second": {"items": [{"track": {"id": "b"}}], "next": None},
    })
    items = fetch_all_saved_tracks(api, "token", "first")
    assert [item["track"]["id"] for item in items] == ["a", "b"]


def test_build_saved_rows_preserves_identity_and_isrc():
    rows = build_saved_rows([{
        "added_at": "2026-01-01T00:00:00Z",
        "track": {
            "id": "spotify-id",
            "name": "Track",
            "duration_ms": 123000,
            "explicit": True,
            "external_ids": {"isrc": "USAAA0000001"},
            "artists": [{"name": "Artist"}],
            "album": {"name": "Album"},
        },
    }])
    assert rows == [{
        "spotify_id": "spotify-id", "isrc": "USAAA0000001", "artist": "Artist",
        "title": "Track", "album": "Album", "duration_ms": 123000,
        "explicit": True, "added_at": "2026-01-01T00:00:00Z",
    }]


def test_token_store_refreshes_and_preserves_refresh_token(tmp_path: Path):
    path = tmp_path / "token.json"
    path.write_text(json.dumps({"access_token": "old", "refresh_token": "refresh"}))
    store = SpotifyTokenStore(path)
    store.update_from_refresh({"access_token": "new", "expires_in": 3600})
    saved = json.loads(path.read_text())
    assert saved["access_token"] == "new"
    assert saved["refresh_token"] == "refresh"
    assert saved["expires_at"] > 0
