"""Rank ASX entity profiles into a transparent first deep-dive shortlist."""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path


def score(item: dict) -> tuple[float, list[str]]:
    tech = item.get("technical") or {}
    returns = tech.get("returns_pct") or {}
    ma = tech.get("moving_averages") or {}
    latest = tech.get("latest_close")
    points = 0.0
    reasons = []
    one_year = returns.get("1y")
    three_month = returns.get("3m")
    if one_year is not None:
        points += max(-2, min(4, one_year / 20)); reasons.append(f"1Y {one_year:+.1f}%")
    if three_month is not None and three_month > 0:
        points += 1; reasons.append(f"3M {three_month:+.1f}%")
    if latest and ma.get("ma50") and latest > ma["ma50"]:
        points += 1; reasons.append("above MA50")
    if latest and ma.get("ma200") and latest > ma["ma200"]:
        points += 1; reasons.append("above MA200")
    if tech.get("flow_proxy_60d") == "accumulation_proxy":
        points += 2; reasons.append("accumulation proxy")
    elif tech.get("flow_proxy_60d") == "distribution_proxy":
        points -= 2; reasons.append("distribution proxy")
    if (tech.get("volume_ratio20") or 0) >= 1.5:
        points += 0.5; reasons.append("volume expansion")
    rsi = tech.get("rsi14")
    if rsi is not None and 45 <= rsi <= 70:
        points += 0.5; reasons.append("constructive RSI")
    gaps = len(item.get("evidence_gaps") or [])
    points -= min(3, gaps * 0.5)
    if gaps: reasons.append(f"{gaps} evidence gaps")
    return round(points, 2), reasons


def build(profiles_path: Path, output: Path, limit: int = 30) -> dict:
    payload = json.loads(profiles_path.read_text(encoding="utf-8"))
    ranked = []
    for item in payload.get("profiles", []):
        value, reasons = score(item); ranked.append({"instrument": item["instrument"], "company": item.get("company", ""), "sector": item.get("sector", ""), "score": value, "reasons": reasons, "flow": (item.get("technical") or {}).get("flow_proxy_60d"), "profile_status": item.get("profile_status"), "evidence_gaps": item.get("evidence_gaps", [])})
    ranked.sort(key=lambda item: (-item["score"], item["instrument"]))
    result = {
        "snapshot": "asx_first_deep_dive_shortlist", "created_on": date.today().isoformat(), "source_profiles": str(profiles_path),
        "method": "Transparent technical and data-completeness triage; not a forecast or buy/sell recommendation.",
        "candidate_count": len(ranked), "deep_dive_queue": ranked[:limit], "full_ranked": ranked,
        "next_action": "Attach issuer evidence and perform a fundamental deep dive on the queued candidates.",
    }
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return {"profiles": len(ranked), "deep_dive_queue": min(limit, len(ranked)), "top": ranked[:5]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--profiles", type=Path, required=True); parser.add_argument("--output", type=Path, required=True); parser.add_argument("--limit", type=int, default=30)
    args = parser.parse_args(); print(json.dumps(build(args.profiles, args.output, args.limit), indent=2))


if __name__ == "__main__":
    main()
