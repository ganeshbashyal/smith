import asyncio
import html
import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
import httpx

FEEDS = {
    "BBC News": "https://feeds.bbci.co.uk/news/rss.xml",
    "NASA": "https://www.nasa.gov/rss/dyn/breaking_news.rss",
    "Medium Technology": "https://medium.com/feed/tag/technology",
    "Yahoo Finance": "https://finance.yahoo.com/news/rssindex",
}


async def read_feed(name: str, url: str, limit: int = 4) -> dict:
    try:
        async with httpx.AsyncClient(timeout=6, follow_redirects=True, headers={"User-Agent": "PersonalAssistant/0.1"}) as client:
            response = await client.get(url)
            response.raise_for_status()
        root = ET.fromstring(response.content)
        items = []
        for item in root.findall(".//item")[:limit]:
            title = html.unescape((item.findtext("title") or "").strip())
            link = (item.findtext("link") or "").strip()
            description = re.sub("<[^>]+>", "", html.unescape(item.findtext("description") or "")).strip()
            published = item.findtext("pubDate") or item.findtext("published") or ""
            try: published = parsedate_to_datetime(published).isoformat()
            except (TypeError, ValueError): pass
            if title and link: items.append({"title": title, "link": link, "summary": description[:180], "published": published})
        return {"source": name, "items": items}
    except Exception as exc:
        return {"source": name, "items": [], "error": str(exc)}


async def news_digest() -> list[dict]:
    return list(await asyncio.gather(*(read_feed(name, url) for name, url in FEEDS.items())))
