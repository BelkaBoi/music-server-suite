from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from mutagen import File as MutagenFile

from ultimate_music_sync.reconcile import reconcile_saved_with_library
from ultimate_music_sync.spotify import SpotifyTokenStore, UrllibSpotifyApi, build_saved_rows, fetch_all_saved_tracks

import os as _os
import sys as _sys

ROOT = Path(_os.environ.get("MUSIC_LIBRARY", Path.home() / "Music" / "Spotify Liked"))
TOOLS = Path(_os.environ.get("MUSICSERVER_TOOLS", Path.home() / "Navidrome" / "tools"))
TOKEN = Path(_os.environ.get("SPOTIFY_TOKEN_FILE", TOOLS / "spotify-token.json"))
_DEFAULT_STATE = Path(__file__).resolve().parents[2] / ".real-state"
STATE = Path(_os.environ.get("UMS_STATE", _DEFAULT_STATE))
REPORT = STATE / "hourly-sync-report.json"
SAVED_CSV = STATE / "spotify-saved-tracks.csv"
MISSING_CSV = STATE / "spotify-missing-tracks.csv"
AUDIO = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac", ".alac"}


def first(tags, key: str, default=""):
    value = tags.get(key, [default]) if tags else [default]
    if not isinstance(value, list):
        value = [value]
    return str(value[0] if value else default)


def inventory() -> list[dict]:
    rows = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in AUDIO:
            continue
        try:
            media = MutagenFile(path, easy=True)
            tags = media or {}
            info = getattr(media, "info", None)
            rows.append({
                "path": str(path),
                "artist": first(tags, "artist", "Unknown Artist"),
                "title": first(tags, "title", path.stem),
                "album": first(tags, "album", ""),
                "isrc": first(tags, "isrc", ""),
                "duration_ms": round(float(getattr(info, "length", 0)) * 1000) if info else None,
            })
        except Exception:
            continue
    return rows


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temporary.replace(path)


def main() -> int:
    STATE.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    report = {"started_at": started, "status": "blocked", "downloaded": 0}
    if not TOKEN.exists():
        report["blocker"] = "Spotify OAuth token is missing"
        REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        return 2
    client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        report["blocker"] = "Spotify client credentials are unavailable to the scheduled process"
        REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report))
        return 2

    token = SpotifyTokenStore(TOKEN).access_token(client_id, client_secret)
    saved = build_saved_rows(fetch_all_saved_tracks(UrllibSpotifyApi(), token))
    local = inventory()
    result = reconcile_saved_with_library(saved, local)
    fields = ["spotify_id", "isrc", "artist", "title", "album", "duration_ms", "explicit", "added_at"]
    write_csv(SAVED_CSV, saved, fields)
    write_csv(MISSING_CSV, result["missing"], fields)

    # Spotify is metadata-only. Acquisition requires an explicit durable-export
    # rights basis and source URL in the authorization manifest.
    manifest = TOOLS / "youtube-authorized-manifest.csv"
    importer_summary = None
    if manifest.exists() and len(manifest.read_text(encoding="utf-8-sig").splitlines()) > 1:
        process = subprocess.run([
            sys.executable, str(TOOLS / "youtube_authorized_import.py"), "--manifest", str(manifest)
        ], text=True, capture_output=True, encoding="utf-8", errors="replace")
        try:
            importer_summary = json.loads(process.stdout.strip().splitlines()[-1])
        except Exception:
            importer_summary = {"status": "failed", "exit_code": process.returncode}

    report.update({
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "status": "ok",
        "spotify_saved": len(saved),
        "local_audio": len(local),
        "matched": len(result["matched"]),
        "missing": len(result["missing"]),
        "authorized_import": importer_summary,
        "acquisition_policy": "Missing Spotify tracks are never ripped from subscription audio. Only manifest entries with explicit durable-export rights and source URLs are downloaded.",
    })
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
