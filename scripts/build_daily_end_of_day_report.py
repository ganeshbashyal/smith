"""Create a concise dated end-of-day market report from local snapshots."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--snapshot-dir',type=Path,required=True); ap.add_argument('--output-dir',type=Path,default=Path('data/end_of_day')); a=ap.parse_args(); rows=[]; failures=[]
    for path in a.snapshot_dir.glob('*.json'):
        if path.name=='summary.json': continue
        try: d=json.loads(path.read_text()); bars=d.get('bars',[]); last=bars[-1] if bars else {}; prev=bars[-2] if len(bars)>1 else {}; change=((float(last.get('close'))/float(prev.get('close')))-1)*100 if last.get('close') and prev.get('close') else None; rows.append((d.get('symbol',path.stem),change,last.get('volume')))
        except Exception: failures.append(path.name)
    rows.sort(key=lambda x: x[1] if x[1] is not None else -999, reverse=True); a.output_dir.mkdir(parents=True,exist_ok=True); date=a.snapshot_dir.name; lines=[f'# Mr Smith end-of-day report — {date}','',f'Instruments captured: {len(rows)}','', '## Largest positive moves']
    lines += [f'- `{s}`: {c:+.2f}% | volume {v}' for s,c,v in rows[:10] if c is not None]; lines += ['', '## Largest negative moves']; lines += [f'- `{s}`: {c:+.2f}% | volume {v}' for s,c,v in rows[-10:] if c is not None]; lines += ['', '## Data quality', f'- Unreadable snapshots: {len(failures)}', '- Source: Yahoo Finance public chart endpoint (unofficial; may be delayed).', '- This report is analytical context, not investment advice.']
    (a.output_dir/f'{date}.md').write_text('\n'.join(lines),encoding='utf-8'); print(json.dumps({'date':date,'instruments':len(rows),'failures':len(failures)}))
if __name__=='__main__': main()
