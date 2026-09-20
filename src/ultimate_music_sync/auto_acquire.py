"""Automatic acquisition orchestrator: Dotify first, free sources second.

Stage 1: reconcile the Spotify saved snapshot against the local library.
Stage 2: acquire missing tracks with the user's own-account Dotify connector
         (highest-quality priority), stopping early when Spotify's audio-key
         rejection pattern reappears.
Stage 3: acquire whatever remains via free sources (SoundCloud, then YouTube)
         with conservative matching, tagging, and LRCLIB lyrics.
Stage 4: backfill missing .lrc sidecars for the whole library.

Writes auto-acquire-report.json; never deletes existing library files.
"""

from __future__ import annotations

import csv
import os as _os
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from .acquire import STATE, LIBRARY, acquire_lock, fill_library_lrc, load_missing, release_lock, safe_name
from .hourly_sync import inventory
from .reconcile import reconcile_saved_with_library

PROJECT = Path(_os.environ.get("UMS_PROJECT", Path(__file__).resolve().parents[2]))
_DOTIFY_NAME = "dotify.exe" if sys.platform == "win32" else "dotify"
DOTIFY = Path(_os.environ.get("DOTIFY_BIN", Path.home() / (".dotify-venv" / _DOTIFY_NAME if sys.platform == "win32" else ".local/bin/dotify")))
DOTIFY_STATE = Path(_os.environ.get("DOTIFY_STATE", Path.home() / ".dotify"))
REPORT = STATE / "auto-acquire-report.json"
URLS = STATE / "dotify-missing-urls.txt"
SAVED_CSV = STATE / "spotify-saved-tracks.csv"
FALLBACK_CSV = STATE / "fallback-remaining.csv"
LOG = STATE / "auto-acquire.log"

AUDIO_QUALITY = "vorbis-high,vorbis-medium,vorbis-low"
AUDIO_KEY_LIMIT = 8  # consecutive audio-key rejections before abandoning Dotify


def log(message: str) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def build_url_file(missing: list[dict], done_ids: set[str]) -> int:
    ids = [row["spotify_id"] for row in missing if row["spotify_id"] not in done_ids]
    URLS.write_text(
        "\n".join(f"https://open.spotify.com/track/{spotify_id}" for spotify_id in ids),
        encoding="utf-8",
    )
    return len(ids)


def run_dotify() -> dict:
    command = [
        str(DOTIFY), "download", "--no-tui", "--skip-preflight", "-r", str(URLS),
        "--output", str(LIBRARY),
        "--audio-quality", AUDIO_QUALITY,
        "--database-path", str(DOTIFY_STATE / "liked-downloads.sqlite"),
        "--queue-state-path", str(DOTIFY_STATE / "liked-queue.json"),
        "--librespot-credentials-path", str(DOTIFY_STATE / "librespot_credentials.json"),
        "--wait-interval", "5",
        "--log-level", "INFO",
    ]
    process = subprocess.Popen(
        command,
        stdout=open(STATE / "dotify-stage.log", "w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
        text=True,
    )
    import re
    import time
    tail = ""
    while process.poll() is None:
        time.sleep(5)
        try:
            tail = (STATE / "dotify-stage.log").read_text(encoding="utf-8", errors="replace")[-4000:]
            if tail.count("LIBRESPOT_AUDIO_KEY_REJECTED") >= AUDIO_KEY_LIMIT:
                process.kill()
                return {"stopped": "audio_key_rejection_pattern", "exit_code": None}
        except Exception:
            pass
    summary = {"exit_code": process.poll()}
    for token in ("successful", "failed"):
        for line in tail.splitlines():
            if line.startswith("[INFO") and "Finished:" in line:
                import re
                match = re.search(rf"(\d+) {token}", line)
                if match:
                    summary[token] = int(match.group(1))
    return summary


def dotify_done_ids() -> set[str]:
    import sqlite3
    database = DOTIFY_STATE / "liked-downloads.sqlite"
    if not database.exists():
        return set()
    connection = sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True)
    try:
        return {str(row[0]) for row in connection.execute("SELECT id FROM media")}
    finally:
        connection.close()


def main() -> int:
    if not acquire_lock():
        print(json.dumps({"status": "locked"}))
        return 0
    try:
        return run_pipeline()
    finally:
        release_lock()


def run_pipeline() -> int:
    STATE.mkdir(parents=True, exist_ok=True)
    started = datetime.now(timezone.utc).isoformat()
    report: dict = {"started_at": started, "dotify": None, "fallback": None, "lrc": None}

    saved = list(csv.DictReader(open(SAVED_CSV, encoding="utf-8-sig", newline="")))
    local = inventory()
    result = reconcile_saved_with_library(saved, local)
    missing = result["missing"]
    report["saved"] = len(saved)
    report["local_audio"] = len(local)
    report["matched"] = len(result["matched"])
    report["missing_at_start"] = len(missing)
    log(f"=== auto-acquire: {len(missing)} missing ===")

    if missing:
        done = dotify_done_ids()
        count = build_url_file(missing, done)
        log(f"dotify stage: {count} urls (skipping {len(done & {r['spotify_id'] for r in missing})} already in dotify db)")
        if count:
            report["dotify"] = run_dotify()
            log(f"dotify stage done: {report['dotify']}")

        saved_ids = {row["spotify_id"] for row in missing}
        still_missing = [
            row for row in missing if not any(
                (LIBRARY / (f"{safe_name(row['artist'])} - {safe_name(row['title'])} [{row['spotify_id']}]{ext}")).exists()
                for ext in (".ogg", ".m4a", ".mp3", ".opus", ".flac")
            )
        ]
        fields = ["spotify_id", "isrc", "artist", "title", "album", "duration_ms", "explicit", "added_at"]
        with FALLBACK_CSV.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(still_missing)
        log(f"fallback stage: {len(still_missing)} tracks")
        if still_missing:
            process = subprocess.run(
                [sys.executable, "-m", "ultimate_music_sync.acquire", "--csv", str(FALLBACK_CSV), "--no-lock"],
                text=True, capture_output=True, encoding="utf-8", errors="replace", timeout=21600,
            )
            try:
                report["fallback"] = json.loads(process.stdout.strip().splitlines()[-1])
            except Exception:
                report["fallback"] = {"status": "failed", "exit_code": process.returncode}
            log(f"fallback stage done: {report['fallback']}")

    report["lrc"] = fill_library_lrc()
    final = reconcile_saved_with_library(saved, inventory())
    report["missing_at_end"] = len(final["missing"])
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    REPORT.write_text(json.dumps(report, indent=2), encoding="utf-8")
    log(f"=== auto-acquire done: missing {report['missing_at_start']} -> {report['missing_at_end']} ===")
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
