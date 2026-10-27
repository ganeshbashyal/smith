"""Bounded ASX/Yahoo daily price history provider.

Unofficial, potentially delayed, best-effort public source. Distinguishes raw
close from adjusted close and never fabricates a bar for a missing day.
Refreshes are capped at 50 symbols with bounded concurrency and a single
retry on rate-limiting / transient failures.
"""
import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

logger = logging.getLogger(__name__)

MAX_SYMBOLS_PER_REFRESH = 50
MAX_RANGE = "1y"
CONCURRENCY = 5
XAUUSD_SOURCE = "Yahoo Finance (XAUUSD=X proxy)"
XAUUSD_SOURCE_SYMBOL = "XAUUSD=X"
XAUUSD_TIMEFRAMES = {
    "1m": ("1d", "1m", timedelta(minutes=5)),
    "5m": ("5d", "5m", timedelta(minutes=20)),
    "15m": ("1mo", "15m", timedelta(minutes=60)),
    "1h": ("3mo", "1h", timedelta(hours=4)),
    "1d": ("1y", "1d", timedelta(days=3)),
}
YAHOO_MACRO_SYMBOLS = {
    "XAUUSD=X": ("xauusd_spot_proxy", "Gold / US dollar (proxy)"),
    "DX-Y.NYB": ("index", "US Dollar Index (proxy)"),
    "EURUSD=X": ("currency", "EUR/USD"),
    "USDJPY=X": ("currency", "USD/JPY"),
    "^VIX": ("index", "CBOE Volatility Index"),
    "^AXJO": ("index", "S&P/ASX 200"),
    "^GSPC": ("index", "S&P 500"),
    "^IXIC": ("index", "NASDAQ Composite"),
}

ASX_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{1,8}\.AX$")


def _exchange_date(epoch_seconds: int, exchange_timezone_name: str | None) -> tuple[str, str, str]:
    """Returns (observed_on_exchange_local_date, observed_at_utc_iso, timezone_used).

    A daily bar's calendar date must reflect the exchange's own local date
    (e.g. Sydney), not a naive UTC date, which can shift the date backward or
    forward around the exchange's midday depending on the time of year.
    """
    instant_utc = datetime.fromtimestamp(epoch_seconds, timezone.utc)
    tz_name = exchange_timezone_name or "UTC"
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        tz = timezone.utc
        tz_name = "UTC"
    local_date = instant_utc.astimezone(tz).date().isoformat()
    return local_date, instant_utc.isoformat(), tz_name


async def fetch_symbol_history(client: httpx.AsyncClient, symbol: str) -> dict:
    """Returns {"symbol", "status", "bars": [...], "currency", "error"}."""
    if not ASX_SYMBOL_PATTERN.match(symbol):
        return {"symbol": symbol, "status": "invalid_symbol",
               "error": "Not a valid ASX symbol (expected e.g. 'BHP.AX'); refusing to query external feeds",
               "bars": [], "currency": None}
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
        exchange_tz_name = meta.get("exchangeTimezoneName")
        timestamps = payload.get("timestamp") or []
        n = len(timestamps)
        quote = payload["indicators"]["quote"][0]
        closes = _aligned_list(quote.get("close"), n)
        adjclose_block = payload.get("indicators", {}).get("adjclose")
        adjcloses = _aligned_list(
            adjclose_block[0].get("adjclose") if adjclose_block else None, n
        )
        volumes = _aligned_list(quote.get("volume"), n)
        bars = []
        retrieved_at = datetime.now(timezone.utc).isoformat()
        for ts, close, adj, volume in zip(timestamps, closes, adjcloses, volumes):
            if close is None or not _finite_nonnegative(close):
                continue
            if volume is not None and not _finite_nonnegative(volume):
                volume = None
            if adj is not None and not _finite_nonnegative(adj):
                adj = None
            observed_on, observed_at_utc, tz_used = _exchange_date(ts, exchange_tz_name)
            bars.append({
                "observed_on": observed_on, "observed_at_utc": observed_at_utc, "exchange_timezone": tz_used,
                "currency": currency, "close": str(close), "adjusted_close": str(adj) if adj is not None else None,
                "volume": int(volume) if volume is not None else None, "retrieved_at": retrieved_at,
            })
        return {"symbol": symbol, "status": "ok" if bars else "no_results", "bars": bars,
               "currency": currency, "error": None if bars else "No valid observations returned"}
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("Price history parse failed for %s: %s", symbol, type(exc).__name__)
        return {"symbol": symbol, "status": "error",
               "error": "Source unavailable or returned invalid data", "bars": [], "currency": None}


def _aligned_list(values, length: int) -> list:
    """Defensive alignment: a missing, null, or mismatched-length array from the
    upstream feed becomes an all-None list of the expected length rather than
    raising TypeError (non-iterable) or silently truncating via zip()."""
    if not isinstance(values, list):
        return [None] * length
    if len(values) < length:
        return values + [None] * (length - len(values))
    return values[:length]


def _finite_nonnegative(value) -> bool:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    if number != number or number in (float("inf"), float("-inf")):  # NaN check
        return False
    return number >= 0


async def refresh_history(client: httpx.AsyncClient, symbols: list[str]) -> list[dict]:
    if len(symbols) > MAX_SYMBOLS_PER_REFRESH:
        raise ValueError(f"A refresh is capped at {MAX_SYMBOLS_PER_REFRESH} symbols")
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def bounded(symbol: str) -> dict:
        async with semaphore:
            return await fetch_symbol_history(client, symbol)

    return list(await asyncio.gather(*(bounded(symbol) for symbol in symbols)))


async def fetch_yahoo_market_history(
    client: httpx.AsyncClient, symbol: str, timeframe: str = "1h",
) -> dict:
    """Fetch a bounded allowlisted Yahoo series with explicit source metadata."""
    is_asx_equity = bool(ASX_SYMBOL_PATTERN.fullmatch(symbol))
    if symbol not in YAHOO_MACRO_SYMBOLS and not is_asx_equity:
        return {
            "instrument": symbol, "status": "invalid_symbol",
            "timeframe": timeframe, "source": "Yahoo Finance",
            "currency": None, "bars": [], "error": "Symbol is not in the finance adapter allowlist",
        }
    if timeframe not in XAUUSD_TIMEFRAMES:
        return {
            "instrument": symbol, "status": "invalid_timeframe",
            "timeframe": timeframe, "source": "Yahoo Finance",
            "currency": "USD", "bars": [], "error": "Unsupported timeframe",
        }
    instrument_type, display_name = (
        ("asx_equity", symbol) if is_asx_equity else YAHOO_MACRO_SYMBOLS[symbol]
    )
    source = XAUUSD_SOURCE if symbol == XAUUSD_SOURCE_SYMBOL else "Yahoo Finance (unofficial, delayed where applicable)"
    history_range, interval, stale_after = XAUUSD_TIMEFRAMES[timeframe]
    url = (
        "https://query1.finance.yahoo.com/v8/finance/chart/"
        f"{quote(symbol, safe='')}"
    )
    try:
        response = await client.get(
            url, params={"range": history_range, "interval": interval}
        )
        response.raise_for_status()
        chart = response.json().get("chart", {})
        if chart.get("error"):
            raise ValueError("Yahoo chart returned an error")
        payload = chart["result"][0]
        meta = payload["meta"]
        timestamps = payload.get("timestamp") or []
        quote_block = payload["indicators"]["quote"][0]
        opens = _aligned_list(quote_block.get("open"), len(timestamps))
        highs = _aligned_list(quote_block.get("high"), len(timestamps))
        lows = _aligned_list(quote_block.get("low"), len(timestamps))
        closes = _aligned_list(quote_block.get("close"), len(timestamps))
        volumes = _aligned_list(quote_block.get("volume"), len(timestamps))
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        logger.warning("Yahoo market history failed for %s: %s", symbol, type(exc).__name__)
        return {
            "instrument": symbol, "status": "error",
            "timeframe": timeframe, "source": source, "currency": None,
            "bars": [], "error": "Source unavailable or returned invalid data",
        }

    retrieved_at = datetime.now(timezone.utc)
    bars = []
    for timestamp, open_, high, low, close, volume in zip(
        timestamps, opens, highs, lows, closes, volumes
    ):
        if not all(_finite_nonnegative(value) and float(value) > 0
                   for value in (open_, high, low, close)):
            continue
        observed_at = datetime.fromtimestamp(int(timestamp), timezone.utc)
        bars.append({
            "instrument": symbol,
            "instrument_type": instrument_type,
            "observed_at": observed_at.isoformat(),
            "source": source,
            "currency": meta.get("currency") or (
                "N/A" if symbol == "^VIX" else "AUD" if symbol == "^AXJO" else "USD"
            ),
            "timeframe": timeframe,
            "open": str(open_), "high": str(high), "low": str(low), "close": str(close),
            "volume": int(volume) if _finite_nonnegative(volume) else None,
            "retrieved_at": retrieved_at.isoformat(),
            "freshness": "fresh" if retrieved_at - observed_at <= stale_after else "stale",
        })
    if not bars:
        return {
            "instrument": symbol, "status": "no_results",
            "timeframe": timeframe, "source": source,
            "currency": meta.get("currency") or "USD", "bars": [],
            "error": "No valid observations returned",
        }
    status = "ok" if bars[-1]["freshness"] == "fresh" else "stale"
    return {
        "instrument": symbol,
        "name": display_name,
        "instrument_type": instrument_type,
        "status": status,
        "observed_at": bars[-1]["observed_at"],
        "source": source,
        "currency": bars[-1]["currency"],
        "timeframe": timeframe,
        "freshness": bars[-1]["freshness"],
        "bars": bars,
        "error": None if status == "ok" else "Latest observation is stale",
        "warning": (
            "Yahoo Finance XAUUSD=X is an unofficial proxy, not an authoritative executable spot quote."
            if symbol == XAUUSD_SOURCE_SYMBOL else
            "Yahoo Finance data is unofficial and may be delayed; verify market values before use."
        ),
    }


async def refresh_market_history(
    client: httpx.AsyncClient, symbols: list[str], timeframe: str = "1d",
) -> list[dict]:
    if len(symbols) > MAX_SYMBOLS_PER_REFRESH:
        raise ValueError(f"A refresh is capped at {MAX_SYMBOLS_PER_REFRESH} symbols")
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def bounded(symbol: str) -> dict:
        async with semaphore:
            return await fetch_yahoo_market_history(client, symbol, timeframe)

    return list(await asyncio.gather(*(bounded(symbol) for symbol in symbols)))


async def fetch_xauusd_history(client: httpx.AsyncClient, timeframe: str = "1h") -> dict:
    return await fetch_yahoo_market_history(client, XAUUSD_SOURCE_SYMBOL, timeframe)


async def fetch_xauusd_quote(client: httpx.AsyncClient) -> dict:
    result = await fetch_xauusd_history(client, "1m")
    if result["status"] in ("error", "no_results", "invalid_timeframe"):
        return result
    latest = result["bars"][-1]
    return {**result, **latest, "bars": []}
