"""Build a dated ASX opportunity-universe profile from local snapshots.

This first-run builder does not invent index membership or market caps. It
combines the local ASX 200 snapshot with optional additional ASX symbols and
records which fields still require an approved market-data source.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date
import json
from pathlib import Path

BENCHMARKS = [
    {"id": "all_ordinaries", "name": "All Ordinaries", "asx_code": "XAO", "source_symbol": "^AORD"},
    {"id": "asx20", "name": "S&P/ASX 20", "asx_code": "XTL", "source_symbol": None},
    {"id": "asx50", "name": "S&P/ASX 50", "asx_code": "XFL", "source_symbol": None},
    {"id": "asx100", "name": "S&P/ASX 100", "asx_code": "XTO", "source_symbol": None},
    {"id": "asx200", "name": "S&P/ASX 200", "asx_code": "XJO", "source_symbol": "^AXJO"},
    {"id": "asx300", "name": "S&P/ASX 300", "asx_code": "XKO", "source_symbol": None},
    {"id": "midcap50", "name": "S&P/ASX MidCap 50", "asx_code": "XMD", "source_symbol": None},
    {"id": "small_ordinaries", "name": "S&P/ASX Small Ordinaries", "asx_code": "XSO", "source_symbol": "^AXSO"},
    {"id": "emerging_companies", "name": "S&P/ASX Emerging Companies", "asx_code": "XEC", "source_symbol": None},
]


def read_symbols(path: Path | None) -> list[dict]:
    if path is None:
        return []
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        lines = handle.readlines()
    header_index = next((index for index, line in enumerate(lines) if "ASX code" in line or "code" in line.lower()), 0)
    for row in csv.DictReader(lines[header_index:]):
            code = (row.get("code") or row.get("symbol") or row.get("ASX code") or "").strip().upper().removesuffix(".AX")
            if code:
                rows.append({"code": code, "company": (row.get("company") or row.get("Company name") or "").strip(), "sector": (row.get("sector") or row.get("GICS industry group") or "").strip()})
    return rows


def build(snapshot: Path, additional: Path | None, output: Path, limit: int = 1000) -> dict:
    with snapshot.open(encoding="utf-8-sig", newline="") as handle:
        seed = list(csv.DictReader(handle))
    if not seed:
        raise ValueError("ASX constituent snapshot is empty")
    snapshot_date = seed[0].get("snapshot_date") or date.today().isoformat()
    by_code = {}
    for row in seed + read_symbols(additional):
        code = (row.get("code") or "").strip().upper().removesuffix(".AX")
        if not code:
            continue
        by_code.setdefault(code, {"code": code, "company": row.get("company", ""), "sector": row.get("sector", "")})
    seed_codes = {(row.get("code") or "").strip().upper().removesuffix(".AX") for row in seed}
    if limit < len(seed_codes):
        raise ValueError(f"--limit must be at least the seed size ({len(seed_codes)})")
    ordered_codes = sorted(by_code)
    selected_codes = set(seed_codes)
    for code in ordered_codes:
        if len(selected_codes) >= limit:
            break
        selected_codes.add(code)
    companies = []
    for row in sorted((item for code, item in by_code.items() if code in selected_codes), key=lambda item: item["code"]):
        companies.append({
            "instrument": f"ASX:{row['code']}", "source_symbol": f"{row['code']}.AX",
            "company": row.get("company", ""), "sector": row.get("sector", ""),
            "cohorts": ["broad_asx"], "market_cap_status": "not_loaded",
            "liquidity_status": "not_loaded", "profile_status": "price_only_pending",
        })
    for company in companies:
        if company["source_symbol"].removesuffix(".AX") in seed_codes:
            company["cohorts"].append("asx200_seed")
        company["cohorts"].append("microcap_candidate_pending_market_cap")
    payload = {
        "profile": "asx_opportunity_first_run",
        "created_on": date.today().isoformat(),
        "snapshot_date": snapshot_date,
        "source": {
            "additional_file": str(additional) if additional else None,
            "source_note": "Official ASX listed-companies directory snapshot; selection is deterministic alphabetical completion after the ASX 200 seed.",
            "selection_policy": "retain all ASX 200 seed names, then add alphabetically until --limit is reached",
        },
        "benchmarks": BENCHMARKS,
        "companies": companies,
        "counts": {"companies": len(companies), "asx200_seed": len(seed_codes), "additional_symbols": max(0, len(companies) - len(seed_codes)), "source_candidates": len(by_code)},
        "limitations": [
            "Current membership snapshots are not historical membership.",
            "Micro-cap status is not asserted until market capitalisation and liquidity data are loaded.",
            "Company profiles are price-only until issuer evidence is attached.",
            "Benchmark source symbols must be verified before network retrieval.",
        ],
    }
    output.mkdir(parents=True, exist_ok=False)
    (output / "universe_profile.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    with (output / "companies.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["instrument", "source_symbol", "company", "sector", "cohorts", "market_cap_status", "liquidity_status", "profile_status"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for company in companies:
            writer.writerow({**company, "cohorts": ";".join(company["cohorts"])})
    (output / "instruments.json").write_text(json.dumps({"instruments": [{"instrument": item["instrument"], "asset_type": "asx_equity"} for item in companies]}, indent=2), encoding="utf-8")
    return payload["counts"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--additional", type=Path, help="Optional CSV with code, company and sector columns")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    print(json.dumps(build(args.snapshot, args.additional, args.output, args.limit), indent=2))


if __name__ == "__main__":
    main()
