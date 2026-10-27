"""Bounded public macro/news connectors and transparent gold context summary."""

import logging
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

import httpx

from assistant_api.finance.market_data import fetch_yahoo_market_history

logger = logging.getLogger(__name__)

TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "pages/xml?data=daily_treasury_real_yield_curve&field_tdr_date_value={year}"
)
FED_RSS_URL = "https://www.federalreserve.gov/feeds/press_all.xml"
GDELT_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
MACRO_YAHOO_SYMBOLS = (
    "DX-Y.NYB", "EURUSD=X", "USDJPY=X", "^VIX", "^AXJO", "^GSPC", "^IXIC",
)
NEWS_LIMIT = 20
MAX_FEED_BYTES = 1_000_000


def _failure(source: str, message: str) -> dict:
    return {"status": "error", "source": source, "items": [], "error": message}


async def fetch_real_yield_curve(client: httpx.AsyncClient) -> dict:
    """Fetch official daily U.S. Treasury real-yield curve observations."""
    source = "U.S. Treasury daily real yield curve"
    url = TREASURY_URL.format(year=datetime.now(timezone.utc).year)
    try:
        response = await client.get(url)
        response.raise_for_status()
        if len(response.content) > MAX_FEED_BYTES:
            return _failure(source, "Treasury feed exceeds the 1 MB response limit")
        root = ET.fromstring(response.content)
        entries = []
        for properties in root.iter():
            if not properties.tag.endswith("properties"):
                continue
            values = {
                child.tag.rsplit("}", 1)[-1]: child.text
                for child in properties
            }
            observed = values.get("NEW_DATE")
            value = values.get("TC_10YEAR")
            if not observed or not value:
                continue
            observed_at = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            if observed_at.tzinfo is None:
                observed_at = observed_at.replace(tzinfo=timezone.utc)
            number = float(value)
            if not (number == number and abs(number) != float("inf")):
                continue
            retrieved_at = datetime.now(timezone.utc)
            entries.append({
                "instrument": "US10Y_REAL",
                "instrument_type": "index",
                "timeframe": "1d",
                "observed_at": observed_at.isoformat(),
                "source": source,
                "currency": "N/A",
                "open": str(number), "high": str(number),
                "low": str(number), "close": str(number),
                "volume": None,
                "retrieved_at": retrieved_at.isoformat(),
                "freshness": "fresh" if retrieved_at - observed_at <= timedelta(days=5) else "stale",
            })
        if not entries:
            return _failure(source, "No valid 10-year real-yield observations returned")
        entries.sort(key=lambda item: item["observed_at"])
        return {
            "status": "ok" if entries[-1]["freshness"] == "fresh" else "stale",
            "source": source, "items": entries[-30:],
            "latest": entries[-1], "error": None,
            "warning": "Daily Treasury real-yield observations; not an intraday quote.",
        }
    except (httpx.HTTPError, ET.ParseError, ValueError, TypeError) as exc:
        logger.warning("Treasury real-yield feed failed: %s", type(exc).__name__)
        return _failure(source, "Source unavailable or returned invalid data")


def _safe_source_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


async def fetch_federal_reserve_headlines(client: httpx.AsyncClient) -> dict:
    """Read a small set of dated policy-related headlines from the Fed RSS feed."""
    source = "Federal Reserve press releases RSS"
    try:
        response = await client.get(FED_RSS_URL)
        response.raise_for_status()
        if len(response.content) > MAX_FEED_BYTES:
            return _failure(source, "Federal Reserve feed exceeds the 1 MB response limit")
        root = ET.fromstring(response.content)
        items = []
        terms = ("federal funds", "interest rate", "monetary policy", "inflation", "fomc")
        for item in root.iter():
            if item.tag.rsplit("}", 1)[-1] != "item":
                continue
            fields = {
                child.tag.rsplit("}", 1)[-1]: (child.text or "").strip()
                for child in item
            }
            title = fields.get("title", "")
            link = fields.get("link", "")
            if not title or not any(term in title.casefold() for term in terms):
                continue
            if not _safe_source_url(link):
                continue
            items.append({
                "topic": "federal_reserve",
                "title": title[:300],
                "publisher": "Board of Governors of the Federal Reserve System",
                "source_url": link,
                "published_at": fields.get("pubDate") or None,
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "summary": "Official Federal Reserve headline; review the linked release for full context.",
                "status": "unverified",
                "evidence_ids": [],
            })
            if len(items) >= NEWS_LIMIT:
                break
        return {"status": "ok", "source": source, "items": items, "error": None}
    except (httpx.HTTPError, ET.ParseError, ValueError, TypeError) as exc:
        logger.warning("Federal Reserve RSS failed: %s", type(exc).__name__)
        return _failure(source, "Source unavailable or returned invalid data")


async def fetch_geopolitical_news(client: httpx.AsyncClient) -> dict:
    """Fetch bounded GDELT headline metadata; article text is not copied or stored."""
    source = "GDELT DOC API"
    params = {
        "query": '(gold OR bullion OR "central bank" OR equities OR markets OR inflation OR geopolitical)',
        "mode": "ArtList",
        "format": "json",
        "maxrecords": str(NEWS_LIMIT),
        "timespan": "24h",
        "sort": "HybridRel",
    }
    try:
        response = await client.get(GDELT_URL, params=params)
        response.raise_for_status()
        if len(response.content) > MAX_FEED_BYTES:
            return _failure(source, "GDELT response exceeds the 1 MB limit")
        payload = response.json()
        articles = payload.get("articles", [])
        if not isinstance(articles, list):
            return _failure(source, "GDELT returned an invalid article list")
        items = []
        now = datetime.now(timezone.utc).isoformat()
        for article in articles[:NEWS_LIMIT]:
            if not isinstance(article, dict):
                continue
            title, link = article.get("title"), article.get("url")
            if not isinstance(title, str) or not title.strip() or not isinstance(link, str):
                continue
            if not _safe_source_url(link):
                continue
            title_terms = title.casefold()
            topic = (
                "geopolitical_events"
                if any(term in title_terms for term in ("war", "sanction", "conflict", "geopolitic"))
                else "risk_sentiment"
            )
            items.append({
                "topic": topic,
                "title": title.strip()[:300],
                "publisher": str(article.get("domain") or "GDELT-indexed publisher")[:200],
                "source_url": link,
                "published_at": article.get("seendate"),
                "observed_at": now,
                "summary": "News index headline only; verify the publisher's article before relying on it.",
                "status": "unverified",
                "evidence_ids": [],
            })
        return {"status": "ok", "source": source, "items": items, "error": None}
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("GDELT news feed failed: %s", type(exc).__name__)
        return _failure(source, "Source unavailable or returned invalid data")


def summarize_gold_macro_context(store) -> dict:
    """Summarize only dated cached observations; never invent missing sentiment."""
    symbols = ("US10Y_REAL", "DX-Y.NYB", "EURUSD=X", "USDJPY=X", "^VIX")
    components = {}
    for symbol in symbols:
        bars = list(reversed(store.market_history(symbol, "1d", 2)))
        if not bars:
            components[symbol] = {"status": "unavailable", "observations": []}
            continue
        components[symbol] = {
            "status": "stale" if bars[-1]["freshness"] == "stale" else "available",
            "source": bars[-1]["source"],
            "observations": [{
                "observed_at": bar["observed_at"],
                "value": bar["close"],
                "currency": bar["currency"],
                "freshness": bar["freshness"],
            } for bar in bars],
        }

    drivers = []
    real_yields = components["US10Y_REAL"]["observations"]
    if components["US10Y_REAL"]["status"] == "available" and len(real_yields) > 1:
        change = float(real_yields[-1]["value"]) - float(real_yields[-2]["value"])
        if change >= 0.02:
            interpretation = "rising real yields can be a conditional headwind for gold"
        elif change <= -0.02:
            interpretation = "falling real yields can be a conditional support for gold"
        else:
            interpretation = "real yields are broadly unchanged over the last two observations"
        drivers.append({
            "factor": "10-year U.S. real yield",
            "change_percentage_points": round(change, 4),
            "interpretation": interpretation,
        })

    dollar = components["DX-Y.NYB"]["observations"]
    if components["DX-Y.NYB"]["status"] == "available" and len(dollar) > 1:
        previous, latest = float(dollar[-2]["value"]), float(dollar[-1]["value"])
        change_pct = (latest / previous - 1) * 100 if previous else 0
        if change_pct >= 0.1:
            interpretation = "a stronger dollar can be a conditional headwind for USD-priced gold"
        elif change_pct <= -0.1:
            interpretation = "a weaker dollar can be a conditional support for USD-priced gold"
        else:
            interpretation = "the dollar proxy is broadly unchanged over the last two observations"
        drivers.append({
            "factor": "U.S. Dollar Index proxy",
            "change_percent": round(change_pct, 4),
            "interpretation": interpretation,
        })

    vix_values = components["^VIX"]["observations"]
    if components["^VIX"]["status"] != "available":
        risk_mood = {"status": "unavailable", "label": None, "value": None}
    else:
        value = float(vix_values[-1]["value"])
        label = "low volatility" if value < 15 else "moderate volatility" if value < 25 else (
            "elevated volatility" if value < 35 else "high volatility"
        )
        risk_mood = {
            "status": "available", "label": label, "value": value,
            "observed_at": vix_values[-1]["observed_at"],
            "warning": "VIX is an equity-volatility proxy, not a universal fear/greed index or gold-direction signal.",
        }

    return {
        "category": "gold_macro",
        "status": "available" if any(item["status"] == "available" for item in components.values()) else "unavailable",
        "components": components,
        "conditional_drivers": drivers,
        "risk_mood_proxy": risk_mood,
        "fear_greed_index": {
            "status": "unavailable",
            "reason": "No validated universal fear-and-greed index is configured.",
        },
        "headlines": store.list_gold_macro_evidence(limit=20),
        "warning": "Context is descriptive, incomplete when feeds are missing, and not a trade recommendation.",
    }


def summarize_market_snapshot(store) -> dict:
    """Summarize cached index/volatility moves without presenting a fake score."""
    definitions = {
        "^AXJO": "ASX 200",
        "^GSPC": "S&P 500",
        "^IXIC": "NASDAQ Composite",
        "^VIX": "VIX equity-volatility proxy",
        "DX-Y.NYB": "U.S. Dollar Index proxy",
        "US10Y_REAL": "U.S. 10-year real yield",
    }
    instruments = {}
    index_directions = []
    for symbol, label in definitions.items():
        bars = list(reversed(store.market_history(symbol, "1d", 2)))
        if not bars:
            instruments[symbol] = {"name": label, "status": "unavailable", "observations": []}
            continue
        observations = [{
            "observed_at": bar["observed_at"],
            "value": bar["close"],
            "currency": bar["currency"],
            "freshness": bar["freshness"],
        } for bar in bars]
        item = {
            "name": label,
            "status": "stale" if bars[-1]["freshness"] == "stale" else "available",
            "source": bars[-1]["source"],
            "observations": observations,
        }
        if len(observations) > 1:
            previous, latest = float(observations[-2]["value"]), float(observations[-1]["value"])
            change_pct = ((latest / previous) - 1) * 100 if previous else None
            item["change_percent"] = round(change_pct, 4) if change_pct is not None else None
            item["direction"] = (
                "up" if change_pct is not None and change_pct > 0 else
                "down" if change_pct is not None and change_pct < 0 else "flat"
            )
            if symbol in ("^AXJO", "^GSPC", "^IXIC") and item["status"] == "available":
                index_directions.append(item["direction"])
        instruments[symbol] = item

    vix_bars = instruments["^VIX"]["observations"]
    if instruments["^VIX"]["status"] != "available":
        volatility = {"status": "unavailable", "label": None, "value": None}
    else:
        value = float(vix_bars[-1]["value"])
        volatility = {
            "status": "available",
            "label": "low" if value < 15 else "moderate" if value < 25 else "elevated" if value < 35 else "high",
            "value": value,
            "observed_at": vix_bars[-1]["observed_at"],
        }
    if len(index_directions) < 2:
        tone = "insufficient_data"
        rationale = "At least two fresh equity-index moves are needed for a broad-market description."
    elif index_directions.count("up") >= 2 and volatility["status"] == "available" and (
        instruments["^VIX"].get("direction") in ("down", "flat")
    ):
        tone = "risk_on_conditions"
        rationale = "Most available equity indexes rose while the VIX proxy did not rise."
    elif index_directions.count("down") >= 2 and volatility["status"] == "available" and (
        instruments["^VIX"].get("direction") == "up"
    ):
        tone = "risk_off_conditions"
        rationale = "Most available equity indexes fell while the VIX proxy rose."
    else:
        tone = "mixed"
        rationale = "Available index and volatility signals do not meet the descriptive risk-on or risk-off rules."

    headlines = store.list_gold_macro_evidence(limit=20)
    return {
        "status": "available" if any(item["status"] == "available" for item in instruments.values()) else "unavailable",
        "as_of": max(
            (observation["observed_at"] for item in instruments.values()
             for observation in item["observations"]),
            default=None,
        ),
        "markets": instruments,
        "market_tone": {
            "label": tone,
            "rationale": rationale,
            "method": "Descriptive rule over available ASX 200, S&P 500, NASDAQ and VIX daily changes; not a forecast or investment signal.",
        },
        "risk_mood_proxy": {
            **volatility,
            "warning": "VIX measures expected U.S. equity volatility; it is not a universal fear/greed index.",
        },
        "headlines": headlines,
        "warning": "Market data may be delayed or stale. Headline summaries are unverified leads, not confirmed sentiment.",
    }
