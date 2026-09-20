from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol


class JsonApi(Protocol):
    def get_json(self, url: str, token: str) -> dict[str, Any]: ...


class UrllibSpotifyApi:
    def get_json(self, url: str, token: str) -> dict[str, Any]:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)


class SpotifyTokenStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))

    def update_from_refresh(self, refreshed: dict[str, Any]) -> dict[str, Any]:
        current = self.load()
        current.update(refreshed)
        if not refreshed.get("refresh_token") and current.get("refresh_token"):
            current["refresh_token"] = current["refresh_token"]
        current["expires_at"] = int(time.time()) + int(current.get("expires_in", 3600)) - 60
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(json.dumps(current, indent=2), encoding="utf-8")
        temporary.replace(self.path)
        return current

    def access_token(self, client_id: str, client_secret: str) -> str:
        data = self.load()
        if data.get("access_token") and int(data.get("expires_at", 0)) > int(time.time()):
            return str(data["access_token"])
        body = urllib.parse.urlencode({
            "grant_type": "refresh_token",
            "refresh_token": data["refresh_token"],
        }).encode()
        import base64
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        request = urllib.request.Request(
            "https://accounts.spotify.com/api/token",
            data=body,
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            refreshed = json.load(response)
        return str(self.update_from_refresh(refreshed)["access_token"])


def fetch_all_saved_tracks(api: JsonApi, token: str, first_url: str = "https://api.spotify.com/v1/me/tracks?limit=50") -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    url: str | None = first_url
    while url:
        page = api.get_json(url, token)
        items.extend(page.get("items") or [])
        url = page.get("next")
    return items


def build_saved_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        track = item.get("track") or {}
        spotify_id = track.get("id")
        if not spotify_id:
            continue
        rows.append({
            "spotify_id": spotify_id,
            "isrc": (track.get("external_ids") or {}).get("isrc", ""),
            "artist": ", ".join(a.get("name", "") for a in track.get("artists") or [] if a.get("name")),
            "title": track.get("name", ""),
            "album": (track.get("album") or {}).get("name", ""),
            "duration_ms": track.get("duration_ms"),
            "explicit": bool(track.get("explicit")),
            "added_at": item.get("added_at", ""),
        })
    return rows
