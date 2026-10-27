import re
from pathlib import Path
from .storage import SQLiteStore


def ingest_directory(directory: Path, store: SQLiteStore) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in directory.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".txt", ".md"}:
            store.upsert_document(str(path), path.read_text(encoding="utf-8", errors="ignore"))
            count += 1
    return count


class LocalRetriever:
    def __init__(self, store: SQLiteStore): self.store = store

    def search(self, query: str, limit: int = 3) -> list[dict]:
        terms = set(re.findall(r"\w+", query.lower()))
        scored = []
        for doc in self.store.documents():
            words = set(re.findall(r"\w+", doc["content"].lower()))
            score = len(terms & words)
            if score: scored.append((score, doc))
        return [doc for _, doc in sorted(scored, key=lambda x: x[0], reverse=True)[:limit]]
