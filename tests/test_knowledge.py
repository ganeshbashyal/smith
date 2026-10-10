import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from assistant_api.personas import BASE_INSTRUCTION, PERSONAS
from assistant_api.routing import conceptual_question, route_request
from assistant_api.storage import Store
from assistant_api.tools import SOURCES, execute_tools, feed_items, freshness

NOW = datetime(2026, 10, 10, 8, tzinfo=timezone.utc)
RSS = b"""<rss><channel><item><title>NASA launches mission</title>
<link>https://www.nasa.gov/mission</link><description>Space science update</description>
<pubDate>Sat, 10 Oct 2026 07:00:00 GMT</pubDate></item>
<item><title>Unrelated football</title><link>https://example.org/football</link></item></channel></rss>"""


@pytest.mark.parametrize(("question", "persona"), [
    ("Explain inflation", "finance"), ("Explain a PLC scan cycle", "engineering"),
    ("Explain heat transfer", "building"), ("Explain football rules", "sports"),
    ("What is wellbeing?", "health"), ("What is peer review?", "science"),
    ("Explain news verification", "news"),
])
def test_stable_concepts_use_no_unnecessary_network(question, persona):
    # Explicit persona is also how general questions with no matching keywords are supported.
    route = route_request(question, "daily", "research", persona=persona)
    assert route.tools == ()
    assert "Do not refuse a conceptual question" in BASE_INSTRUCTION
    assert "Training knowledge is not a live source" in BASE_INSTRUCTION


def test_current_routes_are_allowlisted_and_bounded():
    assert route_request("Latest NASA space news", "d", "r").tools == ("nasa_news",)
    assert route_request("Current RBA interest rates", "d", "r").tools == ("rba_news",)
    assert route_request("Latest SCADA developments", "d", "r").tools == ("technology_news",)
    assert route_request("Latest medical guidance", "d", "r").tools == ()
    assert route_request("Current Manchester United score", "d", "r").tools == ()
    for persona in PERSONAS:
        route = route_request("Latest updates", "d", "r", persona=persona)
        assert set(route.tools) <= set(PERSONAS[persona].tools)


def test_conceptual_format_does_not_apply_to_current_or_compliance_questions():
    assert conceptual_question("Explain inflation")
    assert conceptual_question("What is a PLC scan cycle?")
    assert not conceptual_question("Explain current inflation")
    assert not conceptual_question("How do I certify NCC compliant insulation?")


def test_feed_relevance_dates_and_content_scope():
    items = feed_items(RSS, "Latest NASA news", NOW)
    assert len(items) == 1
    assert items[0]["freshness"] == "recent"
    assert items[0]["content_scope"] == "feed_summary"
    assert feed_items(RSS, "Latest insulation news", NOW) == []
    assert freshness("2020-01-01T00:00:00+00:00", NOW, timedelta(days=7)) == "stale"
    assert freshness(None, NOW, timedelta(days=7)) == "unknown"
    assert freshness("Today", NOW, timedelta(days=7)) == "unknown"


def test_rba_rdf_feed_supported():
    xml = b"""<rdf:RDF xmlns:rdf="rdf" xmlns="http://purl.org/rss/1.0/" xmlns:dc="dc">
    <item><title>RBA monetary policy</title><link>https://www.rba.gov.au/release</link>
    <dc:date>2026-10-01T11:30:00+10:00</dc:date></item></rdf:RDF>"""
    item = feed_items(xml, "Current RBA monetary policy", NOW)[0]
    assert item["freshness"] == "stale"
    assert item["content_scope"] == "headline"


def test_cache_refresh_expiry_failure_and_disabled(tmp_path):
    calls = []
    failing = False
    def handler(request):
        calls.append(str(request.url))
        assert str(request.url) == SOURCES["nasa_news"]
        return httpx.Response(503) if failing else httpx.Response(200, content=RSS)
    async def run():
        nonlocal failing
        store = Store(tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            async def fetch(time=NOW, **kwargs):
                return (await execute_tools(("nasa_news",), "NASA news", client, True, store=store, now=time, **kwargs))[0]
            first = await fetch()
            assert first["status"] == "ok" and not first["cached"]
            second = await fetch(NOW + timedelta(minutes=1))
            assert second["cached"] and second["retrieved_at"] == first["retrieved_at"]
            assert len(calls) == 1
            await fetch(refresh=True)
            assert len(calls) == 2
            await fetch(NOW + timedelta(minutes=16))
            assert len(calls) == 3
            failing = True
            failed = await fetch(NOW + timedelta(minutes=32))
            assert failed["status"] == "error" and "data" not in failed
            disabled = (await execute_tools(("nasa_news",), "NASA news", client, False, store=store))[0]
            assert disabled["status"] == "disabled"
            assert len(calls) == 4
            assert store.tool_status()["nasa_news"]["status"] == "disabled"
    asyncio.run(run())


@pytest.mark.parametrize("body", [b"not XML", b"<rss/>", RSS])
def test_invalid_or_irrelevant_feeds_are_explicit(body):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))) as client:
            result = (await execute_tools(("science_news",), "Quantum gravity news", client, True))[0]
            assert result["status"] in ("error", "no_results")
            assert result["source"] == SOURCES["science_news"]
    asyncio.run(run())


def test_timeout_has_no_success_shaped_fallback():
    def handler(request):
        raise httpx.ReadTimeout("Timed out", request=request)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = (await execute_tools(("news_feed",), "news", client, True))[0]
            assert result["status"] == "error" and "data" not in result
    asyncio.run(run())
