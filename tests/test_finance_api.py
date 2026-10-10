import csv
import io
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant_api.config import Settings
from assistant_api.main import create_app


class Ollama:
    """Minimal fake Ollama backend; finance tests never need real model output."""

    def __call__(self, request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": "daily"}, {"name": "research"}]})
        if request.url.path == "/api/ps":
            return httpx.Response(200, json={"models": [{"name": "daily"}]})
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={"message": {"content": "Answer referencing linked evidence"}})
        raise AssertionError(request.url)


@pytest.fixture
def finance_client(tmp_path):
    settings = Settings(tmp_path, "http://ollama", "daily", "research", "test-token", False)
    with TestClient(create_app(settings, ollama_transport=httpx.MockTransport(Ollama()))) as client:
        yield client


AUTH = {"Authorization": "Bearer test-token"}


def test_finance_endpoints_require_admin_token(finance_client):
    client = finance_client
    assert client.get("/api/finance/securities").status_code == 401
    assert client.post("/api/finance/securities", json={"symbol": "bhp", "name": "BHP Group"}).status_code == 401


def test_finance_page_served(finance_client):
    assert finance_client.get("/finance").status_code == 200


def test_security_create_and_list(finance_client):
    client = finance_client
    created = client.post("/api/finance/securities", headers=AUTH,
                          json={"symbol": "bhp", "name": "BHP Group", "sector": "Materials"})
    assert created.status_code == 200
    assert created.json()["symbol"] == "BHP"
    listing = client.get("/api/finance/securities", headers=AUTH).json()
    assert len(listing["securities"]) == 1
    assert client.get("/api/finance/securities/missing", headers=AUTH).status_code == 404


def test_evidence_requires_existing_security_and_text(finance_client):
    client = finance_client
    missing_security = client.post("/api/finance/securities/BHP/evidence", headers=AUTH, json={
        "symbol": "BHP", "title": "FY24", "publisher": "BHP", "source_type": "annual_report",
        "document_type": "annual_report", "text": "Revenue grew.",
    })
    assert missing_security.status_code == 422
    client.post("/api/finance/securities", headers=AUTH, json={"symbol": "BHP", "name": "BHP Group"})
    ok = client.post("/api/finance/securities/BHP/evidence", headers=AUTH, json={
        "symbol": "BHP", "title": "FY24", "publisher": "BHP", "source_type": "annual_report",
        "document_type": "annual_report", "text": "Revenue grew 5% to $50bn.",
    })
    assert ok.status_code == 200 and ok.json()["created"] is True
    listed = client.get("/api/finance/securities/BHP/evidence", headers=AUTH).json()
    assert len(listed["evidence"]) == 1


def test_statement_requires_evidence_or_manual_source_note(finance_client):
    client = finance_client
    client.post("/api/finance/securities", headers=AUTH, json={"symbol": "BHP", "name": "BHP Group"})
    rejected = client.post("/api/finance/securities/BHP/statements", headers=AUTH, json={
        "symbol": "BHP", "period": "annual", "fiscal_period_end": "2024-06-30", "currency": "AUD",
        "revenue": "50000",
    })
    assert rejected.status_code == 422
    accepted = client.post("/api/finance/securities/BHP/statements", headers=AUTH, json={
        "symbol": "BHP", "period": "annual", "fiscal_period_end": "2024-06-30", "currency": "AUD",
        "revenue": "50000", "source_note": "Manually entered from the FY24 investor presentation slide 4",
    })
    assert accepted.status_code == 200
    statements = client.get("/api/finance/securities/BHP/statements", headers=AUTH).json()
    assert statements["statements"][0]["figures"]["revenue"] == "50000"


def test_thesis_revision_compare_and_export(finance_client):
    client = finance_client
    client.post("/api/finance/securities", headers=AUTH, json={"symbol": "BHP", "name": "BHP Group"})
    client.post("/api/finance/securities/BHP/thesis", headers=AUTH, json={
        "symbol": "BHP", "thesis": "Long China demand",
    })
    client.post("/api/finance/securities/BHP/thesis", headers=AUTH, json={
        "symbol": "BHP", "thesis": "Demand slowing, trim position",
    })
    revisions = client.get("/api/finance/securities/BHP/thesis", headers=AUTH).json()["revisions"]
    assert [r["revision"] for r in revisions] == [1, 2]
    compare = client.get("/api/finance/securities/BHP/thesis/compare?from_revision=1&to_revision=2", headers=AUTH)
    assert compare.status_code == 200
    assert "thesis" in compare.json()["what_changed"]
    export = client.get("/api/finance/securities/BHP/thesis/export", headers=AUTH)
    assert export.status_code == 200
    assert "Demand slowing" in export.json()["markdown"]


def test_watchlist_create_cap_and_dedup(finance_client):
    client = finance_client
    created = client.post("/api/finance/watchlists", headers=AUTH, json={
        "name": "Miners", "symbols": ["bhp", "BHP", "rio"], "price_alert_threshold_pct": "5",
    })
    assert created.status_code == 200
    assert created.json()["symbols"] == ["BHP", "RIO"]
    too_many = client.post("/api/finance/watchlists", headers=AUTH, json={
        "name": "Too many", "symbols": [f"SYM{i}" for i in range(51)],
    })
    assert too_many.status_code == 422


def test_watchlist_refresh_uses_feeds_client_and_records_bars(finance_client):
    client = finance_client
    created = client.post("/api/finance/watchlists", headers=AUTH, json={
        "name": "Miners", "symbols": ["BHP"],
    }).json()

    def handler(request):
        return httpx.Response(200, json={"chart": {"result": [{
            "meta": {"currency": "AUD"}, "timestamp": [1704153600],
            "indicators": {"quote": [{"close": [45.5], "volume": [1000]}],
                          "adjclose": [{"adjclose": [45.0]}]},
        }]}})

    client.app.state.feeds = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    refreshed = client.post(f"/api/finance/watchlists/{created['id']}/refresh", headers=AUTH)
    assert refreshed.status_code == 200
    assert refreshed.json()["results"][0]["status"] == "ok"
    bars = client.get("/api/finance/securities/BHP/prices", headers=AUTH).json()["bars"]
    assert bars[0]["close"] == "45.5"


def test_valuation_ratios_and_dcf_persist_runs(finance_client):
    client = finance_client
    ratios = client.post("/api/finance/securities/BHP/valuation/ratios", headers=AUTH, json={
        "price": "45", "shares_diluted": "100", "net_income": "50",
    })
    assert ratios.status_code == 200
    assert ratios.json()["values"]["pe"] == "90"
    dcf = client.post("/api/finance/securities/BHP/valuation/dcf", headers=AUTH, json={
        "symbol": "BHP",
        "scenarios": [{"name": "base", "projected_fcff": ["100", "110"], "discount_rate": "0.1",
                      "terminal_growth_rate": "0.02", "net_debt": "0", "shares_diluted": "10", "probability": "1"}],
        "current_price": "5",
    })
    assert dcf.status_code == 200
    assert dcf.json()["margin_of_safety"] is not None
    bad_dcf = client.post("/api/finance/securities/BHP/valuation/dcf", headers=AUTH, json={
        "symbol": "BHP",
        "scenarios": [{"name": "base", "projected_fcff": ["100"], "discount_rate": "0.02",
                      "terminal_growth_rate": "0.05", "net_debt": "0", "shares_diluted": "10"}],
    })
    assert bad_dcf.status_code == 422
    runs = client.get("/api/finance/securities/BHP/valuation/runs", headers=AUTH).json()["runs"]
    assert len(runs) == 2


def test_portfolio_import_preview_and_atomic_confirm(finance_client):
    client = finance_client
    portfolio_id = client.post("/api/finance/portfolios", headers=AUTH, json={"name": "Main"}).json()["id"]

    good_csv = "symbol,units,is_cash\nBHP,100,false\nCASH,5000,true\n"
    preview = client.post(f"/api/finance/portfolios/{portfolio_id}/holdings/preview", headers=AUTH,
                          files={"file": ("holdings.csv", good_csv, "text/csv")})
    assert preview.status_code == 200
    assert preview.json()["valid"] is True

    bad_csv = "symbol,units,is_cash\nBHP,not-a-number,false\n"
    rejected = client.post(f"/api/finance/portfolios/{portfolio_id}/holdings/confirm", headers=AUTH,
                           files={"file": ("holdings.csv", bad_csv, "text/csv")})
    assert rejected.status_code == 422

    confirmed = client.post(f"/api/finance/portfolios/{portfolio_id}/holdings/confirm", headers=AUTH,
                            files={"file": ("holdings.csv", good_csv, "text/csv")})
    assert confirmed.status_code == 200
    assert confirmed.json()["imported"] == 2

    analysis = client.get(f"/api/finance/portfolios/{portfolio_id}/analysis", headers=AUTH)
    assert analysis.status_code == 200
    assert analysis.json()["coverage_count"] == 1  # BHP unquoted -> missing; CASH always valued.


def test_portfolio_json_import(finance_client):
    client = finance_client
    portfolio_id = client.post("/api/finance/portfolios", headers=AUTH, json={"name": "Main"}).json()["id"]
    payload = json.dumps([{"symbol": "RIO", "units": "20", "manual_aud_value": "1000"}])
    confirmed = client.post(f"/api/finance/portfolios/{portfolio_id}/holdings/confirm", headers=AUTH,
                            files={"file": ("holdings.json", payload, "application/json")})
    assert confirmed.status_code == 200
    assert confirmed.json()["imported"] == 1


def test_chat_links_finance_evidence_via_entity_symbol(finance_client):
    client = finance_client
    client.post("/api/finance/securities", headers=AUTH, json={"symbol": "BHP", "name": "BHP Group"})
    client.post("/api/finance/securities/BHP/evidence", headers=AUTH, json={
        "symbol": "BHP", "title": "FY24", "publisher": "BHP", "source_type": "annual_report",
        "document_type": "annual_report", "text": "Revenue grew 5% to $50bn.",
    })
    response = client.post("/api/chat", json={"question": "Summarize BHP", "entity_symbol": "bhp"})
    assert response.status_code == 200
    sources = response.json()["sources"]
    assert any(s["id"].startswith("finance_evidence:") for s in sources)
