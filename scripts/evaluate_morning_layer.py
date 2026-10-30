"""Smoke-test Plug morning output and commodity freshness metadata."""
from __future__ import annotations
import json
from datetime import date
from pathlib import Path

def main():
    brief=Path('data/morning_brief'); today=brief/(date.today().isoformat()+'.json'); payload=json.loads(today.read_text()) if today.exists() else None
    result={'plug_file_present':bool(payload),'plug_items':len(payload.get('items',[])) if payload else 0,'commodity_summary_present':(Path('data/commodities/snapshots')/date.today().isoformat()/'summary.json').exists(),'status':'pass' if payload else 'review'}
    out=Path('data/evaluations'); out.mkdir(parents=True,exist_ok=True); (out/'morning_layer_latest.json').write_text(json.dumps(result,indent=2)); print(json.dumps(result,indent=2))
if __name__=='__main__': main()
