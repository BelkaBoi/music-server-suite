import csv, hashlib, json, random, subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(r'%MUSIC_LIBRARY%')
OUT = Path(r'%MUSICSERVER_BASE%\tools')
AUDIO = {'.mp3','.flac','.m4a','.ogg','.opus','.wav','.aac','.alac'}


def tag_file(path):
    try:
        from mutagen import File
        audio = File(path, easy=True)
        tags = audio or {}
        return {
            'artist': '; '.join(tags.get('artist', ['Unknown Artist'])),
            'album': '; '.join(tags.get('album', ['Unknown Album'])),
            'title': '; '.join(tags.get('title', [path.stem])),
            'year': '; '.join(tags.get('date', ['']))
        }
    except Exception:
        return {'artist':'Unknown Artist','album':'Unknown Album','title':path.stem,'year':''}


def inventory():
    rows=[]
    if ROOT.exists():
        for path in sorted(ROOT.rglob('*')):
            if path.is_file() and path.suffix.lower() in AUDIO:
                t=tag_file(path)
                rows.append({'path':str(path),'filename':path.name,**t})
    return rows


def write_csv(rows):
    fields=['path','filename','artist','album','title','year']
    with (OUT/'local-inventory.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)


def daily_seed():
    from datetime import date
    return int(hashlib.sha256(str(date.today()).encode()).hexdigest()[:8],16)


def queues(rows):
    groups=defaultdict(list)
    for row in rows: groups[row['artist']].append(row)
    rng=random.Random(daily_seed()); artists=list(groups); rng.shuffle(artists)
    ordered=[]
    while artists:
        for artist in list(artists):
            if groups[artist]: ordered.append(groups[artist].pop(0))
            if not groups[artist]: artists.remove(artist)
    for name, subset in [('ai-dj-queue.m3u8',ordered),('ai-dj-favorites.m3u8',rows)]:
        with (OUT/name).open('w',encoding='utf-8') as f:
            f.write('#EXTM3U\n'); f.writelines(r['path'].replace('\\','/')+'\n' for r in subset)


def report(rows):
    (OUT/'library-summary.json').write_text(json.dumps({'tracks':len(rows),'artists':len(set(r['artist'] for r in rows)),'albums':len(set((r['artist'],r['album']) for r in rows))},indent=2),encoding='utf-8')

if __name__=='__main__':
    rows=inventory(); write_csv(rows); queues(rows); report(rows)
    print(json.dumps({'tracks':len(rows),'artists':len(set(r['artist'] for r in rows)),'albums':len(set((r['artist'],r['album']) for r in rows))}))
