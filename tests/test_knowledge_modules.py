import json
from pathlib import Path

import pytest

from assistant_api.personas import PERSONAS
from assistant_api.storage import Store
from scripts.ingest_knowledge_modules import (
    MANIFEST_PATH,
    ModuleError,
    ingest_modules,
    load_modules,
    resolve_data_dir,
    ROOT,
)


def test_manifest_covers_all_personas_with_routable_foundation_modules():
    modules = load_modules()

    assert {module.persona for module in modules} == set(PERSONAS)
    assert len(modules) == len(PERSONAS)
    for module in modules:
        assert module.category in PERSONAS[module.persona].knowledge_sources
        assert module.review_status == "pending_human_review"
        assert len(module.content) >= 500
        assert "Verification sources" in module.content


def test_module_ingestion_is_idempotent_and_updates_with_revision(tmp_path):
    store = Store(tmp_path)
    modules = load_modules()

    assert ingest_modules(store, modules) == (len(PERSONAS), 0)
    assert ingest_modules(store, modules) == (0, 0)
    for module in modules:
        evidence = store.search(module.title, PERSONAS[module.persona].knowledge_sources)
        assert any(item["source"] == module.source for item in evidence)

    original = modules[0]
    changed = type(original)(
        original.persona, original.category, original.title, original.source,
        original.content + "\n\nHuman review note: confirm local preferences source.",
        original.review_status,
    )
    assert ingest_modules(store, [changed]) == (0, 1)
    rows = store.list_documents(query="Human review note", category=original.category)
    assert len(rows) == 1
    assert rows[0]["revision"] == 2
    assert rows[0]["review_status"] == "pending_human_review"
    assert len(store.list_document_revisions(rows[0]["id"])) == 1


def test_manifest_rejects_missing_persona_and_unsafe_paths(tmp_path):
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    for module in manifest["modules"]:
        (tmp_path / module["path"]).write_text("# A sufficiently long module\n" * 30, encoding="utf-8")

    missing_persona = dict(manifest)
    missing_persona["modules"] = manifest["modules"][:-1]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(missing_persona), encoding="utf-8")
    with pytest.raises(ModuleError, match="missing personas"):
        load_modules(path)

    unsafe = json.loads(json.dumps(manifest))
    unsafe["modules"][0]["path"] = "../outside.md"
    path.write_text(json.dumps(unsafe), encoding="utf-8")
    with pytest.raises(ModuleError, match="relative Markdown path"):
        load_modules(path)


def test_all_module_files_are_validated_before_any_database_write(tmp_path):
    store = Store(tmp_path / "db")
    invalid = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    invalid["modules"][0]["review_status"] = "reviewed"
    manifest = tmp_path / "manifest.json"
    module_dir = tmp_path / "modules"
    module_dir.mkdir()
    for entry in invalid["modules"]:
        (module_dir / entry["path"]).write_text("# Module\n" + ("Foundation content. " * 40), encoding="utf-8")
    manifest.write_text(json.dumps(invalid), encoding="utf-8")

    with pytest.raises(ModuleError, match="must remain pending_human_review"):
        modules = load_modules(manifest)
        ingest_modules(store, modules)

    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 0


def test_relative_database_path_is_anchored_to_project_not_shell_cwd():
    assert resolve_data_dir(Path("data")) == ROOT / "data"
    absolute = Path("C:/MrSmith/data")
    assert resolve_data_dir(absolute) == absolute
