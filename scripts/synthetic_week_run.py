"""Create a synthetic five-session ASX/Plug evaluation from saved packets."""
from __future__ import annotations
import argparse, json
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--batch',type=Path,default=Path('data/research_jobs/asx1000-20261029')); ap.add_argument('--output',type=Path,default=Path('data/evaluations/synthetic_week_20261030.json')); a=ap.parse_args(); rows=[]
    for path in a.batch.glob('*/market-data.json'):
        try: d=json.loads(path.read_text()); bars=d.get('bars',[])
        except Exception: continue
        if len(bars)<6: continue
        window=bars[-6:]; first=float(window[0].get('close') or 0); last=float(window[-1].get('close') or 0); vols=[float(x.get('volume') or 0) for x in window[:-1]]; avg=sum(vols)/len(vols) if vols else 0; ratio=float(window[-1].get('volume') or 0)/avg if avg else 0; change=(last/first-1)*100 if first else 0
        valid=sum(1 for x in bars[-6:-1] if float(x.get('volume') or 0)>0); liquid=valid>=3 and avg>=100000; prior=float(window[-2].get('close') or 0); prior2=float(window[-3].get('close') or 0); confirmed=abs(change)>=4 and prior2>0 and abs((prior/prior2-1)*100)>=2; rows.append({'instrument':d.get('instrument'),'week_change_pct':round(change,2),'latest_volume_ratio':round(ratio,2),'liquidity_pass':liquid,'confirmed_multi_session':confirmed,'confidence':'medium' if liquid and confirmed and (ratio>=2 or abs(change)>=8) else 'low','plug_flag':liquid and confirmed and (ratio>=2 or abs(change)>=8)})
    rows.sort(key=lambda x:x['week_change_pct'],reverse=True); flagged=[x for x in rows if x['plug_flag']]; result={'period':'synthetic last six saved sessions','source_batch':str(a.batch),'companies':len(rows),'flagged':len(flagged),'top_positive':rows[:20],'top_negative':rows[-20:],'plug_queue':flagged,'limitations':['Synthetic replay of saved history, not a live backtest.','No issuer fundamentals or slippage model.','Flags are triage signals, not recommendations.']}; a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(result,indent=2)); print(json.dumps({'companies':len(rows),'flagged':len(flagged),'output':str(a.output)},indent=2))
if __name__=='__main__': main()
