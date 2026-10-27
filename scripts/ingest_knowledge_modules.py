"""Validate and ingest the versioned persona foundation modules into local SQLite."""

import argparse
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import sys

from assistant_api.config import Settings
from assistant_api.personas import PERSONAS
from assistant_api.storage import Store

ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = ROOT / "knowledge" / "modules"
MANIFEST_PATH = MODULE_DIR / "manifest.json"
MAX_MODULE_BYTES = 200_000
SOURCE_PREFIX = "knowledge-module:"


class ModuleError(ValueError):
    """The knowledge manifest or a module is invalid."""


@dataclass(frozen=True)
class KnowledgeModule:
    persona: str
    category: str
    title: str
    source: str
    content: str
    review_status: str


def load_modules(manifest_path: Path = MANIFEST_PATH) -> list[KnowledgeModule]:
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ModuleError(f"Could not read a valid knowledge module manifest: {exc}") from exc
    if not isinstance(manifest, dict) or set(manifest) != {"version", "purpose", "modules"}:
        raise ModuleError("Manifest must contain only version, purpose, and modules")
    if manifest["version"] != 1 or not isinstance(manifest["purpose"], str) or not manifest["purpose"].strip():
        raise ModuleError("Unsupported manifest version or missing purpose")
    entries = manifest["modules"]
    if not isinstance(entries, list) or not entries:
        raise ModuleError("Manifest modules must be a non-empty list")

    base = manifest_path.parent.resolve()
    modules = []
    seen_personas = set()
    seen_sources = set()
    seen_paths = set()
    for entry in entries:
        required = {"persona", "category", "title", "path", "review_status"}
        if not isinstance(entry, dict) or set(entry) != required:
            raise ModuleError("Each module entry must contain persona, category, title, path, and review_status")
        persona, category, title, relative_path, status = (
            entry["persona"], entry["category"], entry["title"], entry["path"], entry["review_status"],
        )
        if not all(isinstance(value, str) and value.strip() for value in (persona, category, title, relative_path)):
            raise ModuleError("Module persona, category, title, and path must be non-empty strings")
        if persona not in PERSONAS:
            raise ModuleError(f"Unknown persona: {persona}")
        if category not in PERSONAS[persona].knowledge_sources:
            raise ModuleError(f"Category {category} is not enabled for persona {persona}")
        if status != "pending_human_review":
            raise ModuleError(f"Foundation modules must remain pending_human_review: {persona}")
        path = PurePosixPath(relative_path)
        if path.is_absolute() or path.suffix.lower() != ".md" or ".." in path.parts:
            raise ModuleError(f"Module path must be a relative Markdown path: {relative_path}")
        file_path = (base / Path(*path.parts)).resolve()
        if file_path.parent != base or file_path in seen_paths:
            raise ModuleError(f"Module path must be unique and directly inside the module directory: {relative_path}")
        if persona in seen_personas:
            raise ModuleError(f"Duplicate module for persona: {persona}")
        source = SOURCE_PREFIX + persona
        if source in seen_sources:
            raise ModuleError(f"Duplicate module source: {source}")
        try:
            if file_path.stat().st_size > MAX_MODULE_BYTES:
                raise ModuleError(f"Module exceeds {MAX_MODULE_BYTES} bytes: {relative_path}")
            content = file_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ModuleError(f"Could not read module {relative_path}: {exc}") from exc
        if not content:
            raise ModuleError(f"Module is empty: {relative_path}")
        modules.append(KnowledgeModule(persona, category, title, source, content, status))
        seen_personas.add(persona)
        seen_sources.add(source)
        seen_paths.add(file_path)

    missing = set(PERSONAS) - seen_personas
    if missing:
        raise ModuleError(f"Manifest is missing personas: {', '.join(sorted(missing))}")
    return modules


def ingest_modules(store: Store, modules: list[KnowledgeModule]) -> tuple[int, int]:
    """Upsert modules by stable source; changed content becomes a Store revision."""
    created = updated = 0
    for module in modules:
        with store.connect() as db:
            rows = db.execute(
                "SELECT id FROM documents WHERE source=? AND category=? AND source_type='working_note'",
                (module.source, module.category),
            ).fetchall()
        if len(rows) > 1:
            raise ModuleError(f"Multiple local documents use module source {module.source}")
        fields = {
            "title": module.title,
            "category": module.category,
            "source_type": "working_note",
            "source": module.source,
            "content": module.content,
            "review_status": module.review_status,
        }
        if rows:
            before = store.get_document(rows[0]["id"])
            revision = store.update_document(rows[0]["id"], **fields)
            if revision is None:
                raise ModuleError(f"Module disappeared during update: {module.persona}")
            updated += int(revision != before["revision"])
        else:
            _, was_created = store.add_document(**fields)
            created += int(was_created)
    return created, updated


def resolve_data_dir(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-only", action="store_true", help="Validate every module without writing to SQLite")
    parser.add_argument("--data-dir", type=Path, help="Override the local database directory")
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH, help="Override the module manifest")
    args = parser.parse_args()
    try:
        modules = load_modules(args.manifest)
        print(f"Validated {len(modules)} modules for: {', '.join(module.persona for module in modules)}")
        if args.check_only:
            return
        settings = Settings.from_env()
        store = Store(resolve_data_dir(args.data_dir or settings.data_dir))
        created, updated = ingest_modules(store, modules)
        print(
            f"Knowledge modules registered in {store.path}: "
            f"{created} created, {updated} updated, {len(modules) - created - updated} unchanged. "
            "All remain pending_human_review."
        )
    except ModuleError as exc:
        print(f"Knowledge module import failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
