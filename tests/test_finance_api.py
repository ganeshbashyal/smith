import json
from decimal import Decimal

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


def _make_client(tmp_path, external_feeds=False):
    settings = Settings(tmp_path, "http://ollama", "daily", "research", "test-admin-token-for-local-test-only", external_feeds)
    return TestClient(
        create_app(settings, ollama_transport=httpx.MockTransport(Ollama())),
        client=("127.0.0.1", 12345),
    )


@pytest.fixture
def finance_client(tmp_path):
    with _make_client(tmp_path) as client:
        yield client


@pytest.fixture
def feeds_enabled_client(tmp_path):
    with _make_client(tmp_path, external_feeds=True) as client:
        yield client


AUTH = {"Authorization": "Bearer test-admin-token-for-local-test-only"}


def _create_bhp(client):
    return client.post("/api/finance/securities", headers=AUTH, json={"symbol": "BHP.AX", "name": "BHP Group"})


def test_finance_endpoints_require_admin_token(finance_client):
    client = finance_client
    assert client.get("/api/finance/securities").status_code == 401
    assert client.post("/api/finance/securities", json={"symbol": "BHP.AX", "name": "BHP Group"}).status_code == 401


def test_finance_page_served(finance_client):
    assert finance_client.get("/finance").status_code == 200


def test_security_create_and_list(finance_client):
    client = finance_client
    created = client.post("/api/finance/securities", headers=AUTH,
                          json={"symbol": "bhp.ax", "name": "BHP Group", "sector": "Materials"})
    assert created.status_code == 200
    assert created.json()["symbol"] == "BHP.AX"
    listing = client.get("/api/finance/securities", headers=AUTH).json()
    assert len(listing["securities"]) == 1
    assert client.get("/api/finance/securities/missing", headers=AUTH).status_code == 404


def test_evidence_requires_existing_security_and_text(finance_client):
    client = finance_client
    missing_security = client.post("/api/finance/securities/BHP.AX/evidence", headers=AUTH, json={
        "symbol": "BHP.AX", "title": "FY24", "publisher": "BHP", "source_type": "annual_report",
        "document_type": "annual_report", "text": "Revenue grew.",
    })
    assert missing_security.status_code == 422
    _create_bhp(client)
    ok = client.post("/api/finance/securities/BHP.AX/evidence", headers=AUTH, json={
        "symbol": "BHP.AX", "title": "FY24", "publisher": "BHP", "source_type": "annual_report",
        "document_type": "annual_report", "text": "Revenue grew 5% to $50bn.",
    })
    assert ok.status_code == 200 and ok.json()["created"] is True
    listed = client.get("/api/finance/securities/BHP.AX/evidence", headers=AUTH).json()
    assert len(listed["evidence"]) == 1


def test_statement_requires_evidence_or_manual_source_note(finance_client):
    client = finance_client
    _create_bhp(client)
    rejected = client.post("/api/finance/securities/BHP.AX/statements", headers=AUTH, json={
        "symbol": "BHP.AX", "period": "annual", "fiscal_period_end": "2024-06-30", "currency": "AUD",
        "revenue": "50000",
    })
    assert rejected.status_code == 422
    accepted = client.post("/api/finance/securities/BHP.AX/statements", headers=AUTH, json={
        "symbol": "BHP.AX", "period": "annual", "fiscal_period_end": "2024-06-30", "currency": "AUD",
        "revenue": "50000", "source_note": "Manually entered from the FY24 investor presentation slide 4",
    })
    assert accepted.status_code == 200
    body = accepted.json()
    assert body["revision"] == 1
    statements = client.get("/api/finance/securities/BHP.AX/statements", headers=AUTH).json()
    assert statements["statements"][0]["figures"]["revenue"] == "50000"


def test_statement_resave_creates_new_revision_never_overwrites(finance_client):
    client = finance_client
    _create_bhp(client)
    body = {"symbol": "BHP.AX", "period": "annual", "fiscal_period_end": "2024-06-30", "currency": "AUD",
            "revenue": "50000", "source_note": "First cut"}
    first = client.post("/api/finance/securities/BHP.AX/statements", headers=AUTH, json=body)
    assert first.json()["revision"] == 1
    body2 = {**body, "revenue": "52000", "source_note": "Restated after audit"}
    second = client.post("/api/finance/securities/BHP.AX/statements", headers=AUTH, json=body2)
    assert second.json()["revision"] == 2
    revisions = client.get(
        "/api/finance/securities/BHP.AX/statements/annual/2024-06-30/revisions", headers=AUTH,
    ).json()["revisions"]
    assert [r["figures"]["revenue"] for r in revisions] == ["50000", "52000"]
    # The "current" list_statements view only ever shows the latest revision.
    latest = client.get("/api/finance/securities/BHP.AX/statements", headers=AUTH).json()["statements"]
    assert len(latest) == 1 and latest[0]["figures"]["revenue"] == "52000"


def test_statement_csv_preview_and_atomic_confirm(finance_client):
    client = finance_client
    _create_bhp(client)
    good_csv = ("period,fiscal_period_end,currency,revenue,source_note\n"
               "annual,2024-06-30,AUD,50000,Imported from the FY24 annual report\n")
    preview = client.post("/api/finance/securities/BHP.AX/statements/preview", headers=AUTH,
                          files={"file": ("statements.csv", good_csv, "text/csv")})
    assert preview.status_code == 200 and preview.json()["valid"] is True

    bad_csv = "period,fiscal_period_end,currency,revenue\nannual,2024-06-30,AUD,not-a-number\n"
    rejected = client.post("/api/finance/securities/BHP.AX/statements/confirm", headers=AUTH,
                           files={"file": ("statements.csv", bad_csv, "text/csv")})
    assert rejected.status_code == 422

    confirmed = client.post("/api/finance/securities/BHP.AX/statements/confirm", headers=AUTH,
                            files={"file": ("statements.csv", good_csv, "text/csv")})
    assert confirmed.status_code == 200 and confirmed.json()["imported"] == 1


def test_evidence_ownership_enforced_on_statement_link(finance_client):
    client = finance_client
    _create_bhp(client)
    client.post("/api/finance/securities", headers=AUTH, json={"symbol": "RIO.AX", "name": "Rio Tinto"})
    other_evidence = client.post("/api/finance/securities/RIO.AX/evidence", headers=AUTH, json={
        "symbol": "RIO.AX", "title": "RIO FY24", "publisher": "RIO", "source_type": "annual_report",
        "document_type": "annual_report", "text": "Iron ore volumes rose.",
    }).json()["id"]
    cross_owned = client.post("/api/finance/securities/BHP.AX/statements", headers=AUTH, json={
        "symbol": "BHP.AX", "period": "annual", "fiscal_period_end": "2024-06-30", "currency": "AUD",
        "revenue": "50000", "evidence_id": other_evidence,
    })
    assert cross_owned.status_code == 422


def test_thesis_revision_compare_and_export_cites_evidence(finance_client):
    client = finance_client
    _create_bhp(client)
    evidence_id = client.post("/api/finance/securities/BHP.AX/evidence", headers=AUTH, json={
        "symbol": "BHP.AX", "title": "FY24 annual report", "publisher": "BHP",
        "source_type": "annual_report", "document_type": "annual_report", "source_url": "https://bhp.example/fy24",
        "text": "Revenue grew 5% to $50bn.",
    }).json()["id"]
    client.post("/api/finance/securities/BHP.AX/thesis", headers=AUTH, json={
        "symbol": "BHP.AX", "thesis": "Long China demand",
    })
    client.post("/api/finance/securities/BHP.AX/thesis", headers=AUTH, json={
        "symbol": "BHP.AX", "thesis": "Demand slowing, trim position", "evidence_ids": [evidence_id],
    })
    revisions = client.get("/api/finance/securities/BHP.AX/thesis", headers=AUTH).json()["revisions"]
    assert [r["revision"] for r in revisions] == [1, 2]
    compare = client.get("/api/finance/securities/BHP.AX/thesis/compare?from_revision=1&to_revision=2", headers=AUTH)
    assert compare.status_code == 200
    what_changed = compare.json()["what_changed"]
    assert "thesis" in what_changed
    assert "evidence_ids" in what_changed  # Defect-9 regression: newly linked evidence must be detected.
    export = client.get("/api/finance/securities/BHP.AX/thesis/export", headers=AUTH)
    assert export.status_code == 200
    markdown = export.json()["markdown"]
    assert "Demand slowing" in markdown
    assert "FY24 annual report" in markdown and "https://bhp.example/fy24" in markdown


def test_thesis_evidence_ownership_enforced(finance_client):
    client = finance_client
    _create_bhp(client)
    client.post("/api/finance/securities", headers=AUTH, json={"symbol": "RIO.AX", "name": "Rio Tinto"})
    other_evidence = client.post("/api/finance/securities/RIO.AX/evidence", headers=AUTH, json={
        "symbol": "RIO.AX", "title": "RIO FY24", "publisher": "RIO", "source_type": "annual_report",
        "document_type": "annual_report", "text": "Iron ore volumes rose.",
    }).json()["id"]
    rejected = client.post("/api/finance/securities/BHP.AX/thesis", headers=AUTH, json={
        "symbol": "BHP.AX", "thesis": "Long China demand", "evidence_ids": [other_evidence],
    })
    assert rejected.status_code == 422


def test_watchlist_create_cap_and_dedup(finance_client):
    client = finance_client
    created = client.post("/api/finance/watchlists", headers=AUTH, json={
        "name": "Miners", "symbols": ["bhp.ax", "BHP.AX", "rio.ax"], "price_alert_threshold_pct": "5",
    })
    assert created.status_code == 200
    assert created.json()["symbols"] == ["BHP.AX", "RIO.AX"]
    too_many = client.post("/api/finance/watchlists", headers=AUTH, json={
        "name": "Too many", "symbols": [f"S{i}.AX" for i in range(51)],
    })
    assert too_many.status_code == 422
    not_asx = client.post("/api/finance/watchlists", headers=AUTH, json={
        "name": "Not ASX", "symbols": ["AAPL"],
    })
    assert not_asx.status_code == 422


def test_watchlist_refresh_disabled_without_external_feeds(finance_client):
    client = finance_client
    created = client.post("/api/finance/watchlists", headers=AUTH, json={
        "name": "Miners", "symbols": ["BHP.AX"],
    }).json()
    refreshed = client.post(f"/api/finance/watchlists/{created['id']}/refresh", headers=AUTH)
    assert refreshed.status_code == 503
    assert "MR_SMITH_EXTERNAL_FEEDS" in refreshed.json()["detail"]


def test_watchlist_refresh_uses_feeds_client_and_records_bars_when_enabled(feeds_enabled_client):
    client = feeds_enabled_client
    created = client.post("/api/finance/watchlists", headers=AUTH, json={
        "name": "Miners", "symbols": ["BHP.AX"],
    }).json()

    def handler(request):
        return httpx.Response(200, json={"chart": {"result": [{
            "meta": {"currency": "AUD", "exchangeTimezoneName": "Australia/Sydney"},
            "timestamp": [1704153600],
            "indicators": {"quote": [{"close": [45.5], "volume": [1000]}],
                          "adjclose": [{"adjclose": [45.0]}]},
        }]}})

    client.app.state.feeds = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    refreshed = client.post(f"/api/finance/watchlists/{created['id']}/refresh", headers=AUTH)
    assert refreshed.status_code == 200
    assert refreshed.json()["results"][0]["status"] == "ok"
    bars = client.get("/api/finance/securities/BHP.AX/prices", headers=AUTH).json()["bars"]
    assert bars[0]["close"] == "45.5"
    assert bars[0]["exchange_timezone"] == "Australia/Sydney"


def test_valuation_ratios_requires_confirmed_statement_and_applies_scale(finance_client):
    client = finance_client
    _create_bhp(client)
    without_statement = client.post("/api/finance/securities/BHP.AX/valuation/ratios", headers=AUTH, json={
        "price": "45", "price_currency": "AUD", "price_date": "2024-07-01", "statement_period": "annual",
        "statement_fiscal_period_end": "2024-06-30",
    })
    assert without_statement.status_code == 422

    client.post("/api/finance/securities/BHP.AX/statements", headers=AUTH, json={
        "symbol": "BHP.AX", "period": "annual", "fiscal_period_end": "2024-06-30", "currency": "AUD",
        "scale": "millions", "shares_diluted": "2", "net_income": "25",
        "source_note": "FY24 annual report, p.12",
    })
    ratios = client.post("/api/finance/securities/BHP.AX/valuation/ratios", headers=AUTH, json={
        "price": "45", "price_currency": "AUD", "price_date": "2024-07-01", "statement_period": "annual",
        "statement_fiscal_period_end": "2024-06-30",
    })
    assert ratios.status_code == 200
    body = ratios.json()
    # net_income of 25 "millions" scale -> 25,000,000 units; market_cap = 45 * 2 = 90.
    assert body["values"]["market_cap"] == "90"
    from decimal import Decimal
    assert Decimal(body["values"]["pe"]) == Decimal(90) / Decimal(25_000_000)
    assert body["provenance"]["statement_revision"] == 1


def test_valuation_ratios_rejects_price_currency_mismatch(finance_client):
    """Defect regression: ASX price in AUD against a USD-reporting issuer must
    be rejected rather than silently treated as directly comparable."""
    client = finance_client
    _create_bhp(client)
    client.post("/api/finance/securities/BHP.AX/statements", headers=AUTH, json={
        "symbol": "BHP.AX", "period": "annual", "fiscal_period_end": "2024-06-30", "currency": "USD",
        "scale": "millions", "shares_diluted": "2", "net_income": "25",
        "source_note": "FY24 20-F, p.12",
    })
    mismatched = client.post("/api/finance/securities/BHP.AX/valuation/ratios", headers=AUTH, json={
        "price": "45", "price_currency": "AUD", "price_date": "2024-07-01", "statement_period": "annual",
        "statement_fiscal_period_end": "2024-06-30",
    })
    assert mismatched.status_code == 422
    assert "price_currency" in mismatched.json()["detail"]

    matching = client.post("/api/finance/securities/BHP.AX/valuation/ratios", headers=AUTH, json={
        "price": "45", "price_currency": "USD", "price_date": "2024-07-01", "statement_period": "annual",
        "statement_fiscal_period_end": "2024-06-30",
    })
    assert matching.status_code == 200


def test_valuation_dcf_persists_runs_and_margin_of_safety(finance_client):
    client = finance_client
    dcf = client.post("/api/finance/securities/BHP.AX/valuation/dcf", headers=AUTH, json={
        "symbol": "BHP.AX",
        "scenarios": [{"name": "base", "projected_fcff": ["100", "110"], "discount_rate": "0.1",
                      "terminal_growth_rate": "0.02", "net_debt": "0", "shares_diluted": "10", "probability": "1"}],
        "current_price": "5", "current_price_date": "2024-07-01",
    })
    assert dcf.status_code == 200
    assert dcf.json()["margin_of_safety"] is not None
    assert dcf.json()["margin_of_safety"]["price_date"] == "2024-07-01"
    missing_date = client.post("/api/finance/securities/BHP.AX/valuation/dcf", headers=AUTH, json={
        "symbol": "BHP.AX",
        "scenarios": [{"name": "base", "projected_fcff": ["100", "110"], "discount_rate": "0.1",
                      "terminal_growth_rate": "0.02", "net_debt": "0", "shares_diluted": "10"}],
        "current_price": "5",
    })
    assert missing_date.status_code == 422
    bad_dcf = client.post("/api/finance/securities/BHP.AX/valuation/dcf", headers=AUTH, json={
        "symbol": "BHP.AX",
        "scenarios": [{"name": "base", "projected_fcff": ["100"], "discount_rate": "0.02",
                      "terminal_growth_rate": "0.05", "net_debt": "0", "shares_diluted": "10"}],
    })
    assert bad_dcf.status_code == 422
    runs = client.get("/api/finance/securities/BHP.AX/valuation/runs", headers=AUTH).json()["runs"]
    assert len(runs) == 1  # the missing-date and invalid-rate requests were rejected before persisting.


def test_valuation_dcf_sensitivity_and_export(finance_client):
    client = finance_client
    sensitivity = client.post("/api/finance/securities/BHP.AX/valuation/dcf/sensitivity", headers=AUTH, json={
        "base": {"name": "base", "projected_fcff": ["100", "110"], "discount_rate": "0.1",
                "terminal_growth_rate": "0.02", "net_debt": "0", "shares_diluted": "10"},
        "discount_rate_deltas": ["-0.01", "0", "0.01"],
        "terminal_growth_deltas": ["-0.01", "0", "0.01"],
    })
    assert sensitivity.status_code == 200
    assert len(sensitivity.json()["rows"]) == 3

    dcf = client.post("/api/finance/securities/BHP.AX/valuation/dcf", headers=AUTH, json={
        "symbol": "BHP.AX",
        "scenarios": [{"name": "base", "projected_fcff": ["100", "110"], "discount_rate": "0.1",
                      "terminal_growth_rate": "0.02", "net_debt": "0", "shares_diluted": "10", "probability": "1"}],
        "current_price": "5", "current_price_date": "2024-07-01",
    })
    run_id = dcf.json()["id"]
    export = client.get(f"/api/finance/securities/BHP.AX/valuation/dcf/export?run_id={run_id}", headers=AUTH)
    assert export.status_code == 200
    assert "Margin of safety" in export.json()["markdown"]


def test_portfolio_import_preview_and_atomic_confirm(finance_client):
    client = finance_client
    portfolio_id = client.post("/api/finance/portfolios", headers=AUTH, json={"name": "Main"}).json()["id"]

    good_csv = "symbol,units,is_cash\nBHP.AX,100,false\nCASH,5000,true\n"
    preview = client.post(f"/api/finance/portfolios/{portfolio_id}/holdings/preview", headers=AUTH,
                          files={"file": ("holdings.csv", good_csv, "text/csv")})
    assert preview.status_code == 200
    assert preview.json()["valid"] is True

    bad_csv = "symbol,units,is_cash\nBHP.AX,not-a-number,false\n"
    rejected = client.post(f"/api/finance/portfolios/{portfolio_id}/holdings/confirm", headers=AUTH,
                           files={"file": ("holdings.csv", bad_csv, "text/csv")})
    assert rejected.status_code == 422

    negative_csv = "symbol,units,is_cash\nBHP.AX,-5,false\n"
    rejected_negative = client.post(f"/api/finance/portfolios/{portfolio_id}/holdings/confirm", headers=AUTH,
                                    files={"file": ("holdings.csv", negative_csv, "text/csv")})
    assert rejected_negative.status_code == 422

    dup_csv = "symbol,units,is_cash\nBHP.AX,10,false\nBHP.AX,20,false\n"
    rejected_dup = client.post(f"/api/finance/portfolios/{portfolio_id}/holdings/confirm", headers=AUTH,
                               files={"file": ("holdings.csv", dup_csv, "text/csv")})
    assert rejected_dup.status_code == 422

    confirmed = client.post(f"/api/finance/portfolios/{portfolio_id}/holdings/confirm", headers=AUTH,
                            files={"file": ("holdings.csv", good_csv, "text/csv")})
    assert confirmed.status_code == 200
    assert confirmed.json()["imported"] == 2

    analysis = client.get(f"/api/finance/portfolios/{portfolio_id}/analysis", headers=AUTH)
    assert analysis.status_code == 200
    body = analysis.json()
    assert body["coverage_count"] == 1  # BHP.AX unquoted -> missing; CASH always valued.
    assert body["coverage_complete"] is False
    assert body["total_value_aud"] is None  # Defect-1 regression: never silently renormalized.
    assert body["covered_value_aud"] is not None
    # Follow-up regression: weights/sector_concentration must never substitute
    # for whole-portfolio figures when coverage is incomplete; only the
    # explicitly-named covered_* figures are populated.
    assert body["weights"] is None
    assert body["sector_concentration"] is None
    assert body["covered_weights"]["CASH"] == "1"
    assert any(h["symbol"] == "BHP.AX" and h["market_value_aud"] is None for h in body["holdings_detail"])


def test_portfolio_json_import_overseas_requires_dated_manual_value(finance_client):
    client = finance_client
    portfolio_id = client.post("/api/finance/portfolios", headers=AUTH, json={"name": "Main"}).json()["id"]
    undated = json.dumps([{"symbol": "AAPL", "units": "20", "manual_aud_value": "1000"}])
    rejected = client.post(f"/api/finance/portfolios/{portfolio_id}/holdings/confirm", headers=AUTH,
                           files={"file": ("holdings.json", undated, "application/json")})
    assert rejected.status_code == 422
    dated = json.dumps([{"symbol": "AAPL", "units": "20", "manual_aud_value": "1000",
                        "valuation_date": "2024-07-01"}])
    confirmed = client.post(f"/api/finance/portfolios/{portfolio_id}/holdings/confirm", headers=AUTH,
                            files={"file": ("holdings.json", dated, "application/json")})
    assert confirmed.status_code == 200
    assert confirmed.json()["imported"] == 1


def test_portfolio_holdings_single_row_crud(finance_client):
    client = finance_client
    portfolio_id = client.post("/api/finance/portfolios", headers=AUTH, json={"name": "Main"}).json()["id"]
    added = client.post(f"/api/finance/portfolios/{portfolio_id}/holdings", headers=AUTH, json={
        "symbol": "BHP.AX", "units": "100",
    })
    assert added.status_code == 200
    holding_id = added.json()["id"]
    duplicate = client.post(f"/api/finance/portfolios/{portfolio_id}/holdings", headers=AUTH, json={
        "symbol": "BHP.AX", "units": "50",
    })
    assert duplicate.status_code == 422
    updated = client.put(f"/api/finance/portfolios/{portfolio_id}/holdings/{holding_id}", headers=AUTH, json={
        "symbol": "BHP.AX", "units": "150",
    })
    assert updated.status_code == 200
    listed = client.get(f"/api/finance/portfolios/{portfolio_id}/holdings", headers=AUTH).json()["holdings"]
    assert listed[0]["units"] == "150"
    deleted = client.delete(f"/api/finance/portfolios/{portfolio_id}/holdings/{holding_id}", headers=AUTH)
    assert deleted.status_code == 200
    assert client.get(f"/api/finance/portfolios/{portfolio_id}/holdings", headers=AUTH).json()["holdings"] == []


def test_portfolio_scenario_validates_shocks(finance_client):
    client = finance_client
    portfolio_id = client.post("/api/finance/portfolios", headers=AUTH, json={"name": "Main"}).json()["id"]
    client.post(f"/api/finance/portfolios/{portfolio_id}/holdings", headers=AUTH, json={
        "symbol": "CASH", "units": "1000", "is_cash": True,
    })
    invalid = client.post(f"/api/finance/portfolios/{portfolio_id}/scenario", headers=AUTH, json=[
        {"label": "Crash", "symbol_or_group": "CASH", "pct_change": "-150"},
    ])
    assert invalid.status_code == 422
    valid = client.post(f"/api/finance/portfolios/{portfolio_id}/scenario", headers=AUTH, json=[
        {"label": "Crash", "symbol_or_group": "CASH", "pct_change": "-10"},
    ])
    assert valid.status_code == 200
    body = valid.json()
    assert body["coverage_complete"] is True
    assert body["baseline_total_aud"] == "1000"

    client.post(f"/api/finance/portfolios/{portfolio_id}/holdings", headers=AUTH, json={
        "symbol": "BHP.AX", "units": "10",
    })
    incomplete = client.post(f"/api/finance/portfolios/{portfolio_id}/scenario", headers=AUTH, json=[
        {"label": "Crash", "symbol_or_group": "CASH", "pct_change": "-10"},
    ])
    assert incomplete.status_code == 200
    incomplete_body = incomplete.json()
    # Regression: an unresolved holding (BHP.AX has no price bar) must withhold
    # the whole-portfolio baseline/shocked totals, not silently report the
    # covered-only subtotal as if it were the full portfolio.
    assert incomplete_body["coverage_complete"] is False
    assert incomplete_body["baseline_total_aud"] is None
    assert incomplete_body["shocked_total_aud"] is None
    assert incomplete_body["covered_baseline_total_aud"] == "1000"
    assert "BHP.AX" in incomplete_body["unresolved_symbols"]


def test_portfolio_correlation_endpoint(finance_client):
    client = finance_client
    portfolio_id = client.post("/api/finance/portfolios", headers=AUTH, json={"name": "Main"}).json()["id"]
    correlation = client.get(
        f"/api/finance/portfolios/{portfolio_id}/correlation?symbol_a=BHP.AX&symbol_b=RIO.AX", headers=AUTH,
    )
    assert correlation.status_code == 200
    assert correlation.json()["available"] is False  # No price history yet; explicitly unavailable, not zero.


def test_return_series_uses_adjusted_close_and_aligns_only_shared_intervals():
    """Follow-up regression: correlation must derive returns purely from
    adjusted_close (dividends/splits accounted for) and must align on the
    exact (start_date, end_date) interval of each return, not just intersect
    end dates."""
    from assistant_api.finance.api import _align_series, _return_series

    bars_a = [
        {"observed_on": "2024-01-01", "close": "100", "adjusted_close": "100"},
        {"observed_on": "2024-01-02", "close": "110", "adjusted_close": "100"},
        {"observed_on": "2024-01-03", "close": "121", "adjusted_close": "110"},
    ]
    series_a = _return_series(bars_a)
    assert [(s, e) for s, e, _ in series_a] == [("2024-01-01", "2024-01-02"), ("2024-01-02", "2024-01-03")]
    assert series_a[0][2] == 0  # adjusted_close flat day 1->2, despite close rising 10%.
    assert series_a[1][2] == Decimal("0.1")  # adjusted_close +10% day 2->3.

    # series_b shares the exact (2024-01-02, 2024-01-03) interval with series_a.
    bars_b = [
        {"observed_on": "2024-01-02", "close": "50", "adjusted_close": "50"},
        {"observed_on": "2024-01-03", "close": "55", "adjusted_close": "55"},
        {"observed_on": "2024-01-04", "close": "60", "adjusted_close": "60.5"},
    ]
    series_b = _return_series(bars_b)
    aligned_a, aligned_b = _align_series(series_a, series_b)
    assert aligned_a == [Decimal("0.1")]
    assert aligned_b == [(Decimal("55") - Decimal("50")) / Decimal("50")]


def test_return_series_unavailable_when_adjusted_close_missing_for_any_bar():
    """Mixing adjusted and raw close within a single series would introduce
    artificial jumps around corporate actions; a series with any bar missing
    adjusted_close must be treated as wholly unusable, never patched with a
    per-bar fallback to raw close."""
    from assistant_api.finance.api import _return_series

    bars = [
        {"observed_on": "2024-01-01", "close": "100", "adjusted_close": "100"},
        {"observed_on": "2024-01-02", "close": "110", "adjusted_close": None},  # missing for this one bar
        {"observed_on": "2024-01-03", "close": "121", "adjusted_close": "110"},
    ]
    assert _return_series(bars) is None


def test_portfolio_correlation_unavailable_without_consistent_adjusted_close(finance_client):
    """End-to-end: inconsistent adjusted_close in recorded bars must surface as
    available:false, not a silently-computed (and potentially wrong) number."""
    client = finance_client
    portfolio_id = client.post("/api/finance/portfolios", headers=AUTH, json={"name": "Main"}).json()["id"]
    store = client.app.state.finance_store
    for i, (close, adjusted) in enumerate([("100", "100"), ("110", None), ("121", "110")]):
        day = f"2024-01-0{i + 1}"
        store.save_price_bar("BHP.AX", day, "AUD", close, adjusted, None, "2024-01-05T00:00:00Z")
        store.save_price_bar("RIO.AX", day, "AUD", close, close, None, "2024-01-05T00:00:00Z")
    correlation = client.get(
        f"/api/finance/portfolios/{portfolio_id}/correlation?symbol_a=BHP.AX&symbol_b=RIO.AX", headers=AUTH,
    ).json()
    assert correlation["available"] is False
    assert "adjusted_close" in correlation["reason"]


def test_align_series_rejects_mismatched_intervals_despite_shared_end_date():
    """A 2-day return (gap day skipped) and a 1-day return that happen to
    share the same end date are different economic intervals and must never
    be aligned as if comparable."""
    from assistant_api.finance.api import _align_series

    # series_a has a gap on 2024-01-02, so its only return spans 01-01 -> 01-03.
    series_a = [("2024-01-01", "2024-01-03", Decimal("0.21"))]
    # series_b has no gap: a one-day return ending on the same date, 01-02 -> 01-03.
    series_b = [("2024-01-02", "2024-01-03", Decimal("0.05"))]
    aligned_a, aligned_b = _align_series(series_a, series_b)
    assert aligned_a == []
    assert aligned_b == []


def test_portfolio_correlation_window_bounds(finance_client):
    client = finance_client
    portfolio_id = client.post("/api/finance/portfolios", headers=AUTH, json={"name": "Main"}).json()["id"]
    too_small = client.get(
        f"/api/finance/portfolios/{portfolio_id}/correlation?symbol_a=BHP.AX&symbol_b=RIO.AX&window=20", headers=AUTH,
    )
    assert too_small.status_code == 422
    too_large = client.get(
        f"/api/finance/portfolios/{portfolio_id}/correlation?symbol_a=BHP.AX&symbol_b=RIO.AX&window=366", headers=AUTH,
    )
    assert too_large.status_code == 422


def test_alert_evaluation_is_on_demand_and_deterministic(finance_client):
    from datetime import date, timedelta
    client = finance_client
    _create_bhp(client)
    soon = (date.today() + timedelta(days=3)).isoformat()
    client.post("/api/finance/securities/BHP.AX/catalysts", headers=AUTH, json={
        "symbol": "BHP.AX", "title": "Quarterly production report", "event_date": soon,
    })
    evaluated = client.post("/api/finance/alerts/evaluate", headers=AUTH)
    assert evaluated.status_code == 200
    created = evaluated.json()["created"]
    assert any(item["kind"] == "material" for item in created)
    alerts = client.get("/api/finance/alerts", headers=AUTH).json()["alerts"]
    assert any(a["kind"] == "material" for a in alerts)
    # Re-running must not duplicate the same alert (add_alert is idempotent per unique key).
    evaluated_again = client.post("/api/finance/alerts/evaluate", headers=AUTH)
    assert evaluated_again.json()["created"] == []


def test_evidence_pdf_rejects_totally_failed_extraction(finance_client):
    client = finance_client
    _create_bhp(client)
    not_a_pdf = b"this is not a pdf file at all"
    response = client.post(
        "/api/finance/securities/BHP.AX/evidence/pdf?title=FY24&publisher=BHP"
        "&source_type=annual_report&document_type=annual_report",
        headers=AUTH, files={"file": ("fake.pdf", not_a_pdf, "application/pdf")},
    )
    assert response.status_code == 422


def test_chat_links_finance_evidence_via_entity_symbol(finance_client):
    client = finance_client
    _create_bhp(client)
    client.post("/api/finance/securities/BHP.AX/evidence", headers=AUTH, json={
        "symbol": "BHP.AX", "title": "FY24", "publisher": "BHP", "source_type": "annual_report",
        "document_type": "annual_report", "text": "Revenue grew 5% to $50bn.",
    })
    response = client.post("/api/chat", json={"question": "Summarize BHP", "entity_symbol": "bhp.ax"})
    assert response.status_code == 200
    sources = response.json()["sources"]
    assert any(s["id"].startswith("finance_evidence:") for s in sources)
