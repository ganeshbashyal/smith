from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import logging
import re
import xml.etree.ElementTree as ET

import httpx
from assistant_api.storage import Store

logger = logging.getLogger(__name__)

FEEDS = {
    "news_feed": "https://feeds.bbci.co.uk/news/rss.xml",
    "finance_news": "https://finance.yahoo.com/news/rssindex",
    "technology_news": "https://feeds.bbci.co.uk/news/technology/rss.xml",
    "science_news": "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",
    "nasa_news": "https://www.nasa.gov/feed/",
    "rba_news": "https://www.rba.gov.au/rss/rss-cb-media-releases.xml",
    "esa_news": "https://www.esa.int/rssfeed/TopNews",
    "arxiv_ai": "https://export.arxiv.org/rss/cs.AI",
    "arxiv_physics": "https://export.arxiv.org/rss/physics",
    "sciencedaily_science": "https://www.sciencedaily.com/rss/top/science.xml",
    "sciencedaily_technology": "https://www.sciencedaily.com/rss/top/technology.xml",
}
SOURCES = {**FEEDS, "asx_quote": "https://finance.yahoo.com/",
           "f1_standings": "https://api.jolpi.ca/ergast/f1/current/driverStandings/"}

PUBLISHERS = {name: "BBC" for name in ("news_feed", "technology_news", "science_news")}
PUBLISHERS.update(finance_news="Yahoo Finance", asx_quote="Yahoo Finance",
                  nasa_news="NASA", rba_news="Reserve Bank of Australia", esa_news="European Space Agency",
                  arxiv_ai="arXiv", arxiv_physics="arXiv", sciencedaily_science="ScienceDaily",
                  sciencedaily_technology="ScienceDaily", f1_standings="Jolpica")
STOP_WORDS = set("what is are the a an in on of for and to explain how why latest current today news headlines updates developments please give me about".split())


def freshness(value: str | None, now: datetime, max_age: timedelta) -> str:
    if not value:
        return "unknown"
    try:
        try:
            date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            return "unknown"
        age = now - date
        return "recent" if timedelta(minutes=-5) <= age <= max_age else "stale"
    except (ValueError, TypeError, OverflowError):
        return "unknown"


def feed_items(content: bytes, question: str, now: datetime) -> list[dict]:
    root = ET.fromstring(content)
    terms = set(re.findall(r"\b\w{3,}\b", question.lower())) - STOP_WORDS
    items = []
    for item in root.iter():
        if item.tag.split("}")[-1] != "item":
            continue
        fields = {child.tag.split("}")[-1]: child.text or "" for child in item}
        title = fields.get("title", "").strip()
        link = fields.get("link", "").strip()
        if not title or not link.startswith(("https://", "http://")):
            continue
        summary = unescape(re.sub(r"<[^>]*>", "", fields.get("description", "")))[:400]
        score = len(terms & set(re.findall(r"\b\w{3,}\b", (title + " " + summary).lower())))
        if terms and not score:
            continue
        published = fields.get("pubDate") or fields.get("date") or None
        items.append((score, {"title": title, "source": link, "published": published,
                              "summary": summary, "content_scope": "feed_summary" if summary else "headline",
                              "freshness": freshness(published, now, timedelta(days=7))}))
    items.sort(key=lambda item: -item[0])
    return [item for _, item in items[:3]]


async def execute_tools(names: tuple[str, ...], question: str, client: httpx.AsyncClient,
                        enabled: bool, *, store: Store | None = None,
                        refresh: bool = False, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    results = []
    for name in names:
        source = SOURCES[name]
        result = {"id": f"tool:{name}", "tool": name, "source": source,
                  "publisher": PUBLISHERS[name], "retrieved_at": now.isoformat(),
                  "freshness": "unknown", "cached": False}
        if not enabled:
            result = {**result, "status": "disabled", "error": "External feeds are disabled"}
            results.append(result)
            if store:
                store.record_tool_status(name, result)
            continue
        cache_key = name + ":" + question.strip().lower()
        cached = store.cached_tool(cache_key, now.isoformat()) if store and not refresh else None
        if cached:
            cached = {**cached, "cached": True}
            if isinstance(cached.get("data"), list) and name in FEEDS:
                cached["data"] = [{**item, "freshness": freshness(item["published"], now, timedelta(days=7))}
                                  for item in cached["data"]]
                states = {item["freshness"] for item in cached["data"]}
                cached["freshness"] = next(iter(states)) if len(states) == 1 else "mixed" if states else "unknown"
            elif name == "asx_quote":
                cached["freshness"] = freshness(cached.get("observed_at"), now, timedelta(days=1))
            results.append(cached)
            store.record_tool_status(name, cached)
            continue
        try:
            if name in FEEDS:
                response = await client.get(source)
                response.raise_for_status()
                if len(response.content) > 2_000_000:
                    raise ValueError("Feed exceeds size budget")
                items = feed_items(response.content, question, now)
                if not items:
                    result.update(status="no_results", error="No relevant valid feed items found")
                data = items
            elif name == "asx_quote":
                symbol = re.search(r"\b[A-Za-z0-9]{2,6}\.AX\b", question, re.IGNORECASE).group().upper()
                source = f"https://finance.yahoo.com/quote/{symbol}/"
                result["source"] = source
                response = await client.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}", params={"range": "1d", "interval": "1d"})
                response.raise_for_status()
                meta = response.json()["chart"]["result"][0]["meta"]
                data = {key: meta.get(key) for key in ("symbol", "currency", "regularMarketPrice", "regularMarketTime")}
                if data["regularMarketPrice"] is None:
                    raise ValueError("Quote returned no price")
                observed = datetime.fromtimestamp(data["regularMarketTime"], timezone.utc).isoformat()
                result.update(observed_at=observed, freshness=freshness(observed, now, timedelta(days=1)))
            else:
                response = await client.get(source)
                response.raise_for_status()
                data = response.json()["MRData"]["StandingsTable"]["StandingsLists"]
                if not data:
                    raise ValueError("No standings available")
                result["observed_at"] = data[0].get("date")
                result["freshness"] = freshness(result["observed_at"], now, timedelta(days=14))
            if name in FEEDS and data:
                states = {item["freshness"] for item in data}
                result["freshness"] = next(iter(states)) if len(states) == 1 else "mixed"
            result = {**result, "status": result.get("status", "ok"), "data": data}
            if store:
                ttl = timedelta(minutes=2 if name == "asx_quote" else 15)
                store.save_tool(cache_key, result, (now + ttl).isoformat())
        except (httpx.HTTPError, ET.ParseError, ValueError, KeyError, IndexError, TypeError, AttributeError, OverflowError) as exc:
            logger.warning("External tool %s failed: %s", name, type(exc).__name__)
            result = {**result, "status": "error", "error": "Source unavailable or returned invalid data"}
        if store:
            store.record_tool_status(name, result)
        results.append(result)
    return results
