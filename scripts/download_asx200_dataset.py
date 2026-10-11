"""Build a local ASX 200 research snapshot with Yahoo daily history.

This deliberately separates current constituent membership from historical prices.
It is a current-membership snapshot, not a survivorship-free historical index
membership file. Yahoo data is retained for internal local research only.
"""
from __future__ import annotations

import asyncio
import csv
import json
import re
from datetime import date, timedelta
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
    return rows


async def fetch_history(client: httpx.AsyncClient, symbol: str, start: str, end: str) -> dict:
    try:
        response = await client.get(YAHOO_URL.format(symbol=symbol), params={"range": "5y", "interval": "1d"})
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
            day = date.fromtimestamp(timestamp).isoformat()
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
    OUT.mkdir(parents=True, exist_ok=True)
    page_path = OUT / "constituent_source.html"
    import urllib.request
    request = urllib.request.Request(PAGE_URL, headers={"User-Agent": "MrSmith-local-research/0.1"})
    page_path.write_bytes(urllib.request.urlopen(request, timeout=20).read())
    constituents = extract_constituents(page_path)
    write_csv(OUT / "constituents_snapshot.csv", constituents, list(constituents[0]))

    end = date.today()
    start = end - timedelta(days=365 * 5)
    results = asyncio.run(fetch_all([row["symbol"] for row in constituents], start.isoformat(), end.isoformat()))
    price_rows = [item for result in results for item in result["rows"]]
    write_csv(OUT / "daily_prices_5y.csv", price_rows, ["symbol", "observed_on", "close", "adjusted_close", "volume", "currency", "exchange_timezone"])
    failures = [{"symbol": result["symbol"], "status": result["status"], "error": result["error"]} for result in results if result["status"] != "ok"]
    write_csv(OUT / "failures.csv", failures, ["symbol", "status", "error"])
    manifest = {
        "dataset": "ASX 200 current-membership snapshot with five-year Yahoo daily prices",
        "created_at": date.today().isoformat(), "start": start.isoformat(), "end": end.isoformat(),
        "constituent_count": len(constituents), "successful_symbols": len(results) - len(failures),
        "failed_symbols": len(failures), "price_row_count": len(price_rows), "concurrency": CONCURRENCY,
        "survivorship_warning": "This uses the current constituent list; historical index membership was not reconstructed.",
        "price_source": "Yahoo Finance chart endpoint; internal research only; verify terms and licensing before redistribution.",
        "constituent_source": PAGE_URL,
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
