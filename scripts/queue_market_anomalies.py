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
        if len(bars)<5: continue
        last, prev=bars[-1], bars[-2]; volumes=[float(x.get('volume') or 0) for x in bars]; valid=[v for v in volumes[:-1] if v>0]; avg=sum(valid)/len(valid) if valid else 0; latest_volume=float(last.get('volume') or 0)
        if len(valid)<3 or avg<100000 or latest_volume<=0: continue
        close=float(last['close']); previous=float(prev['close']); change=(close/previous-1)*100 if previous else 0; ratio=latest_volume/avg; prior=float(bars[-3].get('close') or 0); prior_change=(previous/prior-1)*100 if prior else 0; confirmed=abs(change)>=4 and abs(prior_change)>=2
        if confirmed and (ratio>=2 or abs(change)>=8):
            text=f"{data.get('name',data.get('symbol'))} confirmed move {change:.2f}% with volume ratio {ratio:.2f}"
            target=a.queue_dir/(path.stem+'.json'); target.write_text(json.dumps({'title':'Confirmed market anomaly','summary':text,'symbol':data.get('symbol'),'change_pct':round(change,2),'prior_change_pct':round(prior_change,2),'volume_ratio':round(ratio,2),'confidence':'medium','liquidity_proxy':'average reported volume >= 100000'},indent=2),encoding='utf-8'); queued+=1
    print(json.dumps({'queued':queued,'snapshot_dir':str(a.snapshot_dir)}))
if __name__=='__main__': main()
