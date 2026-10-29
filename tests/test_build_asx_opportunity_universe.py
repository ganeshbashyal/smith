import csv
import json

from scripts.build_asx_opportunity_universe import build


def test_build_profile_keeps_seed_and_additional_symbols(tmp_path):
    snapshot = tmp_path / "snapshot.csv"
    with snapshot.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["code", "company", "sector", "snapshot_date"])
        writer.writeheader(); writer.writerow({"code": "BHP", "company": "BHP Group", "sector": "Materials", "snapshot_date": "2026-10-29"})
    additional = tmp_path / "additional.csv"
    with additional.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["code", "company", "sector"])
        writer.writeheader(); writer.writerow({"code": "ABC", "company": "Example", "sector": "Technology"})
    output = tmp_path / "out"
    counts = build(snapshot, additional, output)
    assert counts == {"companies": 2, "asx200_seed": 1, "additional_symbols": 1}
    profile = json.loads((output / "universe_profile.json").read_text(encoding="utf-8"))
    bhp = next(item for item in profile["companies"] if item["instrument"] == "ASX:BHP")
    assert "asx200_seed" in bhp["cohorts"]
    assert bhp["market_cap_status"] == "not_loaded"
