"""Create a dated baseline benchmark from an ASX research batch."""
from __future__ import annotations

import argparse
import csv
from datetime import date
import json
from pathlib import Path
from statistics import median


def number(value: str) -> float | None:
    try:
        return float(value) if value not in ("", "Unavailable", "None", None) else None
    except (TypeError, ValueError):
        return None


def index_return(path: Path) -> float | None:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if number(row.get("adjusted_close")) is not None]
    if len(rows) < 2:
        return None
    first, last = float(rows[0]["adjusted_close"]), float(rows[-1]["adjusted_close"])
    return (last / first - 1) * 100 if first else None


def build(comparison: Path, index: Path, output: Path) -> dict:
    with comparison.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    metrics = {}
    for field in ("return_pct",):
        values = [number(row.get(field)) for row in rows]
        values = [value for value in values if value is not None]
        metrics[field] = {"available": len(values), "median": round(median(values), 2) if values else None}
    benchmark = index_return(index)
    returns = [(row["instrument"], number(row.get("return_pct"))) for row in rows]
    available = [(symbol, value) for symbol, value in returns if value is not None]
    available.sort(key=lambda item: item[1], reverse=True)
    result = {
        "profile": "asx_opportunity_first_run_benchmark",
        "created_on": date.today().isoformat(),
        "universe": {"batch_rows": len(rows), "price_available": len(available), "price_missing": len(rows) - len(available)},
        "benchmark": {"name": "S&P/ASX 200 price-index proxy", "source_file": str(index), "return_pct": round(benchmark, 2) if benchmark is not None else None},
        "cohort": {"name": "current ASX 200 snapshot, equal-weight company returns", "median_return_pct": metrics["return_pct"]["median"], "beat_benchmark_count": sum(value > benchmark for _, value in available) if benchmark is not None else None, "beat_benchmark_pct": round(sum(value > benchmark for _, value in available) / len(available) * 100, 2) if benchmark is not None and available else None},
        "top_10_by_return": [{"instrument": symbol, "return_pct": round(value, 2)} for symbol, value in available[:10]],
        "bottom_10_by_return": [{"instrument": symbol, "return_pct": round(value, 2)} for symbol, value in available[-10:]],
        "limitations": ["Current membership applied to the snapshot; survivorship bias remains.", "This is price performance, not total return unless the input is adjusted close.", "No micro-cap cohort was included in this baseline.", "This benchmark is descriptive and not an investment recommendation."],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--comparison", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.comparison, args.index, args.output), indent=2))


if __name__ == "__main__":
    main()
