"""Free-source acquisition fallback: yt-dlp bestaudio + LRCLIB lyrics.

Only used for library tracks that the user's own-account Dotify connector
could not acquire. Downloads the best available audio from free sources
(YouTube etc.), preserving the source container/codec (no lossy->FLAC
transcode), writes canonical tags from catalog metadata, and fetches synced
lyrics (.lrc sidecars) from LRCLIB for files that lack them.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
import urllib.request
from pathlib import Path

import os as _os

DEFAULT_LIBRARY = Path.home() / ("Music/Spotify Liked" if sys.platform == "win32" else "Music/Spotify Liked")
LIBRARY = Path(_os.environ.get("MUSIC_LIBRARY", DEFAULT_LIBRARY))
_DEFAULT_STATE = Path(__file__).resolve().parents[2] / ".real-state"
STATE = Path(_os.environ.get("UMS_STATE", _DEFAULT_STATE))
LOG = STATE / "acquire-fallback.log"
RESULTS = STATE / "acquire-fallback-results.csv"
YT_ARCHIVE = STATE / "yt-dlp-archive.txt"
MUSIC_EXT = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wav", ".aac", ".alac"}


def log(message: str) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


LOCK = STATE / "acquire.lock"
LOCK_MAX_AGE_S = 6 * 3600


def acquire_lock() -> bool:
    STATE.mkdir(parents=True, exist_ok=True)
    if LOCK.exists():
        import time
        if time.time() - LOCK.stat().st_mtime < LOCK_MAX_AGE_S:
            log("another acquire run holds the lock; exiting")
            return False
        log("stale lock replaced")
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    return True


def release_lock() -> None:
    LOCK.unlink(missing_ok=True)


def safe_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", value).strip(" .")[:160]


def load_missing(csv_path: Path | None = None) -> list[dict]:
    path = csv_path or (STATE / "spotify-missing-tracks.csv")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def normalize_u(value: str) -> str:
    """Unicode-aware normalization (Georgian, Devanagari, etc. survive)."""
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE)


def strip_feat(value: str) -> str:
    """Remove (feat. X)/(prod. X) style parentheticals for match variants."""
    return re.sub(r"[(\[]\s*(feat|ft|prod|with)\..*?[)\]]", "", value or "", flags=re.IGNORECASE)


VERSION_WORDS = {"live", "remix", "remaster", "remastered", "acoustic", "instrumental",
                 "edit", "sped", "slowed", "karaoke", "demo", "nightcore", "boosted"}
STRICT_MARKERS = {"nightcore", "sped", "slowed", "boosted", "remix"}


def version_markers(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (value or "").casefold())) & VERSION_WORDS


def markers_candidate_set(cand_title: str, cand_artist: str) -> set[str]:
    return version_markers(f"{cand_title} {cand_artist}")


def candidate_matches(row: dict, cand_title: str, cand_artist: str, cand_duration_s: float | None,
                      strict: bool = False) -> tuple[bool, str]:
    """Conservative free-source match: title containment + duration window + version gates.

    strict=True additionally blocks ANY difference in nightcore/sped/slowed/
    boosted/remix markers even when the saved title lacks them (protects
    exact-version wants against altered re-uploads)."""
    if strict and STRICT_MARKERS & markers_candidate_set(cand_title, cand_artist):
        return False, "strict_version_marker"
    target = normalize_u(row.get("title", ""))
    base = normalize_u(strip_feat(row.get("title", "")))
    if not target:
        return False, "empty_target_title"
    haystack = normalize_u(f"{cand_artist} {cand_title}")
    hay_base = normalize_u(FEAT_RE.sub("", cand_title))
    target_core = normalize_u(re.sub(r"[(\[][^)\]]*(?:nightcore|bass|boosted|sped|slowed|remix|remaster|live|acoustic|instrumental)[^)\]]*[)\]]", "", strip_feat(row.get("title", "")), flags=re.IGNORECASE))
    matched = (
        target in haystack
        or (bool(base) and base in haystack)
        or (bool(target_core) and (target_core in haystack or target_core in hay_base))
    )
    if not matched:
        return False, "title_mismatch"
    markers_expected = version_markers(row.get("title", ""))
    markers_candidate = version_markers(f"{cand_title} {cand_artist}")
    if markers_expected != markers_candidate:
        return False, f"version_conflict:{sorted(markers_expected ^ markers_candidate)}"
    if strict and markers_expected != markers_candidate:
        return False, "strict_version_conflict"
    wanted_ms = int(row.get("duration_ms") or 0)
    if wanted_ms and cand_duration_s:
        delta = abs(wanted_ms / 1000.0 - cand_duration_s)
        if delta > max(2.0, wanted_ms / 1000.0 * 0.02):
            return False, f"duration_delta:{delta:.1f}s"
    return True, "ok"


FEAT_RE = re.compile(r"[(\[]?\s*(?:feat|ft|with)\.?\s+[^)\]]*[)\]]?", re.IGNORECASE)


def search_query_variants(artist: str, title: str) -> list[str]:
    """Alternate query shapes: feature text is often a mismatch, never a help."""
    artist = (artist or "").strip()
    title = (title or "").strip()
    clean_title = FEAT_RE.sub("", title).strip()
    primary_artist = re.split(r",|\band\b|&", artist, maxsplit=1, flags=re.IGNORECASE)[0].strip()
    variants = [f"{artist} {title}"]
    if clean_title != title:
        variants.append(f"{artist} {clean_title}")
    if primary_artist and primary_artist.casefold() != artist.casefold():
        variants.append(f"{primary_artist} {clean_title}".strip())
        variants.append(f"{clean_title} {primary_artist}")
    if title and artist:
        variants.append(f"{title} {artist}")  # reversed order: SC relevance is order-sensitive
    # Script mismatch (latin artist, non-latin title or vice versa): SC indexes the
    # official upload under the native-script artist, which latin queries never hit.
    # A title-only query reaches it (title script matches the index).
    CYR = {"а":"a","б":"b","в":"v","г":"g","д":"d","е":"e","ё":"e","ж":"zh","з":"z","и":"i",
           "й":"y","к":"k","л":"l","м":"m","н":"n","о":"o","п":"p","р":"r","с":"s","т":"t",
           "у":"u","ф":"f","х":"kh","ц":"ts","ч":"ch","ш":"sh","щ":"shch","ъ":"","ы":"y","ь":"",
           "э":"e","ю":"yu","я":"ya"}
    def _translit(text: str) -> str:
        out = []
        for ch in text:
            low = ch.lower()
            if low in CYR:
                rep = CYR[low]
                out.append(rep.capitalize() if ch.isupper() and rep else rep)
            else:
                out.append(ch)
        return "".join(out)

    def _script_of(text: str) -> str:
        for ch in text:
            if ch.isalpha():
                o = ord(ch)
                if 0x0400 <= o <= 0x04FF:
                    return "cyrillic"
                if o > 0x2E80:
                    return "other"
                return "latin"
        return "latin"
    if title and artist and _script_of(title) != _script_of(artist):
        variants.append(title)
        variants.append(_translit(title))          # cyrillic title, latin form
        title_t = _translit(title)
        if title_t and title_t.casefold() != title.casefold():
            variants.append(f"{title_t} {artist}")
            variants.append(f"{artist} {title_t}")
    seen: set[str] = set()
    ordered: list[str] = []
    for variant in variants:
        token = variant.casefold()
        if variant.strip() and token not in seen:
            seen.add(token)
            ordered.append(variant)
    return ordered or [f"{artist} {title}"]


def search_soundcloud(artist: str, title: str, limit: int = 6, custom_query: str | None = None) -> list[dict]:
    """Return [{title, uploader, duration, url}] via yt-dlp flat metadata search."""
    query = custom_query or f"{artist} {title}"
    query = f"scsearch{limit}:{query}"
    for attempt in range(2):
        result = subprocess.run(
            ["yt-dlp", "--no-warnings", "--socket-timeout", "20", "--flat-playlist",
             "--dump-single-json", query],
            text=True, capture_output=True, encoding="utf-8", errors="replace")
        if result.returncode == 0:
            break
        time.sleep(3)  # transient soundcloud search flakiness / soft rate limits
    if result.returncode != 0:
        return []
    try:
        data = json.loads(result.stdout)
    except Exception:
        return []
    entries = data.get("entries") if isinstance(data, dict) else None
    return [
        {
            "title": entry.get("title", ""),
            "uploader": entry.get("uploader") or entry.get("uploader_id") or "",
            "duration": entry.get("duration"),
            "url": entry.get("url") or entry.get("webpage_url") or "",
        }
        for entry in (entries or []) if isinstance(entry, dict)
    ]


YOUTUBE_BLOCKED: bool = False  # set when YouTube download confirms the bot wall


def search_youtube(artist: str, title: str, limit: int = 5) -> tuple[list[dict] | None, str]:
    """Return (entries, note). entries is None when YouTube is unusable (bot wall)."""
    global YOUTUBE_BLOCKED
    if YOUTUBE_BLOCKED:
        return None, "youtube_bot_wall (circuit-breaker)"
    query = f"ytsearch{limit}:{artist} {title} audio"
    command = ["yt-dlp", "--no-warnings", "--socket-timeout", "20", "--flat-playlist",
               "--extractor-args", f"youtube:player_client={YT_CLIENTS}"]
    if YT_COOKIES.exists():
        command += ["--cookies", str(YT_COOKIES)]
    command += ["--dump-single-json", query]
    result = subprocess.run(command,
        text=True, capture_output=True, encoding="utf-8", errors="replace")
    combined = (result.stderr or "") + (result.stdout or "")
    if "Sign in to confirm" in combined:
        return None, "youtube_bot_wall"
    if result.returncode != 0:
        return None, "youtube_error"
    try:
        data = json.loads(result.stdout)
    except Exception:
        return None, "youtube_parse_error"
    entries = data.get("entries") if isinstance(data, dict) else None
    return [
        {
            "title": entry.get("title", ""),
            "uploader": entry.get("uploader") or entry.get("channel") or "",
            "duration": entry.get("duration"),
            "url": entry.get("url") or entry.get("webpage_url") or "",
        }
        for entry in (entries or []) if isinstance(entry, dict)
    ], "ok"


def search_candidates(artist: str, title: str) -> str:
    """Return a yt-dlp search spec; yt-dlp picks the first playable hit."""
    return f"ytsearch5:{artist} {title} audio"


YT_CLIENTS = "tv_embedded,web_safari,mweb"  # bot-wall-resistant player clients
YT_COOKIES = STATE / "yt-cookies.txt"


def yt_dlp_base(staging: Path) -> list[str]:
    command = [
        "yt-dlp",
        "--no-playlist",
        "--no-warnings",
        "--socket-timeout", "20",
        "--retries", "3",
        "--extractor-args", f"youtube:player_client={YT_CLIENTS}",
    ]
    if YT_COOKIES.exists():
        command += ["--cookies", str(YT_COOKIES)]
    command += [
        "-f", "bestaudio/best",
        "--embed-metadata",
        "--embed-thumbnail",
        "-o", str(staging / "%(id)s.%(ext)s"),
    ]
    return command


def probe_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            text=True, capture_output=True, encoding="utf-8", errors="replace",
        )
        return float(out.stdout.strip())
    except Exception:
        return None


def tag_file(path: Path, row: dict) -> Path:
    """Write canonical tags using the format's native tag scheme.

    Never writes ID3 headers onto non-ID3 containers (that corrupts MP4/OGG).
    Video-container sources (webm/mkv/mp4 audio) are re-output to MP3.
    """
    from mutagen import File as MutagenFile

    def set_tags(media: Any) -> None:
        for key, value in (
            ("artist", row.get("artist", "")),
            ("title", row.get("title", "")),
            ("album", row.get("album", "")),
            ("isrc", row.get("isrc", "")),
        ):
            if not value:
                continue
            try:
                media[key] = value
            except Exception:
                continue
        try:
            media.save()
        except Exception:
            pass

    try:
        media = MutagenFile(str(path), easy=True)
    except Exception:
        media = None
    if media is None or not hasattr(media, "save"):
        if path.suffix.lower() in {".webm", ".mkv", ".mp4"}:
            target = path.with_suffix(".mp3")
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(path),
                 "-c:a", "libmp3lame", "-q:a", "0", str(target)],
                check=True,
            )
            path.unlink()
            path = target
            try:
                media = MutagenFile(str(path), easy=True)
            except Exception:
                media = None
        else:
            return path
    if media is not None:
        set_tags(media)
    return path


LRCLIB_UA = "ultimate-music-sync/0.1 (music library sync)"


def _lrclib_get(artist: str, title: str, album: str, duration_s: float | None) -> dict | None:
    params = urllib.parse.urlencode({
        "track_name": title,
        "artist_name": artist,
        **({"album_name": album} if album else {}),
        **({"duration": str(int(duration_s))} if duration_s else {}),
    })
    request = urllib.request.Request(
        f"https://lrclib.net/api/get?{params}", headers={"User-Agent": LRCLIB_UA})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception:
        return None


def _lrclib_search(artist: str, title: str) -> list[dict]:
    params = urllib.parse.urlencode({"track_name": title, "artist_name": artist})
    request = urllib.request.Request(
        f"https://lrclib.net/api/search?{params}", headers={"User-Agent": LRCLIB_UA})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8")) or []
    except Exception:
        return []


def lrclib_fetch(artist: str, title: str, album: str, duration_s: float | None,
                 prefer_synced_only: bool = False) -> str | None:
    """Exact /api/get first; then fuzzy /api/search with a duration gate.

    The search fallback is what recovers tracks LRCLIB indexes under slightly
    different spellings; duration stays the identity guard (2 s / 2 %).
    Returns synced lyrics when present, else plain lyrics (unless
    prefer_synced_only, used by the backfill so it keeps retrying later
    instead of pinning low-quality plain text)."""
    duration = int(duration_s) if duration_s else None

    def usable(entry: dict) -> str | None:
        synced = entry.get("syncedLyrics")
        plain = entry.get("plainLyrics")
        if synced:
            return synced
        if plain and not prefer_synced_only:
            return plain
        return None

    data = _lrclib_get(artist, title, album, duration)
    if data:
        text = usable(data)
        if text:
            return text

    for entry in _lrclib_search(artist, title):
        if duration and entry.get("duration"):
            delta = abs(float(entry["duration"]) - duration)
            if delta > max(2.0, duration * 0.02):
                continue
        text = usable(entry)
        if text:
            return text
    return None


def ensure_lrc(audio: Path, row: dict) -> bool:
    lrc = audio.with_suffix(".lrc")
    if lrc.exists():
        return False
    duration = probe_duration(audio)
    synced = lrclib_fetch(row.get("artist", ""), row.get("title", ""),
                          row.get("album", ""), duration)
    if not synced:
        # fallback pass without exact-album constraint
        synced = lrclib_fetch(row.get("artist", ""), row.get("title", ""), "", duration)
    if not synced:
        return False
    lrc.write_text(synced, encoding="utf-8")
    return True


def acquire_one(row: dict, staging: Path) -> tuple[str, str]:
    """Download one track via free sources; returns (status, path_or_reason)."""
    key = f"{safe_name(row.get('artist', ''))} - {safe_name(row.get('title', ''))} [{row.get('spotify_id', '')}]"
    for ext in (".ogg", ".m4a", ".mp3", ".opus", ".flac"):
        candidate = LIBRARY / (key + ext)
        if candidate.exists():
            return "already_present", str(candidate)

    artist = row.get("artist", "")
    title = row.get("title", "")

    variants = search_query_variants(artist, title)
    source_searches: list[tuple[str, list[dict]]] = []
    sc_entries: list[dict] = []
    seen_urls: set[str] = set()
    for variant in variants:
        batch = search_soundcloud(artist, title, custom_query=variant, limit=6)
        log(f"    search soundcloud ({variant[:50]}): {len(batch)} results")
        for entry in batch:
            url = entry.get("url", "")
            if url and url in seen_urls:
                continue
            seen_urls.add(url)
            entry["_query"] = variant
            sc_entries.append(entry)
    if sc_entries:
        source_searches.append(("soundcloud", sc_entries))
    yt_entries, yt_note = search_youtube(artist, title)
    if yt_entries:
        log(f"    search youtube: {len(yt_entries)} results")
        source_searches.append(("youtube", yt_entries))
    else:
        log(f"    search youtube: unavailable ({yt_note})")

    if not source_searches:
        return "no_source", yt_note if "youtube" in yt_note else "soundcloud_no_results"

    strict = bool(row.get("title") and re.search(r"nightcore|bass.?boosted|sped.?up|slowed", row["title"], re.IGNORECASE))
    best_reject = ""
    for source_name, entries in source_searches:
        for entry in entries or []:
            ok, reason = candidate_matches(
                row, entry.get("title", ""), entry.get("uploader", ""), entry.get("duration"), strict=strict)
            if not ok and f"{source_name}:{reason}" > best_reject:
                best_reject = f"{source_name}:{reason}"  # keep the most informative
            log(f"    [{source_name}] {'PASS' if ok else 'reject'} ({reason}) | {entry.get('title', '')[:60]} | {entry.get('uploader', '')[:25]} | {entry.get('duration')}s")
            if not ok:
                continue
            for old in staging.iterdir():
                old.unlink()
            result = subprocess.run(
                yt_dlp_base(staging) + [entry["url"]],
                text=True, capture_output=True, encoding="utf-8", errors="replace")
            staged = [p for p in staging.iterdir() if p.suffix.lower() in MUSIC_EXT] if staging.exists() else []
            if result.returncode != 0 or not staged:
                detail_lines = (result.stderr or result.stdout or "no output").strip().splitlines()
                reason = detail_lines[-1] if detail_lines else "no output"
                log(f"    download failed from {source_name}: {reason}")
                if source_name == "youtube" and "Sign in to confirm" in reason:
                    YOUTUBE_BLOCKED = True
                    log("    youtube: bot wall confirmed; skipping youtube for the rest of this run")
                continue
            source = max(staged, key=lambda p: p.stat().st_mtime)
            source = tag_file(source, row)
            if probe_duration(source) is None:
                source.unlink(missing_ok=True)
                continue
            target = LIBRARY / (key + source.suffix.lower())
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            return f"downloaded[{source_name}]", str(target)

    detail = f"no conservative candidate ({len(source_searches)} source(s), {len(variants)} query shape(s))"
    if best_reject:
        detail += f" :: closest {best_reject}"
    return "no_match", detail


def fill_library_lrc() -> dict:
    """Walk the whole library and fetch LRCLIB lyrics sidecars for files missing one."""
    counts = {"have": 0, "fetched": 0, "unavailable": 0, "skipped_unparseable": 0}
    audio_files = [p for p in LIBRARY.rglob("*") if p.suffix.lower() in MUSIC_EXT]
    for index, path in enumerate(audio_files, 1):
        if path.with_suffix(".lrc").exists():
            counts["have"] += 1
            continue
        try:
            from mutagen import File as MutagenFile
            media = MutagenFile(str(path), easy=True)
            artist = (media.get("artist") or [""])[0]
            title = (media.get("title") or [""])[0]
            album = (media.get("album") or [""])[0]
        except Exception:
            counts["skipped_unparseable"] += 1
            continue
        if not artist or not title:
            counts["skipped_unparseable"] += 1
            continue
        duration = probe_duration(path)
        synced = lrclib_fetch(artist, title, album, duration, prefer_synced_only=True)
        if not synced and duration:
            synced = lrclib_fetch(artist, title, "", duration, prefer_synced_only=True)
        if synced:
            path.with_suffix(".lrc").write_text(synced, encoding="utf-8")
            counts["fetched"] += 1
        else:
            counts["unavailable"] += 1
        if index % 50 == 0:
            log(f"lrc scan {index}/{len(audio_files)}: {counts}")
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Free-source fallback acquisition for missing tracks.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--csv", type=Path, default=None,
                        help="Alternate missing-tracks CSV to process.")
    parser.add_argument("--no-lock", action="store_true",
                        help="Skip the cross-process lock (orchestrator holds its own).")
    parser.add_argument("--fill-lrc", action="store_true",
                        help="Scan the library and fetch missing .lrc sidecars from LRCLIB.")
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    if args.fill_lrc:
        if not args.no_lock and not acquire_lock():
            print(json.dumps({"status": "locked"}))
            return 0
        try:
            counts = fill_library_lrc()
        finally:
            if not args.no_lock:
                release_lock()
        log(f"=== lrc fill done: {counts} ===")
        print(json.dumps(counts))
        return 0
    if not args.no_lock and not acquire_lock():
        print(json.dumps({"status": "locked"}))
        return 0
    try:
        return run_batch(args)
    finally:
        if not args.no_lock:
            release_lock()


def run_batch(args: argparse.Namespace) -> int:
    YT_ARCHIVE.touch(exist_ok=True)
    staging = STATE / "staging"
    staging.mkdir(exist_ok=True)
    rows = load_missing(args.csv)
    if args.limit:
        rows = rows[: args.limit]
    log(f"=== fallback run: {len(rows)} tracks ===")
    counts: dict[str, int] = {}
    results: list[dict] = []
    for index, row in enumerate(rows, 1):
        status, detail = acquire_one(row, staging)
        counts[status] = counts.get(status, 0) + 1
        results.append({**row, "status": status, "detail": detail})
        log(f"[{index}/{len(rows)}] {status}: {row.get('artist', '')} - {row.get('title', '')} :: {detail}")
        if status.startswith("downloaded"):
            path = Path(detail)
            if ensure_lrc(path, row):
                log(f"    lrc: fetched for {path.name}")
        time.sleep(1)
    with RESULTS.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["spotify_id", "artist", "title", "status", "detail"],
            extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    log(f"=== done: {counts} ===")
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
