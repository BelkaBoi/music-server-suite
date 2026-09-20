import json, os, re, unicodedata, urllib.parse, urllib.request
from pathlib import Path

OUT = Path(r'%MUSICSERVER_BASE%\tools')
USER = os.getenv('LISTENBRAINZ_USER', '').strip()
COUNT = int(os.getenv('LISTENBRAINZ_COUNT', '250'))
UA = 'music-server-suite-dj/1.0'


def get_json(url, token=''):
    headers = {'Accept': 'application/json', 'User-Agent': UA}
    if token:
        headers['Authorization'] = f'Token {token}'
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=45) as response:
        return json.loads(response.read())


def artist_from_rels(rels):
    names = []
    for rel in rels or []:
        name = rel.get('artist_name')
        if name and name not in names:
            names.append(name)
    return ', '.join(names)


def main():
    token = os.getenv('LISTENBRAINZ_TOKEN', '').strip()
    url = f'https://api.listenbrainz.org/1/cf/recommendation/user/{urllib.parse.quote(USER)}/recording?count={COUNT}'
    recommendation = get_json(url, token)
    scored = recommendation.get('payload', {}).get('mbids', [])
    ids = [x.get('recording_mbid') for x in scored if x.get('recording_mbid')]
    metadata = {}
    for start in range(0, len(ids), 50):
        batch = ids[start:start + 50]
        endpoint = 'https://api.listenbrainz.org/1/metadata/recording/?' + urllib.parse.urlencode(
            [('recording_mbids', ','.join(batch))]
        )
        try:
            metadata.update(get_json(endpoint, token))
        except Exception:
            continue
    score_by_id = {x.get('recording_mbid'): x for x in scored}
    tracks = []
    unresolved = []
    for mbid in ids:
        record = (metadata.get(mbid) or {}).get('recording') or {}
        title = record.get('name', '')
        artist = artist_from_rels(record.get('rels'))
        if not title or not artist:
            unresolved.append(mbid)
            continue
        score = score_by_id.get(mbid, {})
        tracks.append({
            'recording_mbid': mbid,
            'artist': artist,
            'title': title,
            'duration_ms': record.get('length'),
            'isrcs': record.get('isrcs') or [],
            'first_release_date': record.get('first_release_date'),
            'score': score.get('score'),
            'latest_listened_at': score.get('latest_listened_at'),
            'source': 'listenbrainz-cf',
        })
    result = {
        'status': 'ok', 'user': USER, 'requested': COUNT,
        'recommendations': len(scored), 'resolved_tracks': len(tracks),
        'unresolved_mbids': unresolved, 'tracks': tracks,
    }
    target = OUT / 'listenbrainz-recommendations.json'
    target.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({k: v for k, v in result.items() if k != 'tracks' and k != 'unresolved_mbids'} | {'output': str(target)}))


if __name__ == '__main__':
    main()
