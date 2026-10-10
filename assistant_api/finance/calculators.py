"""Deterministic Decimal financial calculators.

No network access, no LLM involvement and no silent defaults: every function
either returns an exact Decimal-backed result with the inputs/formula it used,
or raises ValueError / marks a ratio "not meaningful" with an explicit reason.
"""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

TWOPLACES = Decimal("0.01")


def _d(value) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"Invalid decimal input: {value!r}") from exc


@dataclass
class RatioResult:
    values: dict[str, str | None] = field(default_factory=dict)
    not_meaningful: dict[str, str] = field(default_factory=dict)
    formulas: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"values": self.values, "not_meaningful": self.not_meaningful, "formulas": self.formulas}


def compute_ratios(inputs: dict) -> RatioResult:
    price = _d(inputs.get("price"))
    shares = _d(inputs.get("shares_diluted"))
    cash = _d(inputs.get("cash_and_equivalents"))
    debt = _d(inputs.get("total_debt"))
    revenue = _d(inputs.get("revenue"))
    ebitda = _d(inputs.get("ebitda"))
    net_income = _d(inputs.get("net_income"))
    ocf = _d(inputs.get("operating_cash_flow"))
    capex = _d(inputs.get("capital_expenditure"))
    dividends = _d(inputs.get("dividends_paid"))

    result = RatioResult()
    result.formulas["market_cap"] = "price * shares_diluted"
    result.formulas["net_debt"] = "total_debt - cash_and_equivalents"
    result.formulas["enterprise_value"] = "market_cap + net_debt"
    result.formulas["pe"] = "market_cap / net_income"
    result.formulas["ev_ebitda"] = "enterprise_value / ebitda"
    result.formulas["fcf"] = "operating_cash_flow - capital_expenditure"
    result.formulas["fcf_yield"] = "fcf / market_cap"
    result.formulas["dividend_yield"] = "dividends_paid / market_cap"
    result.formulas["cash_conversion"] = "operating_cash_flow / net_income"

    market_cap = price * shares if price is not None and shares is not None else None
    if price is None or shares is None:
        result.not_meaningful["market_cap"] = "Missing price or diluted share count"
    else:
        result.values["market_cap"] = str(market_cap)

    net_debt = None
    if debt is not None and cash is not None:
        net_debt = debt - cash
        result.values["net_debt"] = str(net_debt)
    else:
        result.not_meaningful["net_debt"] = "Missing total debt or cash and equivalents"

    enterprise_value = None
    if market_cap is not None and net_debt is not None:
        enterprise_value = market_cap + net_debt
        result.values["enterprise_value"] = str(enterprise_value)
    else:
        result.not_meaningful["enterprise_value"] = "Requires market cap and net debt"

    if market_cap is None:
        result.not_meaningful["pe"] = "Missing market cap"
    elif net_income is None:
        result.not_meaningful["pe"] = "Missing net income"
    elif net_income <= 0:
        result.not_meaningful["pe"] = "Net income is zero or negative; P/E is not meaningful"
    else:
        result.values["pe"] = str(market_cap / net_income)

    if enterprise_value is None:
        result.not_meaningful["ev_ebitda"] = "Missing enterprise value"
    elif ebitda is None:
        result.not_meaningful["ev_ebitda"] = "Missing EBITDA"
    elif ebitda <= 0:
        result.not_meaningful["ev_ebitda"] = "EBITDA is zero or negative; EV/EBITDA is not meaningful"
    else:
        result.values["ev_ebitda"] = str(enterprise_value / ebitda)

    fcf = None
    if ocf is not None and capex is not None:
        fcf = ocf - capex
        result.values["fcf"] = str(fcf)
    else:
        result.not_meaningful["fcf"] = "Missing operating cash flow or capital expenditure"

    if fcf is None:
        result.not_meaningful["fcf_yield"] = "Missing free cash flow"
    elif market_cap is None:
        result.not_meaningful["fcf_yield"] = "Missing market cap"
    elif market_cap <= 0:
        result.not_meaningful["fcf_yield"] = "Market cap is zero or negative"
    else:
        result.values["fcf_yield"] = str(fcf / market_cap)

    if dividends is None:
        result.not_meaningful["dividend_yield"] = "Missing dividends paid"
    elif market_cap is None:
        result.not_meaningful["dividend_yield"] = "Missing market cap"
    elif market_cap <= 0:
        result.not_meaningful["dividend_yield"] = "Market cap is zero or negative"
    else:
        result.values["dividend_yield"] = str(abs(dividends) / market_cap)

    if ocf is None:
        result.not_meaningful["cash_conversion"] = "Missing operating cash flow"
    elif net_income is None:
        result.not_meaningful["cash_conversion"] = "Missing net income"
    elif net_income <= 0:
        result.not_meaningful["cash_conversion"] = "Net income is zero or negative; conversion is not meaningful"
    else:
        result.values["cash_conversion"] = str(ocf / net_income)

    return result


@dataclass
class DCFScenarioResult:
    name: str
    present_value_fcff: str
    enterprise_value: str
    equity_value: str
    value_per_share: str
    probability: str | None


def compute_dcf_scenario(name: str, projected_fcff: list[str], discount_rate: str,
                         terminal_growth_rate: str, net_debt: str, shares_diluted: str,
                         probability: str | None = None) -> DCFScenarioResult:
    r = Decimal(discount_rate)
    g = Decimal(terminal_growth_rate)
    if r <= g:
        raise ValueError("Discount rate must be greater than the terminal growth rate")
    nd = Decimal(net_debt)
    shares = Decimal(shares_diluted)
    if shares <= 0:
        raise ValueError("Diluted share count must be positive")
    flows = [Decimal(value) for value in projected_fcff]
    if not flows:
        raise ValueError("At least one projected FCFF period is required")

    pv_explicit = Decimal(0)
    for year, flow in enumerate(flows, start=1):
        pv_explicit += flow / ((1 + r) ** year)
    terminal_value = flows[-1] * (1 + g) / (r - g)
    pv_terminal = terminal_value / ((1 + r) ** len(flows))
    enterprise_value = pv_explicit + pv_terminal
    equity_value = enterprise_value - nd
    value_per_share = equity_value / shares
    return DCFScenarioResult(
        name=name,
        present_value_fcff=str(pv_explicit),
        enterprise_value=str(enterprise_value),
        equity_value=str(equity_value),
        value_per_share=str(value_per_share),
        probability=probability,
    )


@dataclass
class DCFReport:
    scenarios: list[DCFScenarioResult]
    probability_weighted_value_per_share: str | None
    probability_weighted_reason: str | None
    margin_of_safety: dict[str, str] | None


def compute_dcf_report(scenarios: list[dict], current_price: str | None) -> DCFReport:
    results = [
        compute_dcf_scenario(
            s["name"], s["projected_fcff"], s["discount_rate"], s["terminal_growth_rate"],
            s["net_debt"], s["shares_diluted"], s.get("probability"),
        )
        for s in scenarios
    ]
    weighted = None
    reason = None
    probabilities = [Decimal(r.probability) for r in results if r.probability is not None]
    if len(probabilities) == len(results) and results:
        total = sum(probabilities)
        if total != 1:
            reason = f"Scenario probabilities sum to {total}, not 1; expected value withheld"
        else:
            weighted = sum(Decimal(r.value_per_share) * p for r, p in zip(results, probabilities))
    else:
        reason = "Expected value requires every scenario to have a user-supplied probability"

    margin_of_safety = None
    if current_price is not None and weighted is not None:
        price = Decimal(current_price)
        if price <= 0:
            margin_of_safety = None
        else:
            mos = (weighted - price) / weighted if weighted != 0 else Decimal(0)
            margin_of_safety = {
                "formula": "(fair_value - price) / fair_value",
                "fair_value": str(weighted),
                "price": str(price),
                "margin_of_safety": str(mos),
                "note": "Analytical measure of discount to a user-approved valuation; not guaranteed downside protection.",
            }
    return DCFReport(
        scenarios=results,
        probability_weighted_value_per_share=str(weighted) if weighted is not None else None,
        probability_weighted_reason=reason,
        margin_of_safety=margin_of_safety,
    )
