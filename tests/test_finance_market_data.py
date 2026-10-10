from datetime import datetime, timezone

import httpx
import pytest

from assistant_api.finance.market_data import fetch_symbol_history, refresh_history


def chart_payload(symbol: str, currency: str = "AUD"):
    ts = int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp())
    return {
        "chart": {
            "result": [{
                "meta": {"currency": currency, "symbol": symbol},
                "timestamp": [ts],
                "indicators": {
                    "quote": [{"close": [45.5], "volume": [1000]}],
                    "adjclose": [{"adjclose": [45.0]}],
                },
            }],
        },
    }


@pytest.mark.asyncio
async def test_fetch_symbol_history_success():
    def handler(request):
        return httpx.Response(200, json=chart_payload("BHP.AX"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_symbol_history(client, "BHP.AX")
    assert result["status"] == "ok"
    assert result["bars"][0]["close"] == "45.5"
    assert result["bars"][0]["adjusted_close"] == "45.0"
    assert result["currency"] == "AUD"


@pytest.mark.asyncio
async def test_fetch_symbol_history_retries_once_on_429_then_fails():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_symbol_history(client, "BHP.AX")
    assert result["status"] == "error"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_fetch_symbol_history_handles_malformed_payload():
    def handler(request):
        return httpx.Response(200, json={"chart": {"result": [{}]}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_symbol_history(client, "BHP.AX")
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_refresh_history_enforces_symbol_cap():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200))) as client:
        with pytest.raises(ValueError):
            await refresh_history(client, [f"SYM{i}" for i in range(51)])


@pytest.mark.asyncio
async def test_refresh_history_fetches_all_symbols():
    def handler(request):
        symbol = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(200, json=chart_payload(symbol))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await refresh_history(client, ["BHP.AX", "RIO.AX"])
    assert {r["symbol"] for r in results} == {"BHP.AX", "RIO.AX"}
    assert all(r["status"] == "ok" for r in results)
