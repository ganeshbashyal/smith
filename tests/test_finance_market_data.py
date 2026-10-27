from datetime import datetime, timezone

import httpx
import pytest

from assistant_api.finance.market_data import (
    fetch_symbol_history, fetch_xauusd_history, fetch_yahoo_market_history,
    refresh_history, refresh_market_history,
)


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


@pytest.mark.asyncio
async def test_fetch_symbol_history_rejects_non_asx_symbol_without_network_call():
    """Defect 7/8-adjacent regression: non-.AX symbols must be refused before any request."""
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=chart_payload("AAPL"))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_symbol_history(client, "AAPL")
    assert result["status"] == "invalid_symbol"
    assert calls == []


@pytest.mark.asyncio
async def test_fetch_symbol_history_uses_exchange_timezone_not_naive_utc():
    """Defect 7 regression: a Sydney evening bar must not shift to the UTC date."""
    # 2024-01-02 14:00 UTC == 2024-01-03 01:00 Australia/Sydney (AEDT, UTC+11).
    ts = int(datetime(2024, 1, 2, 14, 0, tzinfo=timezone.utc).timestamp())
    payload = {
        "chart": {"result": [{
            "meta": {"currency": "AUD", "symbol": "BHP.AX", "exchangeTimezoneName": "Australia/Sydney"},
            "timestamp": [ts],
            "indicators": {"quote": [{"close": [45.5], "volume": [1000]}],
                          "adjclose": [{"adjclose": [45.0]}]},
        }]},
    }

    def handler(request):
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_symbol_history(client, "BHP.AX")
    bar = result["bars"][0]
    assert bar["observed_on"] == "2024-01-03"  # Sydney-local date, not the UTC "2024-01-02"
    assert bar["exchange_timezone"] == "Australia/Sydney"
    assert bar["observed_at_utc"].startswith("2024-01-02")


@pytest.mark.asyncio
async def test_fetch_symbol_history_handles_null_and_mismatched_arrays():
    """Defect 7 regression: null/short adjclose arrays must not raise TypeError."""
    payload = {
        "chart": {"result": [{
            "meta": {"currency": "AUD", "symbol": "BHP.AX"},
            "timestamp": [1704153600, 1704240000],
            "indicators": {"quote": [{"close": [45.5, 46.0], "volume": [1000, None]}],
                          "adjclose": [{"adjclose": None}]},
        }]},
    }

    def handler(request):
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_symbol_history(client, "BHP.AX")
    assert result["status"] == "ok"
    assert len(result["bars"]) == 2
    assert all(bar["adjusted_close"] is None for bar in result["bars"])


@pytest.mark.asyncio
async def test_fetch_symbol_history_rejects_negative_or_nan_bars():
    """Defect 7 regression: non-finite/negative close or volume must be dropped, not stored."""
    payload = {
        "chart": {"result": [{
            "meta": {"currency": "AUD", "symbol": "BHP.AX"},
            "timestamp": [1704153600, 1704240000],
            "indicators": {"quote": [{"close": [-5.0, 46.0], "volume": [1000, 2000]}],
                          "adjclose": [{"adjclose": [-5.0, 46.0]}]},
        }]},
    }

    def handler(request):
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_symbol_history(client, "BHP.AX")
    assert len(result["bars"]) == 1  # the negative-close bar is dropped
    assert result["bars"][0]["close"] == "46.0"


@pytest.mark.asyncio
async def test_xauusd_proxy_returns_timestamped_ohlc_and_source_metadata():
    ts = int(datetime(2026, 10, 27, 8, 0, tzinfo=timezone.utc).timestamp())
    payload = {
        "chart": {"result": [{
            "meta": {"currency": "USD", "symbol": "XAUUSD=X"},
            "timestamp": [ts],
            "indicators": {"quote": [{
                "open": [2400.0], "high": [2410.0], "low": [2390.0],
                "close": [2405.0], "volume": [20],
            }]},
        }]},
    }

    def handler(request):
        assert request.url.params["interval"] == "1h"
        assert request.url.params["range"] == "3mo"
        assert "XAUUSD" in request.url.path
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_xauusd_history(client, "1h")
    assert result["instrument"] == "XAUUSD=X"
    assert result["timeframe"] == "1h"
    assert result["currency"] == "USD"
    assert result["source"].startswith("Yahoo Finance")
    assert result["bars"][0]["close"] == "2405.0"
    assert result["bars"][0]["observed_at"].startswith("2026-10-27T08:00:00")
    assert result["warning"].startswith("Yahoo Finance XAUUSD=X is an unofficial proxy")


@pytest.mark.asyncio
async def test_xauusd_provider_rejects_unsupported_timeframe_without_network():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_xauusd_history(client, "1w")
    assert result["status"] == "invalid_timeframe"
    assert calls == []


@pytest.mark.asyncio
async def test_yahoo_macro_adapter_allows_bounded_vix_and_fx_symbols():
    ts = int(datetime.now(timezone.utc).timestamp())
    payload = {"chart": {"result": [{
        "meta": {"currency": None, "symbol": "^VIX"},
        "timestamp": [ts],
        "indicators": {"quote": [{
            "open": [20], "high": [21], "low": [19], "close": [20.5], "volume": [100],
        }]},
    }]}}

    def handler(request):
        assert str(request.url).split("?", 1)[0].endswith("%5EVIX")
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_yahoo_market_history(client, "^VIX", "1d")
    assert result["instrument_type"] == "index"
    assert result["freshness"] == "fresh"
    assert result["bars"][0]["currency"] == "N/A"
    assert "unofficial" in result["warning"]


@pytest.mark.asyncio
async def test_yahoo_macro_adapter_rejects_unallowlisted_symbols_without_network():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await fetch_yahoo_market_history(client, "AAPL")
    assert result["status"] == "invalid_symbol"
    assert calls == []


@pytest.mark.asyncio
async def test_yahoo_market_adapter_accepts_validated_asx_ohlc():
    ts = int(datetime.now(timezone.utc).timestamp())
    payload = {"chart": {"result": [{
        "meta": {"currency": "AUD", "symbol": "CSL.AX"},
        "timestamp": [ts],
        "indicators": {"quote": [{
            "open": [290], "high": [300], "low": [280], "close": [295], "volume": [100],
        }]},
    }]}}

    def handler(request):
        assert request.url.path.endswith("CSL.AX")
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        results = await refresh_market_history(client, ["CSL.AX"], "1d")
    assert results[0]["instrument"] == "CSL.AX"
    assert results[0]["instrument_type"] == "asx_equity"
    assert results[0]["bars"][0]["close"] == "295"
