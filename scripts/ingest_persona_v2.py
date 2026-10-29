"""Split and ingest Persona Knowledge Capture v2 by persona category."""
from __future__ import annotations
import argparse, re
from pathlib import Path
from assistant_api.storage import Store

SECTIONS = {
    "general reasoning": ("general", "General reasoning and decision support"),
    "science and technology": ("science", "Science and technology knowledge v2"),
    "engineering and automation": ("engineering", "Engineering and automation knowledge v2"),
    "australian building": ("building", "Australian building knowledge v2"),
    "finance and commodities": ("finance", "Finance and commodities knowledge v2"),
    "business": ("business", "Business knowledge v2"),
    "health": ("health", "Health knowledge v2"),
    "news and geopolitics": ("news", "News and geopolitics knowledge v2"),
    "sports": ("sports", "Sports knowledge v2"),
    "automotive and harley": ("automotive", "Automotive and Harley knowledge v2"),
}

def split(text: str) -> dict[str, str]:
    matches = list(re.finditer(r"^## (.+)$", text, re.MULTILINE)); result = {}
    for index, match in enumerate(matches):
        title = match.group(1).strip().lower(); end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        if title in SECTIONS: result[title] = text[match.start():end].strip()
    return result

def main() -> None:
    ap = argparse.ArgumentParser(); ap.add_argument("--source", type=Path, default=Path("knowledge/persona_v2_capture.md")); ap.add_argument("--data-dir", type=Path, default=Path("data")); args = ap.parse_args()
    store = Store(args.data_dir); sections = split(args.source.read_text(encoding="utf-8")); created = updated = 0
    for key, (category, title) in SECTIONS.items():
        content = sections.get(key)
        if not content: raise SystemExit(f"Missing V2 section: {key}")
        source = f"knowledge-module:v2:{category}"
        with store.connect() as db: row = db.execute("SELECT id FROM documents WHERE source=?", (source,)).fetchone()
        fields = dict(title=title, category=category, source_type="working_note", source=source, content=content, review_status="pending_human_review")
        if row:
            before = store.get_document(row["id"]); revision = store.update_document(row["id"], **fields); updated += int(revision != before["revision"])
        else:
            _, was_created = store.add_document(**fields); created += int(was_created)
    print({"sections": len(sections), "created": created, "updated": updated, "database": str(store.path)})

if __name__ == "__main__": main()
