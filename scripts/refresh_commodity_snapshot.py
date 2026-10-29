"""Capture the v1 commodity and macro market pack with bounded pauses."""
from __future__ import annotations
import argparse, json, time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

def fetch(symbol: str) -> dict:
    url=f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol,safe='')}?range=5d&interval=1d"
    req=Request(url,headers={"User-Agent":"MrSmith-local-commodity-refresh/1.0"})
    with urlopen(req,timeout=20) as response: data=json.loads(response.read())
    result=data['chart']['result'][0]; q=result['indicators']['quote'][0]; bars=[]
    for ts,close,volume in zip(result.get('timestamp',[]),q.get('close',[]),q.get('volume',[])):
        if close is not None: bars.append({'observed_on':datetime.fromtimestamp(ts,timezone.utc).date().isoformat(),'close':close,'volume':volume})
    return {'symbol':symbol,'bars':bars,'retrieved_at':datetime.now(timezone.utc).isoformat(),'source':'Yahoo Finance public chart endpoint (unofficial)'}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--universe',type=Path,default=Path('data/commodities/commodity_universe_v1.json')); ap.add_argument('--data-dir',type=Path,default=Path('data')); ap.add_argument('--pause-seconds',type=float,default=2); a=ap.parse_args()
    day=datetime.now(timezone.utc).date().isoformat(); out=a.data_dir/'commodities'/'snapshots'/day; out.mkdir(parents=True,exist_ok=True); items=json.loads(a.universe.read_text())['instruments']; summary={'date':day,'completed':0,'failed':0,'items':[]}
    for item in items:
        try:
            payload=fetch(item['symbol']); (out/(item['symbol'].replace('=','_').replace('^','index_')+'.json')).write_text(json.dumps({**item,**payload},indent=2)); summary['completed']+=1; summary['items'].append({'symbol':item['symbol'],'status':'ok','bars':len(payload['bars'])})
        except Exception as exc: summary['failed']+=1; summary['items'].append({'symbol':item['symbol'],'status':'error','error':type(exc).__name__})
        time.sleep(max(0,a.pause_seconds))
    (out/'summary.json').write_text(json.dumps(summary,indent=2)); print(json.dumps(summary,indent=2))
if __name__=='__main__': main()
