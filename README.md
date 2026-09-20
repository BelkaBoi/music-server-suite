# Music Server Suite

Self-hosted, rights-aware music library manager: it keeps an offline music
library in sync with your Spotify liked tracks, acquires missing tracks
through your own Spotify account (Dotify/Zotify) and falls back to free
sources (SoundCloud, YouTube), attaches synced lyrics (.lrc) from LRCLIB,
exposes the library through Navidrome, and publishes it anywhere via a
Cloudflare Tunnel — all managed from a single dark desktop control panel
(Windows/macOS/Linux).

```
Spotify liked ──▶ reconcile ──▶ Dotify (own account, vorbis-high)
                     │
                     ▼
              free-source fallback (SoundCloud ▸ YouTube)
                     │
                     ▼
        library + .lrc sidecars ──▶ Navidrome ──▶ Cloudflare Tunnel
                     │
              desktop control panel (tkinter)
```

## What's inside

| Path | What it is |
|---|---|
| `src/ultimate_music_sync/` | Core library: reconcile, acquisition, lyrics, state DB |
| `control-center/` | Desktop control panel (tkinter) + tests |
| `tools/` | Silent launchers, tunnel health watchdog, node rotation |
| `tests/` | Core test suite |
| `docs/` | Setup and operations guide |

## Features

- **Rights-aware reconciliation** — matches your Spotify saved tracks to local
  files by ISRC first, then conservative metadata (version markers, 2s/2%
  duration window). Never collapses remixes, nightcores, or live versions.
- **Dotify-first acquisition** — downloads missing tracks through *your own*
  Spotify account at the highest available quality (Vorbis 320), with
  automatic detection of Spotify-side audio-key rejection bursts.
- **Free-source fallback** — SoundCloud search (order- and script-aware query
  variants, including Cyrillic↔Latin) with conservative matching, plus YouTube
  with bot-wall circuit breaking and optional cookie file support.
- **Synced lyrics** — LRCLIB `.lrc` sidecars for every download and a
  whole-library backfill (synced preferred, plain-only gaps stay retryable).
- **Control panel** — status pills, colored health states, live acquisition
  progress, one-click actions, responsive layout at any window size, DPI
  aware, cross-platform.
- **Silent scheduled operation** — everything runs windowless; a tunnel health
  watchdog with per-probe latency logging self-heals proxy and connector
  failures, including VPN-node rotation.

## Requirements

- Python 3.11+
- [Navidrome](https://www.navidrome.org/) (any platform)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) + FFmpeg (acquisition fallback)
- Optional: [Dotify/Zotify](https://github.com/zotify-dev/zotify) venv with
  your own Spotify account credentials
- Optional: [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
  for remote access, and FlClash/Clash if your network needs a proxy

## Install

```bash
git clone https://github.com/belkaboi/music-server-suite
cd music-server-suite
python -m venv .venv
# Windows:
.venv\Scripts\pip install -e ".[dev]"
# macOS/Linux:
.venv/bin/pip install -e ".[dev]"
```

## Configure

Everything is driven by environment variables (all optional, sensible
defaults):

| Variable | Default | Meaning |
|---|---|---|
| `MUSIC_LIBRARY` | `~/Music/Spotify Liked` | Your music library root |
| `MUSICSERVER_BASE` | `~/Navidrome` | Navidrome installation directory |
| `UMS_PROJECT` | repo checkout dir | Sync project/state directory |
| `UMS_STATE` | `<repo>/.real-state` | State/reports directory |
| `SPOTIFY_TOKEN_FILE` | `<tools>/spotify-token.json` | Spotify OAuth token |
| `DOTIFY_BIN` | dotify on PATH | Dotify/Zotify executable |
| `DOTIFY_STATE` | `~/.dotify` | Dotify state directory |

Spotify API access needs your own
[Spotify developer app](https://developer.spotify.com/dashboard) credentials
(`SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET`).

## Use

```bash
# Interactive control panel
python control-center/server_control.py

# One-shot: reconcile + acquire missing + lyrics backfill
python -m ultimate_music_sync.auto_acquire

# Free-source fallback only
python -m ultimate_music_sync.acquire --csv missing.csv

# Library-wide lyrics backfill
python -m ultimate_music_sync.acquire --fill-lrc

# Run the test suite
pytest
```

Schedule `tools/hourly-sync.cmd.example` (adapt paths, drop the `.example`)
with Task Scheduler / cron / launchd for fully automatic operation. On
Windows always schedule through the silent `.vbs` launchers — scheduling a
`.cmd` directly pops console windows on every run.

## YouTube cookies

If YouTube bot-walls your IP, export your browser cookies once:

```bash
# Close Chrome first (it locks the cookie DB), then:
yt-dlp --cookies-from-browser chrome --cookies .real-state/yt-cookies.txt --simulate --skip-download https://www.youtube.com/
```

`acquire.py` picks up `.real-state/yt-cookies.txt` automatically.

## Platform notes

- **Windows** — full feature set, silent `.vbs` launchers, DPI-aware panel.
- **Linux** — full feature set; tunnel control via native systemctl; XDG
  autostart entries are generated for login startup.
- **macOS** — panel and acquisition work; tunnel control reports unsupported
  (systemd-specific); launchd plists are generated for login startup.

## Rights & ethics

This tool only downloads what you are entitled to keep: your own Spotify
account's streams via Dotify/Zotify, and public/free sources (SoundCloud,
YouTube) for tracks unavailable elsewhere. It never bypasses DRM, never
exports other people's subscription streams, and never touches
Widevine/protected content. Missing tracks that can't be acquired legitimately
are reported as missing, not faked.

## License

MIT
