from decimal import Decimal

import pytest

from assistant_api.finance.calculators import compute_dcf_report, compute_dcf_scenario, compute_ratios


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
