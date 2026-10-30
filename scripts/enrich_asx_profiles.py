"""Add deterministic sector-relative and liquidity enrichment to ASX profiles."""
from __future__ import annotations
import argparse, json, statistics
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--profiles',type=Path,required=True); ap.add_argument('--universe',type=Path,required=True); ap.add_argument('--output',type=Path,required=True); args=ap.parse_args(); payload=json.loads(args.profiles.read_text()); universe={x['instrument']:x for x in json.loads(args.universe.read_text()).get('companies',[])}; groups={}
    for x in payload.get('profiles',[]):
        groups.setdefault(x.get('sector') or 'Unknown',[]).append((x.get('technical') or {}).get('returns_pct',{}).get('1y'))
    medians={k:statistics.median([v for v in vals if v is not None]) for k,vals in groups.items() if any(v is not None for v in vals)}
    for x in payload.get('profiles',[]):
        t=x.get('technical') or {}; avg=float(t.get('average_volume20') or 0); ret=(t.get('returns_pct') or {}).get('1y'); sector=x.get('sector') or 'Unknown'; u=universe.get(x.get('instrument'),{}); 
        tier='unknown' if avg<=0 else 'thin' if avg<100000 else 'liquid' if avg>=1000000 else 'moderate'; x['enrichment']={'liquidity_tier':tier,'average_volume20':avg,'market_cap_band':'unavailable_pending_approved_source','company_type':'established_or_microcap_unclassified','sector_return_1y_median':medians.get(sector),'relative_to_sector_1y_pct':round(ret-medians[sector],2) if ret is not None and sector in medians else None,'sector_source':'current profile cohort median'}; x['sector']=x.get('sector') or u.get('sector') or ''
    payload['enrichment']={'method':'Deterministic cohort medians and volume tiers; market cap remains unavailable until an approved source is attached.','sectors':len(medians)}; args.output.parent.mkdir(parents=True,exist_ok=True); args.output.write_text(json.dumps(payload,indent=2)); print(json.dumps({'profiles':len(payload.get('profiles',[])),'sectors':len(medians)},indent=2))
if __name__=='__main__': main()
