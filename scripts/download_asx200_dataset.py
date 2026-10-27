"""Build a local ASX 200 research snapshot with Yahoo daily history.

This deliberately separates current constituent membership from historical prices.
It is a current-membership snapshot, not a survivorship-free historical index
membership file. Yahoo data is retained for internal local research only.
"""
from __future__ import annotations

import asyncio
import argparse
import csv
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "asx200_ingest"
PAGE_URL = "https://en.wikipedia.org/api/rest_v1/page/html/S%26P_ASX_200"
YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
CONCURRENCY = 4


def extract_constituents(page_path: Path) -> list[dict]:
    tables = pd.read_html(page_path)
    table = next(table for table in tables if len(table) == 200 and "Code" in table.columns)
    rows = []
    for _, row in table.iterrows():
        code = str(row["Code"]).strip().upper()
        code = re.sub(r"[^A-Z0-9]", "", code)
        rows.append({
            "symbol": f"{code}.AX",
            "code": code,
            "company": str(row.get("Company", "")).strip(),
            "sector": str(row.get("Sector", "")).strip(),
            "snapshot_date": date.today().isoformat(),
            "membership_basis": "current constituent snapshot; historical membership not established",
            "source": PAGE_URL,
        })
    codes = [row["code"] for row in rows]
    if len(codes) != 200 or any(not code for code in codes) or len(set(codes)) != 200:
        raise ValueError("The constituent source did not contain 200 unique non-empty ASX codes")
    return rows


async def fetch_history(client: httpx.AsyncClient, symbol: str, start: str, end: str) -> dict:
    try:
        response = await client.get(YAHOO_URL.format(symbol=symbol), params={"range": "10y", "interval": "1d"})
        response.raise_for_status()
        result = response.json()["chart"]["result"][0]
        meta = result.get("meta", {})
        timestamps = result.get("timestamp", [])
        quote = result.get("indicators", {}).get("quote", [{}])[0]
        adjusted = result.get("indicators", {}).get("adjclose", [{}])[0].get("adjclose", [])
        rows = []
        for i, timestamp in enumerate(timestamps):
            close = (quote.get("close") or [None] * len(timestamps))[i]
            if close is None:
                continue
            day = datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
            if not (start <= day <= end):
                continue
            rows.append({
                "symbol": symbol, "observed_on": day, "close": close,
                "adjusted_close": (adjusted[i] if i < len(adjusted) else None),
                "volume": (quote.get("volume") or [None] * len(timestamps))[i],
                "currency": meta.get("currency"), "exchange_timezone": meta.get("exchangeTimezoneName"),
            })
        return {"symbol": symbol, "status": "ok" if rows else "no_results", "rows": rows, "error": None if rows else "no valid rows"}
    except Exception as exc:
        return {"symbol": symbol, "status": "error", "rows": [], "error": type(exc).__name__ + ": " + str(exc)[:200]}


async def fetch_all(symbols: list[str], start: str, end: str) -> list[dict]:
    semaphore = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "MrSmith-local-research/0.1"}) as client:
        async def one(symbol: str) -> dict:
            async with semaphore:
                result = await fetch_history(client, symbol, start, end)
                await asyncio.sleep(0.15)
                return result
        return await asyncio.gather(*(one(symbol) for symbol in symbols))


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepare-only", action="store_true",
        help="Save a current constituent snapshot and batch JSON without downloading prices",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=OUT,
        help="New or empty destination directory (existing files are never overwritten)",
    )
    args = parser.parse_args()
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()):
        parser.error(f"Output directory is not empty; choose a new directory: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    page_path = output_dir / "constituent_source.html"
    import urllib.request
    request = urllib.request.Request(PAGE_URL, headers={"User-Agent": "MrSmith-local-research/0.1"})
    page_path.write_bytes(urllib.request.urlopen(request, timeout=20).read())
    constituents = extract_constituents(page_path)
    write_csv(output_dir / "constituents_snapshot.csv", constituents, list(constituents[0]))
    instruments = {
        "instruments": [
            {
                "instrument": f"ASX:{row['code']}",
                "asset_type": "auto",
                "list_name": f"S&P/ASX 200 snapshot ({row['snapshot_date']})",
            }
            for row in constituents
        ],
        "source": PAGE_URL,
        "snapshot_date": date.today().isoformat(),
        "membership_basis": "Current constituent snapshot only; not historical index membership.",
    }
    instruments_path = output_dir / "instruments.json"
    instruments_path.write_text(json.dumps(instruments, indent=2), encoding="utf-8")
    if args.prepare_only:
        print(json.dumps({
            "constituent_count": len(constituents),
            "snapshot_date": date.today().isoformat(),
            "instruments_file": str(instruments_path),
            "constituent_source": PAGE_URL,
        }, indent=2))
        return

    end = date.today()
    start = end - timedelta(days=365 * 10)
    results = asyncio.run(fetch_all([row["symbol"] for row in constituents], start.isoformat(), end.isoformat()))
    price_rows = [item for result in results for item in result["rows"]]
    write_csv(output_dir / "daily_prices_10y.csv", price_rows, ["symbol", "observed_on", "close", "adjusted_close", "volume", "currency", "exchange_timezone"])
    failures = [{"symbol": result["symbol"], "status": result["status"], "error": result["error"]} for result in results if result["status"] != "ok"]
    write_csv(output_dir / "failures.csv", failures, ["symbol", "status", "error"])
    manifest = {
        "dataset": "ASX 200 current-membership snapshot with ten-year Yahoo daily prices",
        "created_at": date.today().isoformat(), "start": start.isoformat(), "end": end.isoformat(),
        "constituent_count": len(constituents), "successful_symbols": len(results) - len(failures),
        "failed_symbols": len(failures), "price_row_count": len(price_rows), "concurrency": CONCURRENCY,
        "survivorship_warning": "This uses the current constituent list; historical index membership was not reconstructed.",
        "price_source": "Yahoo Finance chart endpoint; internal research only; verify terms and licensing before redistribution.",
        "constituent_source": PAGE_URL,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
