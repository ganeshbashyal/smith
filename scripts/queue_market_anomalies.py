"""Queue simple, explainable market anomalies for Plug after daily capture."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--snapshot-dir',type=Path,required=True); ap.add_argument('--queue-dir',type=Path,default=Path('data/background_queue')); a=ap.parse_args(); a.queue_dir.mkdir(parents=True,exist_ok=True); queued=0
    for path in a.snapshot_dir.glob('*.json'):
        if path.name=='summary.json': continue
        try: data=json.loads(path.read_text(encoding='utf-8')); bars=data.get('bars',[])
        except (OSError,json.JSONDecodeError): continue
        if len(bars)<2: continue
        last, prev=bars[-1], bars[-2]; close=float(last['close']); previous=float(prev['close']); change=(close/previous-1)*100 if previous else 0; volumes=[float(x.get('volume') or 0) for x in bars[:-1]]; avg=sum(volumes)/len(volumes) if volumes else 0; ratio=float(last.get('volume') or 0)/avg if avg else 0
        if abs(change)>=4 or ratio>=2:
            text=f"{data.get('name',data.get('symbol'))} price change {change:.2f}% volume ratio {ratio:.2f}"
            target=a.queue_dir/(path.stem+'.json'); target.write_text(json.dumps({'title':'Market anomaly','summary':text,'symbol':data.get('symbol'),'change_pct':round(change,2),'volume_ratio':round(ratio,2)},indent=2),encoding='utf-8'); queued+=1
    print(json.dumps({'queued':queued,'snapshot_dir':str(a.snapshot_dir)}))
if __name__=='__main__': main()
