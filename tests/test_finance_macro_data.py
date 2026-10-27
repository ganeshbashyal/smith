from datetime import datetime, timedelta, timezone

import httpx
import pytest

from assistant_api.finance.macro_data import (
    FED_RSS_URL,
    fetch_federal_reserve_headlines,
    fetch_geopolitical_news,
    fetch_real_yield_curve,
    summarize_gold_macro_context,
)
from assistant_api.finance.repository import FinanceStore


@pytest.mark.asyncio
async def test_treasury_real_yield_feed_parses_dated_observations():
    observed = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    xml = f"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
      <entry><content><m:properties xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">
        <d:NEW_DATE>{observed}T00:00:00</d:NEW_DATE>
        <d:TC_10YEAR>1.92</d:TC_10YEAR>
      </m:properties></content></entry>
    </feed>"""

    def handler(request):
        assert "daily_treasury_real_yield_curve" in request.url.query.decode()
        return httpx.Response(200, text=xml)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_real_yield_curve(client)
    assert result["status"] == "ok"
    assert result["latest"]["instrument"] == "US10Y_REAL"
    assert result["latest"]["close"] == "1.92"
    assert result["latest"]["freshness"] == "fresh"


@pytest.mark.asyncio
async def test_federal_reserve_feed_keeps_policy_headline_attribution():
    rss = f"""<rss><channel><item>
      <title>Federal Reserve announces interest rate decision</title>
      <link>{FED_RSS_URL.replace('/feeds/press_all.xml', '/newsevents/pressreleases/test.htm')}</link>
      <pubDate>Tue, 27 Oct 2026 10:00:00 GMT</pubDate>
    </item><item><title>Community outreach event</title><link>https://example.com/event</link>
    </item></channel></rss>"""

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text=rss)),
    ) as client:
        result = await fetch_federal_reserve_headlines(client)
    assert result["status"] == "ok"
    assert len(result["items"]) == 1
    assert result["items"][0]["topic"] == "federal_reserve"
    assert "interest rate" in result["items"][0]["title"]
    assert result["items"][0]["status"] == "unverified"


@pytest.mark.asyncio
async def test_gdelt_news_is_capped_and_rejects_non_https_links():
    async def handler(request):
        assert request.url.params["maxrecords"] == "20"
        assert request.url.params["timespan"] == "24h"
        return httpx.Response(200, json={"articles": [
            {"title": "Gold and geopolitics", "url": "https://news.example/story",
             "domain": "news.example", "seendate": "20261027T100000Z"},
            {"title": "Unsafe link", "url": "http://bad.example/story"},
        ]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_geopolitical_news(client)
    assert result["status"] == "ok"
    assert len(result["items"]) == 1
    assert result["items"][0]["publisher"] == "news.example"
    assert result["items"][0]["source_url"] == "https://news.example/story"


def test_macro_summary_labels_vix_as_proxy_and_does_not_invent_fear_greed(tmp_path):
    store = FinanceStore(tmp_path / "finance.sqlite3")
    now = datetime.now(timezone.utc)
    bars = []
    for instrument, instrument_type, values, currency in (
        ("US10Y_REAL", "index", ("1.80", "1.90"), "N/A"),
        ("DX-Y.NYB", "index", ("100", "101"), "USD"),
        ("^VIX", "index", ("18", "22"), "N/A"),
    ):
        for days_ago, value in ((1, values[0]), (0, values[1])):
            observed = now - timedelta(days=days_ago)
            bars.append({
                "instrument": instrument, "instrument_type": instrument_type,
                "timeframe": "1d", "observed_at": observed.isoformat(),
                "source": "Mock source", "currency": currency,
                "open": value, "high": value, "low": value, "close": value,
                "volume": None, "retrieved_at": now.isoformat(), "freshness": "fresh",
            })
    store.save_market_observations(bars)

    result = summarize_gold_macro_context(store)
    assert result["status"] == "available"
    assert len(result["conditional_drivers"]) == 2
    assert result["risk_mood_proxy"]["label"] == "moderate volatility"
    assert result["fear_greed_index"]["status"] == "unavailable"
    assert "not a universal fear/greed index" in result["risk_mood_proxy"]["warning"]
