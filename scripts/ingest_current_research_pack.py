"""Ingest the current commodity and issuer research pack into local Smith memory."""
from __future__ import annotations
import argparse, hashlib, json
from pathlib import Path
from assistant_api.storage import Store
from assistant_api.finance.evidence import extract_pdf_text, split_passages

def add(store, title, category, source, content):
    store.add_document(title=title, category=category, source_type='research_evidence', source=source, content=content, review_status='pending_human_review')

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--data-dir',type=Path,default=Path('data')); args=ap.parse_args(); root=Path('data/issuer_evidence_queue_20261029'); store=Store(args.data_dir)
    for path in sorted(root.rglob('*.pdf')):
        pages, limitation=extract_pdf_text(path.read_bytes()); text='\n\n'.join(pages) or limitation; digest=hashlib.sha256(path.read_bytes()).hexdigest(); add(store,path.stem,'finance_issuer_evidence',f'local-pdf:{digest}',text)
    for path in [Path('data/commodities/COMMODITY_INGESTION_PLAN.md'),Path('data/commodities/commodity_universe_v1.json')]:
        if path.exists(): add(store,path.name,'commodities',f'local-pack:{path.as_posix()}',path.read_text(encoding='utf-8'))
    print(json.dumps({'status':'ingested','database':str(store.path)},indent=2))
if __name__=='__main__': main()
