"""Append one daily ASX price/volume snapshot to Mr Smith's local store.

This is deliberately deterministic: it refreshes bars and indicators without
calling Ollama. Model analysis is triggered separately for shortlisted changes.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from assistant_api.finance.repository import FinanceRepository


def fetch(symbol: str) -> dict:
    source = symbol.removeprefix("ASX:") + ".AX"
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(source, safe='')}?range=5d&interval=1d&events=div%2Csplits"
    request = Request(url, headers={"User-Agent": "MrSmith-local-daily-refresh/1.0"})
    with urlopen(request, timeout=20) as response:
        payload = json.loads(response.read())
    result = payload["chart"]["result"][0]
    meta = result["meta"]
    quote_block = result["indicators"]["quote"][0]
    rows = []
    for ts, op, high, low, close, volume in zip(
        result.get("timestamp", []), quote_block.get("open", []), quote_block.get("high", []),
        quote_block.get("low", []), quote_block.get("close", []), quote_block.get("volume", []),
    ):
        if close is None:
            continue
        observed = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
        rows.append({"observed_on": observed, "open": op, "high": high, "low": low,
                     "close": close, "volume": volume})
    return {"symbol": symbol, "source_symbol": source, "currency": meta.get("currency"), "bars": rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instruments", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--pause-seconds", type=float, default=1.5)
    args = parser.parse_args()
    items = json.loads(args.instruments.read_text(encoding="utf-8"))["instruments"]
    repo = FinanceRepository(args.data_dir / "mr_smith.sqlite3")
    day = datetime.now(timezone.utc).date().isoformat()
    output = args.data_dir / "daily_snapshots" / day
    output.mkdir(parents=True, exist_ok=True)
    summary = {"date": day, "source": "Yahoo Finance public chart endpoint (unofficial)", "completed": 0, "failed": 0, "items": []}
    for item in items:
        symbol = item["instrument"]
        try:
            payload = fetch(symbol)
            (output / f"{symbol.replace(':', '_')}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
            for bar in payload["bars"]:
                repo.save_price_bar(symbol, bar["observed_on"], payload.get("currency") or "AUD", str(bar["close"]), None,
                                    bar.get("volume"), datetime.now(timezone.utc).isoformat())
            summary["completed"] += 1
            summary["items"].append({"instrument": symbol, "status": "ok", "bars": len(payload["bars"])})
        except Exception as exc:  # one bad symbol must not stop the universe
            summary["failed"] += 1
            summary["items"].append({"instrument": symbol, "status": "error", "error": type(exc).__name__})
        time.sleep(max(0, args.pause_seconds))
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("date", "completed", "failed")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
