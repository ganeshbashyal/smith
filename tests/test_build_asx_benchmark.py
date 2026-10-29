import csv
import json

from scripts.build_asx_benchmark import build


def test_build_benchmark_compares_cohort_to_index(tmp_path):
    comparison = tmp_path / "comparison.csv"
    with comparison.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["instrument", "return_pct"])
        writer.writeheader(); writer.writerows([{"instrument": "ASX:A", "return_pct": "20"}, {"instrument": "ASX:B", "return_pct": "-5"}])
    index = tmp_path / "index.csv"
    with index.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["observed_on", "adjusted_close"])
        writer.writeheader(); writer.writerows([{"observed_on": "2025-01-01", "adjusted_close": "100"}, {"observed_on": "2026-01-01", "adjusted_close": "110"}])
    result = build(comparison, index, tmp_path / "benchmark.json")
    assert result["benchmark"]["return_pct"] == 10.0
    assert result["cohort"]["beat_benchmark_count"] == 1
    assert json.loads((tmp_path / "benchmark.json").read_text())['universe']['price_available'] == 2
