import csv, hashlib, json, random
from datetime import date
from pathlib import Path

BASE = Path(r'%MUSICSERVER_BASE%\tools')
MUSIC = Path(r'%MUSIC_LIBRARY%')
PLAYLIST_DIR = MUSIC / 'Playlists'
INVENTORY = BASE / 'local-inventory.csv'
STATSFM = BASE / 'statsfm-tracks.csv'
OUTPUT = BASE / 'ai-dj-channel.m3u8'
NAVIDROME_OUTPUT = PLAYLIST_DIR / 'AI DJ - Daily.m3u8'
SUMMARY = BASE / 'ai-dj-channel-summary.json'
TARGET_SECONDS = 8 * 60 * 60
FAMILIARITY_RATIO = 0.60


def norm(value):
    return ' '.join((value or '').casefold().split())


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def duration_seconds(path):
    try:
        from mutagen import File
        audio = File(path)
        return int(getattr(getattr(audio, 'info', None), 'length', 0) or 0)
    except Exception:
        return 0


def daily_rng():
    seed = int(hashlib.sha256(str(date.today()).encode()).hexdigest()[:16], 16)
    return random.Random(seed)


def interleave(familiar, discovery, rng):
    rng.shuffle(familiar)
    rng.shuffle(discovery)
    result, seconds, familiar_seconds = [], 0, 0
    last_artist = None
    while seconds < TARGET_SECONDS and (familiar or discovery):
        current_ratio = familiar_seconds / seconds if seconds else 0
        prefer_familiar = current_ratio < FAMILIARITY_RATIO
        primary = familiar if prefer_familiar else discovery
        secondary = discovery if prefer_familiar else familiar
        candidates = [x for x in primary if norm(x['artist']) != last_artist]
        if not candidates:
            candidates = [x for x in secondary if norm(x['artist']) != last_artist]
        if not candidates:
            candidates = primary or secondary
        if not candidates:
            break
        track = rng.choice(candidates)
        source = familiar if track in familiar else discovery
        source.remove(track)
        result.append(track)
        seconds += int(track['duration_seconds'])
        if track['bucket'] == 'familiar':
            familiar_seconds += int(track['duration_seconds'])
        last_artist = norm(track['artist'])
    return result, seconds, familiar_seconds


def write_m3u(path, tracks):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        handle.write('#EXTM3U\n')
        for track in tracks:
            handle.write(f"#EXTINF:{track['duration_seconds']},{track['artist']} - {track['title']}\n")
            handle.write(str(Path(track['path']).resolve()).replace('\\', '/') + '\n')


def main():
    inventory = read_csv(INVENTORY)
    stats = read_csv(STATSFM)
    ranked = {(norm(x.get('artist')), norm(x.get('title'))): i for i, x in enumerate(stats)}
    enriched = []
    for source in inventory:
        row = dict(source)
        row['duration_seconds'] = duration_seconds(row['path'])
        key = (norm(row.get('artist')), norm(row.get('title')))
        row['bucket'] = 'familiar' if key in ranked else 'discovery'
        row['statsfm_rank'] = ranked.get(key)
        if row['duration_seconds'] > 0:
            enriched.append(row)
    familiar = [x for x in enriched if x['bucket'] == 'familiar']
    discovery = [x for x in enriched if x['bucket'] == 'discovery']
    tracks, seconds, familiar_seconds = interleave(familiar, discovery, daily_rng())
    write_m3u(OUTPUT, tracks)
    write_m3u(NAVIDROME_OUTPUT, tracks)
    summary = {
        'date': str(date.today()),
        'tracks': len(tracks),
        'estimated_seconds': seconds,
        'estimated_hours': round(seconds / 3600, 2),
        'target_seconds': TARGET_SECONDS,
        'target_met': seconds >= TARGET_SECONDS,
        'familiar_tracks': sum(x['bucket'] == 'familiar' for x in tracks),
        'discovery_tracks': sum(x['bucket'] == 'discovery' for x in tracks),
        'familiarity_time_ratio': round(familiar_seconds / seconds, 3) if seconds else 0,
        'statsfm_reference_tracks': len(stats),
        'navidrome_playlist': str(NAVIDROME_OUTPUT),
    }
    SUMMARY.write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
