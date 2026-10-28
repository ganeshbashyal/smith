import csv
import json

from assistant_api.finance.repository import FinanceStore
from scripts.ingest_asx200_research import ingest


def _fixture(tmp_path):
    batch_dir = tmp_path / "batch"
    item_dir = batch_dir / "01-ASX-CSL"
    item_dir.mkdir(parents=True)
    report = (
        "# CSL — instrument research\n"
        "- **Instrument:** `ASX:CSL`\n"
        "- **Generated:** 2026-10-27T12:00:00+00:00\n"
        "## Market data and event coverage\n"
        "The report records a dated closing price and its source.\n"
    )
    (item_dir / "report.md").write_text(report, encoding="utf-8")
    (batch_dir / "status.json").write_text(json.dumps({
        "status": "completed", "completed": 1, "failed": 0, "total": 1,
        "items": [{
            "instrument": "ASX:CSL", "status": "completed",
            "job_directory": "01-ASX-CSL",
        }],
    }), encoding="utf-8")

    constituent_file = tmp_path / "constituents.csv"
    with constituent_file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=[
            "code", "company", "sector", "snapshot_date",
        ])
        writer.writeheader()
        writer.writerow({
            "code": "CSL", "company": "CSL Limited",
            "sector": "Healthcare", "snapshot_date": "2026-10-27",
        })

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    existing = FinanceStore(data_dir / "mr_smith.sqlite3")
    existing.upsert_security("CSL.AX", "User's existing name", "Existing sector", "Keep this note")
    return batch_dir, constituent_file, data_dir, report


def test_asx_research_import_dry_run_does_not_change_database(tmp_path):
    batch_dir, constituents, data_dir, _ = _fixture(tmp_path)
    database = data_dir / "mr_smith.sqlite3"
    before = database.read_bytes()

    result = ingest(batch_dir, constituents, data_dir, apply=False)

    assert result["mode"] == "dry-run"
    assert result["constituents"] == 1
    assert database.read_bytes() == before
    assert not list(data_dir.glob("*.pre-asx-ingest-*.bak"))


def test_asx_research_import_is_idempotent_and_preserves_existing_security(tmp_path):
    batch_dir, constituents, data_dir, report = _fixture(tmp_path)
    first = ingest(batch_dir, constituents, data_dir, apply=True)
    second = ingest(batch_dir, constituents, data_dir, apply=True)

    store = FinanceStore(data_dir / "mr_smith.sqlite3")
    security = store.get_security("CSL.AX")
    evidence = store.list_evidence("CSL.AX")
    passages = store.search_evidence("CSL.AX", "closing price source")
    assert first["securities_created"] == 0
    assert first["evidence_created"] == 1
    assert second["evidence_created"] == 0
    assert second["evidence_unchanged"] == 1
    assert security["name"] == "User's existing name"
    assert security["sector"] == "Existing sector"
    assert len(evidence) == 1
    assert evidence[0]["source_type"] == "research_snapshot"
    assert evidence[0]["published_on"] == "2026-10-27"
    assert "model-generated analysis was imported" in evidence[0]["limitations"]
    assert passages and passages[0]["content"] in report
    assert len(list(data_dir.glob("*.pre-asx-ingest-*.bak"))) == 2


def test_asx_research_import_rejects_incomplete_batch_before_database_writes(tmp_path):
    batch_dir, constituents, data_dir, _ = _fixture(tmp_path)
    status_path = batch_dir / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["failed"] = 1
    status["status"] = "completed_with_errors"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    database = data_dir / "mr_smith.sqlite3"
    before = database.read_bytes()

    try:
        ingest(batch_dir, constituents, data_dir, apply=True)
    except RuntimeError as exc:
        assert "fully completed batch" in str(exc)
    else:
        raise AssertionError("an incomplete batch should not be imported")
    assert database.read_bytes() == before
    assert not list(data_dir.glob("*.pre-asx-ingest-*.bak"))
