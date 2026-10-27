from decimal import Decimal

import pytest

from assistant_api.finance.calculators import (
    analyze_ohlc, calculate_daily_level_scenarios, calculate_trade_risk,
    compute_dcf_report, compute_dcf_scenario, compute_ratios,
)


def test_ratios_full_inputs():
    result = compute_ratios({
        "price": "10", "shares_diluted": "100", "cash_and_equivalents": "50", "total_debt": "200",
        "revenue": "1000", "ebitda": "300", "net_income": "100", "operating_cash_flow": "150",
        "capital_expenditure": "40", "dividends_paid": "20",
    })
    assert result.values["market_cap"] == "1000"
    assert result.values["net_debt"] == "150"
    assert result.values["enterprise_value"] == "1150"
    assert result.values["pe"] == "10"
    assert result.values["fcf"] == "110"
    assert result.values["dividend_yield"] == "0.02"
    assert result.values["cash_conversion"] == "1.5"
    assert not result.not_meaningful


def test_ratios_missing_and_non_meaningful_inputs():
    result = compute_ratios({"price": "10", "shares_diluted": "100", "net_income": "-5"})
    assert "market_cap" in result.values
    assert "net_debt" in result.not_meaningful
    assert result.not_meaningful["pe"] == "Net income is zero or negative; P/E is not meaningful"
    assert "fcf" in result.not_meaningful
    assert "dividend_yield" in result.not_meaningful


def test_dcf_scenario_requires_discount_above_growth():
    with pytest.raises(ValueError):
        compute_dcf_scenario("base", ["100", "110"], "0.05", "0.05", "0", "10")


def test_dcf_scenario_computes_value_per_share():
    result = compute_dcf_scenario("base", ["100", "110", "120"], "0.10", "0.02", "50", "10", probability="1")
    assert Decimal(result.value_per_share) > 0
    assert result.probability == "1"


def test_dcf_report_weights_scenarios_and_withholds_on_bad_probabilities():
    scenarios = [
        {"name": "bull", "projected_fcff": ["100", "120"], "discount_rate": "0.10",
         "terminal_growth_rate": "0.02", "net_debt": "0", "shares_diluted": "10", "probability": "0.6"},
        {"name": "bear", "projected_fcff": ["80", "90"], "discount_rate": "0.12",
         "terminal_growth_rate": "0.01", "net_debt": "0", "shares_diluted": "10", "probability": "0.4"},
    ]
    report = compute_dcf_report(scenarios, current_price="50")
    assert report.probability_weighted_value_per_share is not None
    assert report.margin_of_safety is not None
    assert report.margin_of_safety["formula"] == "(fair_value - price) / fair_value"

    bad_scenarios = [{**scenarios[0], "probability": "0.5"}, {**scenarios[1], "probability": "0.9"}]
    report2 = compute_dcf_report(bad_scenarios, current_price="50")
    assert report2.probability_weighted_value_per_share is None
    assert "not 1" in report2.probability_weighted_reason


def test_dcf_report_without_any_probability_has_no_expected_value():
    scenarios = [{"name": "base", "projected_fcff": ["100"], "discount_rate": "0.1",
                 "terminal_growth_rate": "0.02", "net_debt": "0", "shares_diluted": "10"}]
    report = compute_dcf_report(scenarios, current_price=None)
    assert report.probability_weighted_value_per_share is None
    assert report.margin_of_safety is None


def test_trade_risk_withholds_position_size_for_missing_inputs():
    result = calculate_trade_risk({"account_equity": "10000", "risk_percent": "1"})
    assert result["status"] == "unavailable"
    assert result["position_size_contracts"] is None
    assert {"entry_price", "stop_price", "pip_or_point_value", "contract_size"} <= set(
        result["missing_inputs"]
    )


def test_trade_risk_calculates_only_with_complete_same_currency_inputs():
    result = calculate_trade_risk({
        "account_equity": "10000", "risk_percent": "1",
        "entry_price": "2400", "stop_price": "2390",
        "pip_or_point_value": "100", "contract_size": "100",
        "target_price": "2420", "account_currency": "USD",
    })
    assert result["status"] == "ok"
    assert result["maximum_loss"] == "100"
    assert result["risk_distance"] == "10"
    assert result["position_size_contracts"] == "0.1"
    assert result["position_size_units"] == "10.0"
    assert result["risk_reward"] == "2"


def test_trade_risk_withholds_size_when_fx_conversion_would_be_required():
    result = calculate_trade_risk({
        "account_equity": "10000", "risk_percent": "1",
        "entry_price": "2400", "stop_price": "2390",
        "pip_or_point_value": "100", "contract_size": "100",
        "account_currency": "AUD", "quote_currency": "USD",
    })
    assert result["status"] == "unavailable"
    assert result["position_size_contracts"] is None
    assert "no FX conversion" in result["reason"]


def test_ohlc_analysis_is_local_and_reports_insufficient_trend_history():
    bars = [
        {
            "instrument": "XAUUSD=X", "observed_at": f"2026-01-01T00:{i:02d}:00+00:00",
            "source": "test", "currency": "USD", "timeframe": "1m",
            "freshness": "fresh", "open": str(100 + i), "high": str(102 + i),
            "low": str(99 + i), "close": str(101 + i),
        }
        for i in range(3)
    ]
    result = analyze_ohlc(bars)
    assert result["status"] == "ok"
    assert result["return_pct"] == str((Decimal(103) - Decimal(102)) / Decimal(102) * 100)
    assert result["atr"] is not None
    assert result["sma50"] is None
    assert result["trend_state"] == "insufficient_history"
    assert result["utc_session_high"] == "104"


def test_daily_level_scenarios_use_ohlc_and_label_conditional_entries():
    bars = [
        {"instrument": "XAUUSD=X", "observed_at": "2026-10-25T00:00:00+00:00",
         "source": "Mock", "currency": "USD", "freshness": "fresh",
         "open": "95", "high": "105", "low": "85", "close": "100"},
        {"instrument": "XAUUSD=X", "observed_at": "2026-10-26T00:00:00+00:00",
         "source": "Mock", "currency": "USD", "freshness": "fresh",
         "open": "100", "high": "110", "low": "90", "close": "100"},
    ]
    result = calculate_daily_level_scenarios(bars, session_date="2026-10-27")
    assert result["status"] == "ok"
    assert result["pivot_levels"] == {
        "pivot": "100.00", "R1": "110.00", "S1": "90.00",
        "R2": "120.00", "S2": "80.00",
    }
    assert result["atr14"] == "20.00"
    assert result["entry_scenarios"][0]["entry_zone"] == ["98.00", "102.00"]
    assert "not fair value" in result["method"]


def test_daily_level_scenarios_withhold_when_history_is_insufficient():
    assert calculate_daily_level_scenarios([])["status"] == "unavailable"
