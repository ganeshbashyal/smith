"""Build structured profiles for every completed ASX research entity."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import date
from pathlib import Path
from statistics import mean


def pct_change(values: list[float], periods: int) -> float | None:
    if len(values) <= periods or values[-periods - 1] == 0:
        return None
    return round((values[-1] / values[-periods - 1] - 1) * 100, 2)


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) <= period:
        return None
    changes = [values[index] - values[index - 1] for index in range(1, len(values))][-period:]
    gains = mean(max(change, 0) for change in changes)
    losses = mean(abs(min(change, 0)) for change in changes)
    return round(100 if losses == 0 else 100 - 100 / (1 + gains / losses), 2)


def technical(bars: list[dict]) -> dict:
    closes = [float(row["adjusted_close"] or row["close"]) for row in bars if row.get("adjusted_close") or row.get("close")]
    volumes = [float(row["volume"] or 0) for row in bars if row.get("adjusted_close") or row.get("close")]
    if not closes:
        return {"status": "unavailable"}
    latest = closes[-1]
    moving = {f"ma{period}": round(mean(closes[-period:]), 4) if len(closes) >= period else None for period in (20, 50, 200)}
    recent = bars[-60:]
    up_volume = down_volume = 0.0
    for row in recent:
        close = row.get("close") or row.get("adjusted_close")
        open_price = row.get("open")
        volume = float(row.get("volume") or 0)
        if close is not None and open_price is not None:
            if float(close) > float(open_price): up_volume += volume
            elif float(close) < float(open_price): down_volume += volume
    volume20 = mean(volumes[-20:]) if volumes else 0
    latest_volume = volumes[-1] if volumes else 0
    if up_volume > down_volume * 1.2: flow = "accumulation_proxy"
    elif down_volume > up_volume * 1.2: flow = "distribution_proxy"
    else: flow = "mixed_or_inconclusive"
    return {
        "status": "available", "latest_close": round(latest, 4), "returns_pct": {
            "1m": pct_change(closes, 21), "3m": pct_change(closes, 63), "6m": pct_change(closes, 126), "1y": pct_change(closes, 252)
        }, "moving_averages": moving, "rsi14": rsi(closes),
        "latest_volume": latest_volume, "average_volume20": round(volume20, 2),
        "volume_ratio20": round(latest_volume / volume20, 2) if volume20 else None,
        "flow_proxy_60d": flow, "up_volume60": round(up_volume, 2), "down_volume60": round(down_volume, 2),
        "warning": "Accumulation/distribution is a price-volume proxy, not proof of institutional activity."
    }


def build(batch: Path, constituents: Path, output: Path) -> dict:
    with constituents.open(encoding="utf-8-sig", newline="") as handle:
        constituent_rows = {row["code"].upper(): row for row in csv.DictReader(handle)}
    profiles = []
    for job in sorted(batch.glob("*-ASX-*")):
        market_path = job / "market-data.json"
        status_path = job / "status.json"
        if not market_path.is_file():
            continue
        market = json.loads(market_path.read_text(encoding="utf-8"))
        code = str(market.get("instrument", "")).removeprefix("ASX:").upper()
        row = constituent_rows.get(code, {})
        status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {}
        report = (job / "report.md").read_text(encoding="utf-8") if (job / "report.md").is_file() else ""
        fundamentals_available = "No issuer annual-report sources are configured" not in report
        profiles.append({
            "instrument": f"ASX:{code}", "symbol": f"{code}.AX", "company": row.get("company", ""), "sector": row.get("sector", ""),
            "indices": ["S&P/ASX 200 snapshot"], "cohort": "asx200_seed", "profile_status": "complete" if market.get("statistics", {}).get("status") == "available" else "price_data_missing",
            "research_status": status.get("status", "unknown"), "fundamentals": {"status": "available" if fundamentals_available else "unavailable", "source_manifest": fundamentals_available},
            "technical": technical(market.get("bars", [])), "market_data": {"retrieved_at": market.get("retrieved_at"), "source": market.get("source"), "currency": market.get("currency"), "observations": market.get("statistics", {}).get("observations")},
            "evidence_gaps": [] if fundamentals_available else ["issuer annual reports", "complete corporate actions", "audited financial history"],
            "limitations": ["Current ASX 200 membership snapshot; historical membership not reconstructed.", "Yahoo public chart data is unofficial and may be delayed."]
        })
    output.mkdir(parents=True, exist_ok=False)
    result = {"profile": "asx_entity_profiles", "created_on": date.today().isoformat(), "count": len(profiles), "profiles": profiles}
    (output / "profiles.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    with (output / "profiles.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["instrument", "company", "sector", "indices", "profile_status", "fundamentals_status", "flow_proxy_60d", "rsi14", "return_1y_pct", "volume_ratio20", "evidence_gaps"]
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for item in profiles:
            tech = item["technical"]
            writer.writerow({"instrument": item["instrument"], "company": item["company"], "sector": item["sector"], "indices": ";".join(item["indices"]), "profile_status": item["profile_status"], "fundamentals_status": item["fundamentals"]["status"], "flow_proxy_60d": tech.get("flow_proxy_60d"), "rsi14": tech.get("rsi14"), "return_1y_pct": (tech.get("returns_pct") or {}).get("1y"), "volume_ratio20": tech.get("volume_ratio20"), "evidence_gaps": ";".join(item["evidence_gaps"])})
    return {"profiles": len(profiles), "fundamentals_available": sum(item["fundamentals"]["status"] == "available" for item in profiles), "technical_available": sum(item["technical"]["status"] == "available" for item in profiles)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--constituents", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.batch, args.constituents, args.output), indent=2))


if __name__ == "__main__":
    main()
