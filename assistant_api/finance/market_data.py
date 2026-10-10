"""Bounded ASX/Yahoo daily price history provider.

Unofficial, potentially delayed, best-effort public source. Distinguishes raw
close from adjusted close and never fabricates a bar for a missing day.
Refreshes are capped at 50 symbols with bounded concurrency and a single
retry on rate-limiting / transient failures.
"""
import asyncio
from datetime import datetime, timezone
import logging

import httpx

logger = logging.getLogger(__name__)

MAX_SYMBOLS_PER_REFRESH = 50
MAX_RANGE = "1y"
CONCURRENCY = 5


async def fetch_symbol_history(client: httpx.AsyncClient, symbol: str) -> dict:
    """Returns {"symbol", "status", "bars": [...], "currency", "error"}."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": MAX_RANGE, "interval": "1d"}
    for attempt in range(2):
        try:
            response = await client.get(url, params=params)
            if response.status_code == 429 and attempt == 0:
                await asyncio.sleep(0.5)
                continue
            response.raise_for_status()
            payload = response.json()["chart"]["result"][0]
            break
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            if attempt == 1:
                logger.warning("Price history failed for %s: %s", symbol, type(exc).__name__)
                return {"symbol": symbol, "status": "error",
                       "error": "Source unavailable or returned invalid data", "bars": [], "currency": None}
    try:
        meta = payload["meta"]
        currency = meta.get("currency")
        timestamps = payload.get("timestamp") or []
        quote = payload["indicators"]["quote"][0]
        closes = quote.get("close") or []
        adjclose_block = payload.get("indicators", {}).get("adjclose")
        adjcloses = adjclose_block[0].get("adjclose") if adjclose_block else [None] * len(timestamps)
        volumes = quote.get("volume") or [None] * len(timestamps)
        bars = []
        retrieved_at = datetime.now(timezone.utc).isoformat()
        for ts, close, adj, volume in zip(timestamps, closes, adjcloses, volumes):
            if close is None:
                continue
            observed_on = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
            bars.append({
                "observed_on": observed_on, "currency": currency,
                "close": str(close), "adjusted_close": str(adj) if adj is not None else None,
                "volume": int(volume) if volume is not None else None, "retrieved_at": retrieved_at,
            })
        return {"symbol": symbol, "status": "ok" if bars else "no_results", "bars": bars,
               "currency": currency, "error": None if bars else "No valid observations returned"}
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("Price history parse failed for %s: %s", symbol, type(exc).__name__)
        return {"symbol": symbol, "status": "error",
               "error": "Source unavailable or returned invalid data", "bars": [], "currency": None}


async def refresh_history(client: httpx.AsyncClient, symbols: list[str]) -> list[dict]:
    if len(symbols) > MAX_SYMBOLS_PER_REFRESH:
        raise ValueError(f"A refresh is capped at {MAX_SYMBOLS_PER_REFRESH} symbols")
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def bounded(symbol: str) -> dict:
        async with semaphore:
            return await fetch_symbol_history(client, symbol)

    return list(await asyncio.gather(*(bounded(symbol) for symbol in symbols)))
