# Setup & Operations Guide

This guide walks through a full deployment on Windows, with notes where
macOS/Linux differ.

## 1. Navidrome

1. Download Navidrome for your platform and place the binary in `~/Navidrome`
   (or set `MUSICSERVER_BASE`).
2. Create a minimal `navidrome.toml`:

   ```toml
   Address = "0.0.0.0"
   Port = 4533
   MusicFolder = '/path/to/your/library'   # your library root
   ```

3. Start it: `./navidrome.exe --configfile navidrome.toml`
4. Verify: `curl http://127.0.0.1:4533/ping` returns `200`.

## 2. Spotify API + Dotify

1. Create an app at <https://developer.spotify.com/dashboard> with redirect
   URI `http://127.0.0.1:8989/callback`.
2. Export `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`.
3. Install Dotify/Zotify into a venv and run its auth once so
   `~/.dotify/librespot_credentials.json` exists (this is what makes
   acquisition run under *your* account).
4. Point `DOTIFY_BIN` at the executable and `DOTIFY_STATE` at its state dir if
   non-default.

## 3. Acquisition pipeline

```bash
python -m ultimate_music_sync.auto_acquire
```

Stages: reconcile saved-vs-library → Dotify (highest quality first, aborts on
audio-key rejection bursts) → SoundCloud/YouTube fallback with conservative
matching → LRCLIB lyrics backfill.

Every run writes `.real-state/auto-acquire-report.json` with before/after
missing counts and per-stage summaries.

### Audio-key rejection bursts

Spotify sometimes rejects Librespot audio-key requests in bursts. The
connector aborts after 8 consecutive rejections and defers those tracks to
the free-source fallback. Retrying the same route never helps; retry on
another day.

### YouTube bot wall

Anonymous YouTube media extraction is IP-blocked in many places. Provide
cookies (see README) or rely on SoundCloud. The module trips a circuit
breaker on the first confirmed wall so runs don't stall.

## 4. Scheduled operation (Windows)

Use the Task Scheduler with these conventions:

- task action: `wscript.exe C:\path\to\tools\silent-hourly-sync.vbs`
  — **never schedule a `.cmd` directly**, it pops a console window every run;
- the VBS wraps the `.cmd` with `shell.Run ..., 0, True` (hidden window, real
  exit code propagation).

## 5. Remote access (Cloudflare Tunnel)

1. Create a named tunnel in the Cloudflare dashboard, route
   `music.example.com` to `http://localhost:4533`.
2. Install `cloudflared` **once** as the single connector (service install
   with the tunnel token). Running a second connector for the same tunnel
   causes intermittent 530/502 — the edge load-balances onto the flaky one.
3. Schedule `tools/cloudflared-healthcheck.vbs` every 5 minutes. It probes
   with per-probe latency logging, restarts the FlClash proxy core if it
   died, rotates the VPN group node if a node kills tunnel TLS, and queues a
   connector restart — all windowless.

Reading `cloudflared-health.log`: each line carries per-probe
`#N=<http_code>/<latency_ms>` timings; `530` = edge without a live connector,
`1010` = WAF blocked the probe signature (use a browser UA), `502` = edge OK
but origin unreachable.

## 6. Control panel

```bash
python control-center/server_control.py
```

Shows colored status pills (server, local/public endpoints, tunnel, sync
coverage, acquisition state, library size, lyrics coverage, next/last run),
buttons for Start/Stop/Restart/Sync Now/Acquire Now/log shortcuts, and
autostart/automation toggles. Acquire Now auto-refreshes every 15 s while the
run holds the lock.

## 7. Troubleshooting quick reference

| Symptom | Likely cause / fix |
|---|---|
| Public 530 | Connector down: check service, then FlClash core, then VPN node |
| Public 1010 on probes | WAF UA filter: probe with a browser UA |
| Public 502 | Edge OK, origin down: check Navidrome + port forward |
| Dotify: `LIBRESPOT_AUDIO_KEY_REJECTED` | Spotify-side; stop retrying, switch to fallback, retry another day |
| SoundCloud search returns 0 | Order matters: try title-first / title-only variants (automated) |
| Lyrics missing | LRCLIB has no entry for the track (common for SoundCloud-native artists); retries continue hourly |
