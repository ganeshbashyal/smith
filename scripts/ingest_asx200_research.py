"""Import completed ASX 200 source reports into Smith's local finance evidence store.

The script imports deterministic reports and price observations, not local-model
analysis drafts. It creates an integrity-checked SQLite backup before writing
and is safe to rerun: reports are deduplicated by content hash.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

from assistant_api.config import Settings
from assistant_api.finance.evidence import plain_text_passages
from assistant_api.finance.repository import FinanceStore


SYMBOL_PATTERN = re.compile(r"^ASX:([A-Z0-9]{1,8})$")
JOB_DIRECTORY_PATTERN = re.compile(r"^\d{2,3}-ASX-[A-Z0-9._-]+$")
REPORT_PATTERN = re.compile(
    r"^\s*-\s*\*\*Generated:\*\*\s*(\d{4}-\d{2}-\d{2})T",
    re.MULTILINE,
)


class ImportError(RuntimeError):
    """Raised when the batch or destination cannot be safely reconciled."""


def _read_inputs(batch_dir: Path, constituent_file: Path) -> tuple[dict, list[dict], dict[str, dict]]:
    try:
        status = json.loads((batch_dir / "status.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ImportError("Batch status is missing or invalid") from exc
    if status.get("status") != "completed" or status.get("failed") != 0:
        raise ImportError("Only a fully completed batch with zero failures can be imported")

    try:
        with constituent_file.open(encoding="utf-8-sig", newline="") as handle:
            constituents = list(csv.DictReader(handle))
    except OSError as exc:
        raise ImportError(f"Constituent snapshot is unreadable: {constituent_file}") from exc
    constituent_by_code = {
        row.get("code", "").strip().upper(): row
        for row in constituents
        if row.get("code", "").strip()
    }
    if len(constituent_by_code) != len(constituents):
        raise ImportError("Constituent snapshot contains empty or duplicate codes")

    items = status.get("items")
    if not isinstance(items, list) or not items or len(items) != status.get("total"):
        raise ImportError("Batch item list does not match its declared total")
    if len(items) != len(constituent_by_code):
        raise ImportError(
            f"Batch has {len(items)} instruments but snapshot has {len(constituent_by_code)}"
        )

    validated = []
    seen = set()
    for item in items:
        symbol = item.get("instrument", "")
        match = SYMBOL_PATTERN.fullmatch(symbol)
        if not match:
            raise ImportError(f"Unexpected non-ASX instrument in batch: {symbol!r}")
        code = match.group(1)
        if code in seen or code not in constituent_by_code:
            raise ImportError(f"Batch contains duplicate or unknown constituent: {code}")
        seen.add(code)
        if item.get("status") != "completed":
            raise ImportError(f"{symbol} is not completed")
        folder = item.get("job_directory", "")
        if not JOB_DIRECTORY_PATTERN.fullmatch(folder):
            raise ImportError(f"Unsafe batch job directory for {symbol}")
        report_path = batch_dir / folder / "report.md"
        try:
            report_path.resolve().relative_to(batch_dir.resolve())
            report = report_path.read_text(encoding="utf-8")
        except (OSError, ValueError) as exc:
            raise ImportError(f"Source report is missing or outside the batch for {symbol}") from exc
        if not report.strip() or f"ASX:{code}" not in report:
            raise ImportError(f"Source report is empty or does not match {symbol}")
        validated.append({
            "symbol": f"{code}.AX",
            "constituent": constituent_by_code[code],
            "report_path": report_path,
            "report": report,
        })
    if seen != set(constituent_by_code):
        missing = sorted(set(constituent_by_code) - seen)
        raise ImportError("Batch is missing snapshot constituents: " + ", ".join(missing[:10]))
    return status, validated, constituent_by_code


def _backup_database(database_path: Path) -> Path:
    if not database_path.is_file() or database_path.stat().st_size == 0:
        raise ImportError(f"Existing non-empty Smith database required: {database_path}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = database_path.with_name(f"{database_path.stem}.pre-asx-ingest-{stamp}.sqlite3.bak")
    source = sqlite3.connect(database_path)
    backup = sqlite3.connect(backup_path)
    try:
        source.backup(backup)
        integrity = backup.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        backup.close()
        source.close()
        backup_path.unlink(missing_ok=True)
        raise ImportError(f"Could not create SQLite backup: {type(exc).__name__}") from exc
    finally:
        try:
            backup.close()
        except sqlite3.Error:
            pass
        source.close()
    if not integrity or integrity[0] != "ok" or backup_path.stat().st_size == 0:
        backup_path.unlink(missing_ok=True)
        raise ImportError("SQLite backup did not pass integrity_check")
    return backup_path


def ingest(batch_dir: Path, constituent_file: Path, data_dir: Path, apply: bool) -> dict:
    batch_dir = batch_dir.resolve()
    constituent_file = constituent_file.resolve()
    data_dir = data_dir.resolve()
    status, records, _ = _read_inputs(batch_dir, constituent_file)
    database_path = data_dir / "mr_smith.sqlite3"
    if not database_path.is_file() or database_path.stat().st_size == 0:
        raise ImportError(f"Existing non-empty Smith database required: {database_path}")
    preview = {
        "batch_dir": str(batch_dir),
        "database": str(database_path),
        "batch_completed": status["completed"],
        "constituents": len(records),
        "model_analyses_imported": 0,
        "mode": "apply" if apply else "dry-run",
    }
    if not apply:
        return preview

    backup_path = _backup_database(database_path)
    store = FinanceStore(database_path)
    created_securities = 0
    created_evidence = 0
    unchanged_evidence = 0
    for record in records:
        symbol = record["symbol"]
        row = record["constituent"]
        if store.get_security(symbol) is None:
            store.upsert_security(
                symbol,
                row.get("company", "").strip() or symbol,
                row.get("sector", "").strip() or None,
                "Current S&P/ASX 200 constituent snapshot; historical membership not established.",
            )
            created_securities += 1

        report = record["report"]
        generated = REPORT_PATTERN.search(report)
        report_date = date.fromisoformat(generated.group(1)) if generated else None
        digest = hashlib.sha256(report.encode("utf-8")).hexdigest()
        source_snapshot_date = row.get("snapshot_date", "")
        limitations = (
            "Local deterministic research snapshot. Price history is from Yahoo Finance's "
            "unofficial public chart endpoint and may be delayed. No model-generated analysis "
            "was imported. Issuer financial statements, complete corporate actions, and "
            "historical ASX 200 membership are unavailable unless explicitly documented in "
            "the report. Constituent snapshot date: "
            f"{source_snapshot_date or 'unrecorded'}."
        )
        _, created = store.add_evidence(
            symbol=symbol,
            title=f"ASX research snapshot — {row.get('company', '').strip() or symbol}",
            publisher="Mr Smith local research (Yahoo Finance public chart data)",
            source_type="research_snapshot",
            document_type="other",
            source_url="",
            published_on=report_date,
            period=None,
            content_hash=digest,
            passages=plain_text_passages(report),
            page_count=1,
            limitations=limitations,
        )
        created_evidence += int(created)
        unchanged_evidence += int(not created)

    return {
        **preview,
        "mode": "applied",
        "backup": str(backup_path),
        "securities_created": created_securities,
        "evidence_created": created_evidence,
        "evidence_unchanged": unchanged_evidence,
        "note": "The original per-company reports remain in the batch directory; Llama drafts were not indexed.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", type=Path, required=True)
    parser.add_argument("--constituents", type=Path, required=True)
    parser.add_argument(
        "--data-dir", type=Path,
        default=Path(Settings.from_env().data_dir),
        help="Smith's active data directory (defaults to MR_SMITH_DATA_DIR or ./data)",
    )
    parser.add_argument("--apply", action="store_true", help="Back up SQLite and import reports")
    args = parser.parse_args(argv)
    try:
        result = ingest(args.batch_dir, args.constituents, args.data_dir, args.apply)
    except (ImportError, OSError, sqlite3.Error, ValueError) as exc:
        print(f"ASX knowledge import failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    if not args.apply:
        print("Dry run only. Re-run with --apply to back up SQLite and import.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
