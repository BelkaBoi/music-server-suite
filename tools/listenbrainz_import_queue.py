import csv, json, re, unicodedata
from pathlib import Path

BASE = Path(r'%MUSICSERVER_BASE%\tools')
OUT = BASE / 'listenbrainz-import-candidates.csv'


def norm(value):
    text = unicodedata.normalize('NFKD', value or '').encode('ascii', 'ignore').decode().casefold()
    return re.sub(r'[^a-z0-9]+', ' ', text).strip()


def main():
    source = json.loads((BASE / 'listenbrainz-recommendations.json').read_text(encoding='utf-8'))
    with (BASE / 'local-inventory.csv').open(encoding='utf-8-sig', newline='') as handle:
        local = list(csv.DictReader(handle))
    local_keys = {(norm(x.get('artist')), norm(x.get('title'))) for x in local}
    rows = []
    for item in source.get('tracks', []):
        if (norm(item.get('artist')), norm(item.get('title'))) in local_keys:
            continue
        rows.append({
            'recording_mbid': item.get('recording_mbid', ''),
            'artist': item.get('artist', ''),
            'title': item.get('title', ''),
            'duration_ms': item.get('duration_ms') or '',
            'isrc': (item.get('isrcs') or [''])[0],
            'source': 'listenbrainz-cf',
            'rights_status': 'needs-authorized-source',
            'search_query': f"{item.get('artist', '')} - {item.get('title', '')}",
        })
    with OUT.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ['recording_mbid','artist','title','duration_ms','isrc','source','rights_status','search_query'])
        writer.writeheader(); writer.writerows(rows)
    print(json.dumps({'status': 'ok', 'missing_authorized_imports': len(rows), 'output': str(OUT)}))


if __name__ == '__main__':
    main()
