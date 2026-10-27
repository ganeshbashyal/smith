"""Deterministic Decimal financial calculators.

No network access, no LLM involvement and no silent defaults: every function
either returns an exact Decimal-backed result with the inputs/formula it used,
or raises ValueError / marks a ratio "not meaningful" with an explicit reason.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
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
    margin_of_safety_reason: str | None


def compute_dcf_report(scenarios: list[dict], current_price: str | None,
                       current_price_date: str | None = None) -> DCFReport:
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
    margin_of_safety_reason = None
    if current_price is None:
        margin_of_safety_reason = "No current_price (with current_price_date) was supplied"
    elif weighted is None:
        margin_of_safety_reason = "No probability-weighted fair value is available: " + (reason or "unknown")
    elif weighted <= 0:
        margin_of_safety_reason = "Probability-weighted fair value is not positive; margin of safety is not meaningful"
    else:
        price = Decimal(current_price)
        if price <= 0:
            margin_of_safety_reason = "current_price must be positive"
        else:
            mos = (weighted - price) / weighted
            margin_of_safety = {
                "formula": "(fair_value - price) / fair_value",
                "fair_value": str(weighted),
                "price": str(price),
                "price_date": current_price_date,
                "margin_of_safety": str(mos),
                "note": "Analytical measure of discount to a user-approved valuation; not guaranteed downside protection.",
            }
    return DCFReport(
        scenarios=results,
        probability_weighted_value_per_share=str(weighted) if weighted is not None else None,
        probability_weighted_reason=reason,
        margin_of_safety=margin_of_safety,
        margin_of_safety_reason=margin_of_safety_reason,
    )


def compute_dcf_sensitivity(base: dict, discount_rate_deltas: list[str],
                            terminal_growth_deltas: list[str]) -> dict:
    """Deterministic value-per-share grid varying discount_rate and
    terminal_growth_rate around a single base scenario's own rates.

    Combinations where discount_rate would fall at or below terminal_growth_rate
    are reported as explicitly not meaningful rather than omitted silently."""
    base_r = Decimal(base["discount_rate"])
    base_g = Decimal(base["terminal_growth_rate"])
    rows = []
    for rd in discount_rate_deltas:
        r = base_r + Decimal(rd)
        row = {"discount_rate": str(r), "cells": []}
        for gd in terminal_growth_deltas:
            g = base_g + Decimal(gd)
            cell = {"terminal_growth_rate": str(g)}
            if r <= g:
                cell["value_per_share"] = None
                cell["not_meaningful"] = "discount_rate must be greater than terminal_growth_rate"
            else:
                try:
                    result = compute_dcf_scenario(
                        base["name"], base["projected_fcff"], str(r), str(g),
                        base["net_debt"], base["shares_diluted"],
                    )
                    cell["value_per_share"] = result.value_per_share
                    cell["not_meaningful"] = None
                except ValueError as exc:
                    cell["value_per_share"] = None
                    cell["not_meaningful"] = str(exc)
            row["cells"].append(cell)
        rows.append(row)
    return {
        "base_discount_rate": str(base_r),
        "base_terminal_growth_rate": str(base_g),
        "rows": rows,
        "note": "Deterministic recomputation of the same scenario's formula at alternate "
                "rates; not a prediction or recommendation.",
    }


def calculate_trade_risk(inputs: dict) -> dict:
    """Return a position-risk estimate or explicitly withhold it as unavailable.

    ``pip_or_point_value`` is the quote-currency value of a one-price-unit move
    per contract. ``contract_size`` is underlying units represented per
    contract. Currency conversion is deliberately not performed.
    """
    required = (
        "account_equity", "risk_percent", "entry_price", "stop_price",
        "pip_or_point_value", "contract_size",
    )
    missing = [name for name in required if inputs.get(name) in (None, "")]
    if missing:
        return {
            "status": "unavailable",
            "reason": "Required risk inputs are missing; no position size was estimated.",
            "missing_inputs": missing,
            "maximum_loss": None,
            "risk_distance": None,
            "position_size_contracts": None,
            "position_size_units": None,
            "risk_reward": None,
        }

    try:
        equity, risk_pct, entry, stop, point_value, contract_size = (
            Decimal(str(inputs[field])) for field in required
        )
        if any(not value.is_finite() for value in (equity, risk_pct, entry, stop, point_value, contract_size)):
            raise ValueError("Risk inputs must be finite numbers")
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("Risk inputs must be valid finite decimal numbers") from exc
    if equity <= 0 or entry <= 0 or stop <= 0 or point_value <= 0 or contract_size <= 0:
        raise ValueError("Equity, prices, point value and contract size must be positive")
    if risk_pct <= 0 or risk_pct > 100:
        raise ValueError("risk_percent must be greater than 0 and at most 100")

    account_currency = str(inputs.get("account_currency") or "USD").upper()
    quote_currency = str(inputs.get("quote_currency") or "USD").upper()
    if account_currency != quote_currency:
        return {
            "status": "unavailable",
            "reason": (
                f"Account currency {account_currency} differs from quote currency {quote_currency}; "
                "no FX conversion is applied."
            ),
            "missing_inputs": [],
            "maximum_loss": None,
            "risk_distance": str(abs(entry - stop)),
            "position_size_contracts": None,
            "position_size_units": None,
            "risk_reward": None,
        }

    distance = abs(entry - stop)
    if distance == 0:
        raise ValueError("entry_price and stop_price must differ")
    maximum_loss = equity * risk_pct / Decimal(100)
    risk_per_contract = distance * point_value
    contracts = maximum_loss / risk_per_contract
    target_raw = inputs.get("target_price")
    risk_reward = None
    if target_raw not in (None, ""):
        target = Decimal(str(target_raw))
        if not target.is_finite() or target <= 0:
            raise ValueError("target_price must be a positive finite number")
        risk_reward = str(abs(target - entry) / distance)
    return {
        "status": "ok",
        "reason": None,
        "missing_inputs": [],
        "maximum_loss": str(maximum_loss),
        "risk_distance": str(distance),
        "position_size_contracts": str(contracts),
        "position_size_units": str(contracts * contract_size),
        "risk_reward": risk_reward,
        "currency": account_currency,
        "assumption": (
            "Point value is quote-currency value per one price-unit move per contract; "
            "contract size converts contracts to underlying units. Fees, slippage and FX are excluded."
        ),
    }


def analyze_ohlc(bars: list[dict], *, window: int = 20) -> dict:
    """Compute compact deterministic OHLC indicators; input bars are oldest first."""
    if not bars:
        return {"status": "unavailable", "reason": "No cached bars are available."}
    try:
        normalized = [
            {
                **bar,
                "open_d": Decimal(str(bar["open"])),
                "high_d": Decimal(str(bar["high"])),
                "low_d": Decimal(str(bar["low"])),
                "close_d": Decimal(str(bar["close"])),
            }
            for bar in bars
        ]
        if any(
            not value.is_finite()
            for bar in normalized
            for value in (bar["open_d"], bar["high_d"], bar["low_d"], bar["close_d"])
        ):
            raise ValueError("OHLC values must be finite")
        if any(
            bar["high_d"] < max(bar["open_d"], bar["close_d"], bar["low_d"])
            or bar["low_d"] > min(bar["open_d"], bar["close_d"], bar["high_d"])
            for bar in normalized
        ):
            raise ValueError("OHLC bar has inconsistent high/low values")
    except (KeyError, InvalidOperation, TypeError) as exc:
        raise ValueError("Bars must include valid finite OHLC values") from exc

    closes = [bar["close_d"] for bar in normalized]
    latest = normalized[-1]
    previous_close = closes[-2] if len(closes) > 1 else None
    change_pct = (
        str((closes[-1] - previous_close) / previous_close * 100)
        if previous_close not in (None, Decimal(0)) else None
    )
    returns = [
        (current - previous) / previous
        for previous, current in zip(closes, closes[1:])
        if previous != 0
    ]
    volatility_pct = None
    if len(returns) >= 2:
        mean = sum(returns) / Decimal(len(returns))
        variance = sum((value - mean) ** 2 for value in returns) / Decimal(len(returns) - 1)
        volatility_pct = str(variance.sqrt() * Decimal(100))

    true_ranges = []
    for index, bar in enumerate(normalized):
        prior = normalized[index - 1]["close_d"] if index else bar["close_d"]
        true_ranges.append(max(
            bar["high_d"] - bar["low_d"],
            abs(bar["high_d"] - prior),
            abs(bar["low_d"] - prior),
        ))
    atr_window = min(14, len(true_ranges))
    atr = sum(true_ranges[-atr_window:]) / Decimal(atr_window) if atr_window else None
    sma20 = sum(closes[-min(20, len(closes)):]) / Decimal(min(20, len(closes)))
    sma50 = (
        sum(closes[-50:]) / Decimal(50)
        if len(closes) >= 50 else None
    )
    recent = normalized[-min(window, len(normalized)):]
    peak = max(bar["high_d"] for bar in recent)
    drawdown_pct = (closes[-1] - peak) / peak * 100 if peak else Decimal(0)
    if sma50 is None:
        trend = "insufficient_history"
    elif closes[-1] > sma20 > sma50:
        trend = "uptrend"
    elif closes[-1] < sma20 < sma50:
        trend = "downtrend"
    else:
        trend = "mixed"

    utc_day = str(latest.get("observed_at", ""))[:10]
    session = [
        bar for bar in normalized
        if str(bar.get("observed_at", ""))[:10] == utc_day
    ]
    return {
        "status": "ok",
        "instrument": latest.get("instrument"),
        "observed_at": latest.get("observed_at"),
        "source": latest.get("source"),
        "currency": latest.get("currency"),
        "timeframe": latest.get("timeframe"),
        "freshness": latest.get("freshness"),
        "return_pct": change_pct,
        "atr": str(atr) if atr is not None else None,
        "realized_volatility_pct": volatility_pct,
        "sma20": str(sma20),
        "sma50": str(sma50) if sma50 is not None else None,
        "drawdown_from_window_high_pct": str(drawdown_pct),
        "trend_state": trend,
        "support_candidate": str(min(bar["low_d"] for bar in recent)),
        "resistance_candidate": str(max(bar["high_d"] for bar in recent)),
        "utc_session_high": str(max(bar["high_d"] for bar in session)),
        "utc_session_low": str(min(bar["low_d"] for bar in session)),
        "calculation_note": (
            "Indicators are deterministic calculations from supplied OHLC bars, not forecasts. "
            "Support/resistance are window extremes, not validated levels; session uses UTC dates."
        ),
    }


def calculate_daily_level_scenarios(
    daily_bars: list[dict],
    intraday_bars: list[dict] | None = None,
    *,
    session_date: str | None = None,
) -> dict:
    """Derive prior-session pivots and conditional levels from timestamped OHLC.

    Levels are mechanical references, not forecasts or personalized trade
    recommendations. The caller supplies bars oldest first.
    """
    intraday_bars = intraday_bars or []
    if len(daily_bars) < 2:
        return {
            "status": "unavailable",
            "reason": "At least two daily bars are required to identify a prior completed session.",
            "entry_scenarios": [],
        }

    try:
        normalized = []
        for bar in daily_bars:
            values = {
                name: Decimal(str(bar[name]))
                for name in ("open", "high", "low", "close")
            }
            if any(not value.is_finite() or value <= 0 for value in values.values()):
                raise ValueError("Daily OHLC values must be finite and positive")
            if values["high"] < max(values["open"], values["close"], values["low"]):
                raise ValueError("Daily bar high is inconsistent")
            if values["low"] > min(values["open"], values["close"], values["high"]):
                raise ValueError("Daily bar low is inconsistent")
            normalized.append({**bar, **values})

        current_date = session_date or datetime.now(timezone.utc).date().isoformat()
        has_in_progress_bar = str(normalized[-1].get("observed_at", ""))[:10] >= current_date
        completed = normalized[:-1] if has_in_progress_bar else normalized
        if not completed:
            return {
                "status": "unavailable",
                "reason": "No completed daily bar is available to calculate today's levels.",
                "entry_scenarios": [],
            }
        reference = completed[-1]
        high, low, close = reference["high"], reference["low"], reference["close"]
        pivot = (high + low + close) / Decimal(3)
        range_ = high - low
        r1 = 2 * pivot - low
        s1 = 2 * pivot - high
        r2 = pivot + range_
        s2 = pivot - range_

        ranges = []
        for index, bar in enumerate(completed):
            previous_close = completed[index - 1]["close"] if index else bar["close"]
            ranges.append(max(
                bar["high"] - bar["low"],
                abs(bar["high"] - previous_close),
                abs(bar["low"] - previous_close),
            ))
        atr_period = min(14, len(ranges))
        atr = sum(ranges[-atr_period:]) / Decimal(atr_period)
        buffer = atr * Decimal("0.1")
        current_session = [
            bar for bar in intraday_bars
            if str(bar.get("observed_at", ""))[:10] == current_date
        ]
        session_high = max(
            (Decimal(str(bar["high"])) for bar in current_session), default=None,
        )
        session_low = min(
            (Decimal(str(bar["low"])) for bar in current_session), default=None,
        )
        fmt = lambda value: str(value.quantize(TWOPLACES))
        fresh = reference.get("freshness") == "fresh"
        scenarios = [
            {
                "scenario": "pullback_confirmation",
                "entry_zone": [fmt(pivot - buffer), fmt(pivot + buffer)],
                "targets": [fmt(r1), fmt(r2)],
                "invalidation": fmt(s1 - buffer),
                "condition": "Only consider after price holds the pivot zone and confirms a higher low.",
            },
            {
                "scenario": "breakout_confirmation",
                "entry_trigger": fmt(r1 + buffer),
                "targets": [fmt(r2)],
                "invalidation": fmt(pivot),
                "condition": "Only consider after a completed-bar close above R1; avoid chasing an unconfirmed spike.",
            },
            {
                "scenario": "downside_break_confirmation",
                "entry_trigger": fmt(s1 - buffer),
                "targets": [fmt(s2)],
                "invalidation": fmt(pivot),
                "condition": "Bear-case observation only; require a completed-bar close below S1.",
            },
        ] if fresh else []
        return {
            "status": "ok" if fresh else "stale",
            "instrument": reference.get("instrument"),
            "session_date": current_date,
            "reference_session": reference.get("observed_at"),
            "source": reference.get("source"),
            "freshness": reference.get("freshness"),
            "currency": reference.get("currency"),
            "reference_ohlc": {
                "high": fmt(high), "low": fmt(low), "close": fmt(close),
            },
            "atr14": fmt(atr),
            "pivot_levels": {
                "pivot": fmt(pivot), "R1": fmt(r1), "S1": fmt(s1),
                "R2": fmt(r2), "S2": fmt(s2),
            },
            "session_range": {
                "timezone": "UTC",
                "high": fmt(session_high) if session_high is not None else None,
                "low": fmt(session_low) if session_low is not None else None,
                "status": "available" if current_session else "unavailable",
            },
            "entry_scenarios": scenarios,
            "method": (
                "Classic pivots from the latest cached daily OHLC bar; ATR is the mean "
                "true range over up to 14 cached daily bars; buffer is 0.1 ATR. "
                "These are conditional technical reference levels, not fair value, "
                "a prediction, a personalized recommendation, or a trade signal."
            ),
        }
    except (InvalidOperation, KeyError, TypeError) as exc:
        raise ValueError("Daily and intraday bars must contain valid finite OHLC values") from exc
