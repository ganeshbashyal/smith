"""Build an explainable multi-factor ASX screen from entity profiles."""
from __future__ import annotations
import argparse, json
from datetime import date
from pathlib import Path

def score(item):
    t=item.get('technical') or {}; r=t.get('returns_pct') or {}; ma=t.get('moving_averages') or {}; latest=t.get('latest_close'); p=0.0; why=[]
    if r.get('1y') is not None: p+=max(-4,min(8,r['1y']/12)); why.append(f"1Y {r['1y']:+.1f}%")
    if r.get('3m') is not None and r['3m']>0: p+=2; why.append(f"3M {r['3m']:+.1f}%")
    if latest and ma.get('ma50') and latest>ma['ma50']: p+=1.5; why.append('above MA50')
    if latest and ma.get('ma200') and latest>ma['ma200']: p+=2; why.append('above MA200')
    if t.get('flow_proxy_60d')=='accumulation_proxy': p+=2.5; why.append('accumulation proxy')
    if t.get('flow_proxy_60d')=='distribution_proxy': p-=3; why.append('distribution proxy')
    if (t.get('volume_ratio20') or 0)>=1.5: p+=1; why.append('volume expansion')
    if t.get('rsi14') is not None and 45<=t['rsi14']<=70: p+=1; why.append('constructive RSI')
    if t.get('rsi14') is not None and t['rsi14']>80: p-=1.5; why.append('RSI overheated')
    gaps=len(item.get('evidence_gaps') or []); p-=min(4,gaps*.75)
    if gaps: why.append(f'{gaps} evidence gaps')
    if t.get('status')!='available': p-=5; why.append('technical data unavailable')
    return round(p,2),why

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profiles',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); ap.add_argument('--limit',type=int,default=50); a=ap.parse_args()
    source=json.loads(a.profiles.read_text(encoding='utf-8')); rows=[]
    for x in source.get('profiles',[]):
        p,w=score(x); t=x.get('technical') or {}; r=t.get('returns_pct') or {}
        rows.append({'instrument':x['instrument'],'company':x.get('company',''),'sector':x.get('sector',''),'score':p,'confidence':'high' if not x.get('evidence_gaps') and t.get('status')=='available' else 'screening_only','reasons':w,'flow':t.get('flow_proxy_60d'),'rsi14':t.get('rsi14'),'return_3m_pct':r.get('3m'),'return_1y_pct':r.get('1y'),'volume_ratio20':t.get('volume_ratio20'),'evidence_gaps':x.get('evidence_gaps',[])})
    rows.sort(key=lambda x:(-x['score'],x['instrument'])); out={'snapshot':'asx_multi_factor_screen','created_on':date.today().isoformat(),'method':'Trend, momentum, MA alignment, flow proxy, volume, RSI, data-quality and evidence-gap scoring; screening only.','candidate_count':len(rows),'deep_dive_queue':rows[:a.limit],'full_ranked':rows}; a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps({'profiles':len(rows),'queue':min(a.limit,len(rows))},indent=2))
if __name__=='__main__': main()
