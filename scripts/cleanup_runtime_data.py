"""Safely remove disposable Mr Smith runtime data according to policy.

Dry-run is the default. Use --apply to remove only age-qualified disposable
files. Baseline research, issuer evidence, commodity packs and SQLite are never
targets of this utility.
"""
from __future__ import annotations
import argparse, json, time
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--data-dir',type=Path,default=Path('data')); ap.add_argument('--policy',type=Path,default=Path('data/cleanup_policy.json')); ap.add_argument('--apply',action='store_true'); a=ap.parse_args(); policy=json.loads(a.policy.read_text()); now=time.time(); targets=[]
    rules=[('daily_snapshots',policy['daily_snapshot_days']),('end_of_day',policy['end_of_day_report_days']),('morning_brief',policy['morning_brief_days']),('background_queue',policy['processed_queue_days'])]
    for folder,days in rules:
        root=a.data_dir/folder
        if not root.exists(): continue
        for item in root.rglob('*'):
            if item.is_file() and item.stat().st_mtime < now-days*86400 and (folder!='background_queue' or item.name.endswith('.processed.json')): targets.append(item)
    log=a.data_dir/'npu_worker.log'
    if log.exists() and log.stat().st_mtime < now-policy['worker_log_days']*86400: targets.append(log)
    for path in targets:
        if a.apply: path.unlink(missing_ok=True)
    print(json.dumps({'mode':'apply' if a.apply else 'dry_run','count':len(targets),'files':[str(p) for p in targets]},indent=2))
if __name__=='__main__': main()
