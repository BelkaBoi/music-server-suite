import csv, json, re, sys
from pathlib import Path

OUT=Path(r'%MUSICSERVER_BASE%\tools')

def pick(row,*keys):
    low={str(k).strip().lower():v for k,v in row.items()}
    for k in keys:
        if k in low: return low[k]
    return ''

def main(path):
    with Path(path).open(encoding='utf-8-sig',newline='') as f: source=list(csv.DictReader(f))
    rows=[]
    for r in source:
        artist=pick(r,'artist','artist name','artistname')
        title=pick(r,'track','track name','trackname','title','song')
        album=pick(r,'album','album name','albumname')
        if artist and title: rows.append({'spotify_id':pick(r,'spotify track uri','track uri','uri','spotify_id'),'artist':artist,'title':title,'album':album})
    out=OUT/'statsfm-tracks.csv'
    with out.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['spotify_id','artist','title','album']); w.writeheader(); w.writerows(rows)
    print(json.dumps({'status':'ok','input_rows':len(source),'tracks':len(rows),'output':str(out)}))

if __name__=='__main__':
    if len(sys.argv)!=2: print(json.dumps({'status':'blocked','reason':'Provide a stats.fm CSV export path'})); raise SystemExit(2)
    main(sys.argv[1])
