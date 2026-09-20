import csv, json, random, re, sqlite3, unicodedata, urllib.parse, urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

BASE = Path(r'%MUSICSERVER_BASE%\tools')
MUSIC = Path(r'%MUSIC_LIBRARY%')
PLAYLISTS = MUSIC / 'Playlists'
API = 'https://api.stats.fm/api/v1'
USER = os.getenv("LISTENBRAINZ_USER", "")
NAVIDROME_DB = Path(os.environ.get("NAVIDROME_DB", "%MUSICSERVER_BASE%\\data\\navidrome.db"))


def get(path, params=None):
    url = API + path
    if params:
        url += '?' + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={'Accept': 'application/json', 'User-Agent': 'music-server-suite-ai-dj/1.0'})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.loads(response.read())


def norm(value):
    text = unicodedata.normalize('NFKD', value or '').encode('ascii', 'ignore').decode().casefold()
    text = re.sub(r'\b(feat|ft|with)\b.*$', '', text)
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def read_inventory():
    path = BASE / 'local-inventory.csv'
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def navidrome_history():
    """Use Firmium/Navidrome scrobbles as the long-term feedback loop."""
    if not NAVIDROME_DB.exists():
        return {}
    db = sqlite3.connect(NAVIDROME_DB)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        """SELECT m.path, COALESCE(a.play_count, 0) play_count,
                  a.play_date, COALESCE(a.rating, 0) rating,
                  COALESCE(a.starred, 0) starred
             FROM media_file m
             LEFT JOIN annotation a
               ON a.item_id = m.id AND a.item_type = 'media_file'"""
    ).fetchall()
    db.close()
    return {str(Path(row['path'])): dict(row) for row in rows}


def local_index(rows):
    by_key = defaultdict(list)
    by_artist = defaultdict(list)
    for row in rows:
        by_key[(norm(row.get('artist')), norm(row.get('title')))].append(row)
        by_artist[norm(row.get('artist'))].append(row)
    return by_key, by_artist


def duration(row):
    try:
        from mutagen import File
        audio = File(row['path'])
        return int(getattr(getattr(audio, 'info', None), 'length', 0) or 0)
    except Exception:
        return 0


def stats_tracks(range_name):
    return get(f'/users/{USER}/top/tracks', {'range': range_name}).get('items', [])


def stats_artists(range_name='weeks'):
    return get(f'/users/{USER}/top/artists', {'range': range_name}).get('items', [])


def recent_tracks():
    return get(f'/users/{USER}/streams/recent').get('items', [])


def listenbrainz_tracks():
    path = BASE / 'listenbrainz-recommendations.json'
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding='utf-8')).get('tracks', [])
    except Exception:
        return []


def exact_listenbrainz(item, by_key):
    candidates = by_key.get((norm(item.get('artist')), norm(item.get('title'))), [])
    if not candidates:
        return None
    wanted = item.get('duration_ms')
    if not wanted:
        return candidates[0]
    for row in candidates:
        actual = duration(row) * 1000
        if actual and abs(actual - int(wanted)) <= max(2000, int(wanted) * 0.02):
            return row
    return None


def collect_listenbrainz(items, by_key, seen):
    result = []
    for item in items:
        row = exact_listenbrainz(item, by_key)
        if row and row['path'] not in seen:
            seen.add(row['path'])
            result.append(row)
    return result


def related_artists(artist_id):
    return get(f'/artists/{artist_id}/related').get('items', [])


def track_fields(item):
    track = item.get('track', item)
    artists = track.get('artists', [])
    artist = ', '.join(a.get('name', '') for a in artists if isinstance(a, dict))
    albums = track.get('albums', [])
    album = albums[0].get('name', '') if albums and isinstance(albums[0], dict) else ''
    return artist, track.get('name', ''), album


def exact_local(item, by_key):
    artist, title, _ = track_fields(item)
    candidates = by_key.get((norm(artist), norm(title)), [])
    return candidates[0] if candidates else None


def collect_exact(items, by_key, seen):
    result = []
    for item in items:
        row = exact_local(item, by_key)
        if row and row['path'] not in seen:
            seen.add(row['path'])
            result.append(row)
    return result


def collect_rows(items, seen):
    result = []
    for row in items:
        if row['path'] not in seen:
            seen.add(row['path'])
            result.append(row)
    return result


def collect_related(seed_artists, by_artist, seen, max_artists=40):
    tracks, names = [], []
    for seed in seed_artists[:8]:
        artist = seed.get('artist', seed)
        artist_id = artist.get('id')
        if not artist_id:
            continue
        try:
            related = related_artists(artist_id)
        except Exception:
            continue
        for candidate in related:
            key = norm(candidate.get('name'))
            local = by_artist.get(key, [])
            if local:
                names.append(candidate.get('name'))
                random.shuffle(local)
                for row in local[:3]:
                    if row['path'] not in seen:
                        seen.add(row['path'])
                        tracks.append(row)
            if len(names) >= max_artists:
                return tracks, names
    return tracks, names


def fill_random(rows, seen, limit=250):
    candidates = [r for r in rows if r['path'] not in seen]
    random.shuffle(candidates)
    return candidates[:limit]


def diverse_queue(buckets, target_seconds=8 * 3600):
    rng = random.Random(str(date.today()))
    for values in buckets.values():
        rng.shuffle(values)
    pattern = ['current', 'favorite', 'listenbrainz', 'discovery', 'current', 'rediscovery', 'listenbrainz', 'favorite', 'deepcut', 'current', 'discovery']
    output, seconds, last_artist, positions = [], 0, None, defaultdict(int)
    while seconds < target_seconds:
        progressed = False
        for bucket in pattern:
            values = buckets.get(bucket, [])
            while positions[bucket] < len(values):
                row = values[positions[bucket]]
                positions[bucket] += 1
                if norm(row.get('artist')) == last_artist:
                    continue
                d = duration(row)
                if d <= 0:
                    continue
                enriched = dict(row, dj_bucket=bucket, duration_seconds=d)
                output.append(enriched)
                seconds += d
                last_artist = norm(row.get('artist'))
                progressed = True
                break
            if seconds >= target_seconds:
                break
        if not progressed:
            break
    return output, seconds


def write_m3u(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        handle.write('#EXTM3U\n')
        for row in rows:
            handle.write(f"#EXTINF:{row['duration_seconds']},{row.get('artist')} - {row.get('title')} [{row['dj_bucket']}]\n")
            handle.write(str(Path(row['path']).resolve()).replace('\\', '/') + '\n')


def main():
    rows = read_inventory()
    history = navidrome_history()
    for row in rows:
        row.update(history.get(str(Path(row['path'])), {}))
    by_key, by_artist = local_index(rows)
    seen = set()
    weekly = stats_tracks('weeks')
    monthly = stats_tracks('months')
    lifetime = stats_tracks('lifetime')
    recent = recent_tracks()
    listenbrainz = listenbrainz_tracks()
    top_artists = stats_artists('weeks')

    server_recent = sorted(
        [r for r in rows if r.get('play_date')],
        key=lambda r: r.get('play_date') or '', reverse=True
    )
    server_favorites = sorted(
        [r for r in rows if r.get('starred') or int(r.get('rating') or 0) >= 4 or int(r.get('play_count') or 0) >= 3],
        key=lambda r: (bool(r.get('starred')), int(r.get('rating') or 0), int(r.get('play_count') or 0)),
        reverse=True
    )
    current = collect_rows(server_recent, seen) + collect_exact(weekly + recent, by_key, seen)
    favorite = collect_rows(server_favorites, seen) + collect_exact(monthly, by_key, seen)
    listenbrainz_local = collect_listenbrainz(listenbrainz, by_key, seen)
    rediscovery = collect_exact(list(reversed(lifetime)), by_key, seen)
    discovery, related_names = collect_related(top_artists, by_artist, seen)
    deepcut = fill_random(rows, seen)

    buckets = {
        'current': current,
        'favorite': favorite,
        'listenbrainz': listenbrainz_local,
        'discovery': discovery,
        'rediscovery': rediscovery,
        'deepcut': deepcut,
    }
    queue, seconds = diverse_queue(buckets)
    output = PLAYLISTS / 'AI DJ - Daily.m3u8'
    write_m3u(output, queue)

    missing = []
    for item in weekly:
        if not exact_local(item, by_key):
            artist, title, album = track_fields(item)
            track = item.get('track', {})
            ids = track.get('externalIds', {}).get('spotify', [])
            missing.append({'spotify_id': ids[0] if ids else '', 'artist': artist, 'title': title, 'album': album, 'source': 'statsfm-weekly'})
    for item in listenbrainz:
        if not exact_listenbrainz(item, by_key):
            missing.append({
                'spotify_id': '', 'artist': item.get('artist', ''),
                'title': item.get('title', ''), 'album': '',
                'source': 'listenbrainz-cf',
            })
    with (BASE / 'ai-dj-missing-recommendations.csv').open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=['spotify_id', 'artist', 'title', 'album', 'source'])
        writer.writeheader(); writer.writerows(missing)

    summary = {
        'date': str(date.today()),
        'tracks': len(queue),
        'hours': round(seconds / 3600, 2),
        'target_met': seconds >= 8 * 3600,
        'buckets_in_queue': dict((name, sum(r['dj_bucket'] == name for r in queue)) for name in buckets),
        'candidate_counts': dict((name, len(values)) for name, values in buckets.items()),
        'related_local_artists': sorted(set(related_names)),
        'missing_current_recommendations': len(missing),
        'listenbrainz_recommendations': len(listenbrainz),
        'listenbrainz_local_matches': len(listenbrainz_local),
        'navidrome_history_tracks': sum(bool(r.get('play_count')) for r in rows),
        'playlist': str(output),
    }
    (BASE / 'ai-dj-daily-summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
