"""Register the generated ASX snapshot in Mr Smith's active document store.

The large daily price CSV stays as a local analytical file; only the manifest,
coverage note and constituent snapshot are indexed for conversational retrieval.
"""
from pathlib import Path
import json

from assistant_api.config import Settings
from assistant_api.storage import Store


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "asx200_ingest"


def add(store: Store, title: str, source: Path, content: str) -> None:
    document_id, created = store.add_document(title, "finance", "source_document", str(source), content)
    print(f"{title}: id={document_id}, created={created}")


def main() -> None:
    manifest = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))
    store = Store(Settings.from_env().data_dir)
    add(store, "ASX 200 dataset manifest", DATA / "manifest.json", json.dumps(manifest, indent=2))
    add(store, "ASX 200 ingestion limitations", DATA / "README.md", (DATA / "README.md").read_text(encoding="utf-8"))
    add(store, "ASX 200 current constituents snapshot", DATA / "constituents_snapshot.csv", (DATA / "constituents_snapshot.csv").read_text(encoding="utf-8"))
    print("Daily prices remain at data/asx200_ingest/daily_prices_5y.csv for analytical use; they are not copied into the conversational index.")


if __name__ == "__main__":
    main()
