import asyncio

import httpx

from assistant_api.tools import execute_tools


def test_feed_failure_preserves_source():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
            results = await execute_tools(("finance_news",), "ASX news", client, True)
        assert results[0]["status"] == "error"
        assert results[0]["source"] == "https://finance.yahoo.com/news/rssindex"
    asyncio.run(run())


def test_feed_and_quote_shapes():
    def handler(request):
        if "chart" in request.url.path:
            assert request.url.path.endswith("BHP.AX")
            return httpx.Response(200, json={"chart": {"result": [{"meta": {"symbol": "BHP.AX", "regularMarketPrice": 40, "currency": "AUD", "regularMarketTime": 123}}]}})
        return httpx.Response(200, text="<rss><channel><item><title>BHP ASX headline</title><link>https://example.org/news</link><pubDate>Today</pubDate></item></channel></rss>")
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            results = await execute_tools(("asx_quote", "finance_news"), "ASX bhp.ax news", client, True)
        assert results[0]["data"]["regularMarketPrice"] == 40
        assert results[0]["source"] == "https://finance.yahoo.com/quote/BHP.AX/"
        assert results[1]["data"][0]["source"] == "https://example.org/news"
    asyncio.run(run())
