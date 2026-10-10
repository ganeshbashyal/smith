from decimal import Decimal

from assistant_api.finance.portfolio import (
    analyze_portfolio, apply_shocks, pearson_correlation, value_holding,
)


def test_value_holding_cash_manual_and_quoted():
    cash = value_holding({"symbol": "CASH", "units": "1000", "is_cash": True}, {})
    assert cash.market_value_aud == Decimal("1000")
    assert cash.valuation_source == "cash"

    manual = value_holding({"symbol": "FOREIGN", "units": "10", "manual_aud_value": "500"}, {})
    assert manual.market_value_aud == Decimal("500")
    assert manual.valuation_source == "manual_aud_entry"

    quoted = value_holding({"symbol": "BHP", "units": "10"}, {"BHP": {"close": "45.5", "observed_on": "2024-01-01"}})
    assert quoted.market_value_aud == Decimal("455.0")
    assert quoted.valuation_source == "asx_quote"

    missing = value_holding({"symbol": "UNKNOWN", "units": "10"}, {})
    assert missing.market_value_aud is None
    assert missing.valuation_source == "unavailable"


def test_analyze_portfolio_is_coverage_aware():
    holdings = [
        {"symbol": "BHP", "units": "10", "sector": "Materials"},
        {"symbol": "UNKNOWN", "units": "5"},
    ]
    prices = {"BHP": {"close": "40", "observed_on": "2024-01-01"}}
    valuations = [value_holding(h, prices) for h in holdings]
    analysis = analyze_portfolio(valuations)
    assert analysis.coverage_count == 1
    assert analysis.total_count == 2
    assert "UNKNOWN" in analysis.missing_valuations
    assert analysis.weights["BHP"] == "1"
    assert analysis.sector_concentration["Materials"] == "1"


def test_analyze_portfolio_never_renormalizes_incomplete_coverage():
    """Defect 1 regression: total_value_aud must be None (not covered subtotal)
    whenever any holding is missing a valuation; covered_value_aud is the
    explicit, separately labeled subtotal basis for weights."""
    holdings = [
        {"symbol": "BHP", "units": "10", "sector": "Materials"},
        {"symbol": "UNKNOWN", "units": "5"},
    ]
    prices = {"BHP": {"close": "40", "observed_on": "2024-01-01"}}
    valuations = [value_holding(h, prices) for h in holdings]
    analysis = analyze_portfolio(valuations)
    assert analysis.coverage_complete is False
    assert analysis.total_value_aud is None
    assert analysis.covered_value_aud == "400"
    assert analysis.coverage_pct == "50.0"

    full_holdings = [{"symbol": "BHP", "units": "10", "sector": "Materials"}]
    full_valuations = [value_holding(h, prices) for h in full_holdings]
    full_analysis = analyze_portfolio(full_valuations)
    assert full_analysis.coverage_complete is True
    assert full_analysis.total_value_aud == "400"
    assert full_analysis.covered_value_aud == "400"


def test_analyze_portfolio_preserves_zero_valued_holdings():
    """A zero-market-value holding is covered (dated/valued), not missing."""
    holdings = [{"symbol": "WORTHLESS", "units": "100", "sector": "Materials",
                "manual_aud_value": "0", "valuation_date": "2024-01-01"}]
    valuations = [value_holding(h, {}) for h in holdings]
    assert valuations[0].market_value_aud == Decimal("0")
    analysis = analyze_portfolio(valuations)
    assert analysis.coverage_complete is True
    assert analysis.coverage_count == 1
    assert "WORTHLESS" not in analysis.missing_valuations


def test_apply_shocks_first_match_wins_and_reports_unresolved():
    holdings = [
        {"symbol": "BHP", "units": "10", "sector": "Materials"},
        {"symbol": "UNKNOWN", "units": "5"},
    ]
    prices = {"BHP": {"close": "40", "observed_on": "2024-01-01"}}
    valuations = [value_holding(h, prices) for h in holdings]
    shocks = [
        {"label": "Materials down", "symbol_or_group": "Materials", "pct_change": "-10"},
        {"label": "BHP specific", "symbol_or_group": "BHP", "pct_change": "-50"},
    ]
    result = apply_shocks(valuations, shocks)
    assert result["applied_shocks"][0]["shock"] == "Materials down"
    assert "UNKNOWN" in result["unresolved_symbols"]
    assert Decimal(result["shocked_total_aud"]) == Decimal("360")


def test_pearson_correlation_requires_minimum_observations():
    short_series = [Decimal(x) for x in range(5)]
    result = pearson_correlation(short_series, short_series, minimum_points=20)
    assert result["available"] is False

    long_a = [Decimal(x) for x in range(25)]
    long_b = [Decimal(x) * 2 for x in range(25)]
    result2 = pearson_correlation(long_a, long_b, minimum_points=20)
    assert result2["available"] is True
    assert Decimal(result2["correlation"]) == Decimal(1)
