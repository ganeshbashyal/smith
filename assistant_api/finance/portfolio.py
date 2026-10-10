"""Deterministic long-only portfolio analytics.

Coverage-aware: any holding without a known, dated market value is reported
as missing rather than silently excluded or renormalized away. A portfolio's
true total value is only reported when every holding has a valuation; weights
and concentration are explicitly labeled as computed against the covered
subtotal otherwise, never silently renormalized to look like a full total.
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

STALE_AFTER_DAYS = 7


def _days_old(valuation_date: str | None, as_of: date | None = None) -> int | None:
    if not valuation_date:
        return None
    try:
        parsed = date.fromisoformat(valuation_date)
    except ValueError:
        return None
    reference = as_of or datetime.now().date()
    return (reference - parsed).days


def _is_future_date(value: str | None) -> bool:
    if not value:
        return False
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return False
    return parsed > datetime.now().date()


@dataclass
class HoldingValuation:
    symbol: str
    units: Decimal
    is_cash: bool
    market_value_aud: Decimal | None
    valuation_date: str | None
    valuation_source: str
    sector: str | None
    currency: str = "AUD"
    cost_basis_total: Decimal | None = None

    @property
    def is_stale(self) -> bool | None:
        age = _days_old(self.valuation_date)
        if age is None:
            return None
        return age > STALE_AFTER_DAYS

    @property
    def unrealized_gain_aud(self) -> Decimal | None:
        if self.market_value_aud is None or self.cost_basis_total is None:
            return None
        return self.market_value_aud - self.cost_basis_total


def value_holding(holding: dict, latest_price_by_symbol: dict[str, dict]) -> HoldingValuation:
    symbol = holding["symbol"]
    units = Decimal(holding["units"])
    is_cash = bool(holding.get("is_cash"))
    cost_basis = Decimal(holding["cost_basis_total"]) if holding.get("cost_basis_total") not in (None, "") else None
    if is_cash:
        return HoldingValuation(symbol, units, True, units, holding.get("valuation_date"), "cash",
                                holding.get("sector"), "AUD", cost_basis)
    manual_value = holding.get("manual_aud_value")
    valuation_date = holding.get("valuation_date")
    if manual_value is not None:
        if _is_future_date(valuation_date):
            # A future-dated valuation is not valid current evidence; do not use it.
            return HoldingValuation(symbol, units, False, None, valuation_date, "unavailable_future_dated",
                                    holding.get("sector"), "AUD", cost_basis)
        return HoldingValuation(
            symbol, units, False, Decimal(manual_value), valuation_date,
            "manual_aud_entry", holding.get("sector"), "AUD", cost_basis,
        )
    bar = latest_price_by_symbol.get(symbol)
    if bar and bar.get("close") is not None:
        bar_currency = bar.get("currency")
        if bar_currency != "AUD":
            # No automatic FX conversion: a non-AUD or unknown-currency quote
            # cannot be auto-valued in AUD without a user-supplied manual value.
            return HoldingValuation(symbol, units, False, None, bar.get("observed_on"),
                                    "unavailable_non_aud_currency", holding.get("sector"),
                                    bar_currency or "unknown", cost_basis)
        value = Decimal(bar["close"]) * units
        return HoldingValuation(symbol, units, False, value, bar.get("observed_on"), "asx_quote",
                                holding.get("sector"), "AUD", cost_basis)
    return HoldingValuation(symbol, units, False, None, None, "unavailable", holding.get("sector"),
                            "AUD", cost_basis)



@dataclass
class PortfolioAnalysis:
    coverage_complete: bool
    total_value_aud: str | None
    """True whole-portfolio total; None unless every holding has a known valuation."""
    covered_value_aud: str | None
    """Subtotal of only the holdings with a known valuation."""
    coverage_count: int
    total_count: int
    coverage_pct: str
    weights: dict[str, str] | None
    """True whole-portfolio weights; None unless coverage_complete (never
    silently substituted with covered-only weights)."""
    covered_weights: dict[str, str]
    """Weights of each covered holding against covered_value_aud — always
    present, but explicitly NOT whole-portfolio weights when coverage is
    incomplete."""
    sector_concentration: dict[str, str] | None
    """True whole-portfolio sector concentration; None unless coverage_complete."""
    covered_sector_concentration: dict[str, str]
    """Sector concentration against covered_value_aud only — always present."""
    largest_positions: list[dict]
    holdings_detail: list[dict]
    """Every holding (not just the largest), each with its own currency,
    valuation_date, valuation_source and staleness so missing/foreign-currency/
    future-dated holdings are visible, not only the top 10."""
    missing_valuations: list[str]
    total_unrealized_gain_aud: str | None
    """True whole-portfolio unrealized gain; None unless coverage_complete and
    every covered holding has a cost basis."""
    covered_unrealized_gain_aud: str | None
    """Unrealized gain summed over covered holdings only; None if any covered
    holding lacks a cost basis, rather than partial-summing."""


def _position_detail(v: "HoldingValuation") -> dict:
    return {
        "symbol": v.symbol,
        "sector": v.sector,
        "market_value_aud": str(v.market_value_aud) if v.market_value_aud is not None else None,
        "valuation_date": v.valuation_date,
        "valuation_source": v.valuation_source,
        "currency": v.currency,
        "is_stale": v.is_stale,
        "unrealized_gain_aud": str(v.unrealized_gain_aud) if v.unrealized_gain_aud is not None else None,
    }


def analyze_portfolio(valuations: list[HoldingValuation]) -> PortfolioAnalysis:
    covered = [v for v in valuations if v.market_value_aud is not None]
    missing = [v.symbol for v in valuations if v.market_value_aud is None]
    covered_total = sum((v.market_value_aud for v in covered), Decimal(0)) if covered else None
    coverage_complete = len(missing) == 0 and len(valuations) > 0

    covered_weights: dict[str, str] = {}
    covered_sector_totals: dict[str, Decimal] = {}
    if covered_total and covered_total > 0:
        for v in covered:
            covered_weights[v.symbol] = str(v.market_value_aud / covered_total)
            sector = v.sector or "Unknown"
            covered_sector_totals[sector] = covered_sector_totals.get(sector, Decimal(0)) + v.market_value_aud
    covered_sector_concentration = {
        sector: str(value / covered_total) for sector, value in covered_sector_totals.items()
    } if covered_total and covered_total > 0 else {}

    # True whole-portfolio figures are only ever populated when every holding
    # resolved to a known value; otherwise they are explicitly withheld rather
    # than silently substituted with the covered-only figures above.
    true_total = covered_total if coverage_complete else None
    weights = covered_weights if coverage_complete else None
    sector_concentration = covered_sector_concentration if coverage_complete else None

    largest = sorted(covered, key=lambda v: v.market_value_aud, reverse=True)[:10]
    covered_gains = [v.unrealized_gain_aud for v in covered]
    covered_unrealized_gain = (
        sum(covered_gains, Decimal(0)) if covered_gains and all(g is not None for g in covered_gains) else None
    )
    total_unrealized_gain = covered_unrealized_gain if coverage_complete else None
    coverage_pct = str((Decimal(len(covered)) / Decimal(len(valuations)) * 100)) if valuations else "0"
    return PortfolioAnalysis(
        coverage_complete=coverage_complete,
        total_value_aud=str(true_total) if true_total is not None else None,
        covered_value_aud=str(covered_total) if covered_total is not None else None,
        coverage_count=len(covered),
        total_count=len(valuations),
        coverage_pct=coverage_pct,
        weights=weights,
        covered_weights=covered_weights,
        sector_concentration=sector_concentration,
        covered_sector_concentration=covered_sector_concentration,
        largest_positions=[_position_detail(v) for v in largest],
        holdings_detail=[_position_detail(v) for v in valuations],
        missing_valuations=missing,
        total_unrealized_gain_aud=str(total_unrealized_gain) if total_unrealized_gain is not None else None,
        covered_unrealized_gain_aud=(
            str(covered_unrealized_gain) if covered_unrealized_gain is not None else None
        ),
    )


def apply_shocks(valuations: list[HoldingValuation], shocks: list[dict]) -> dict:
    """Apply user-defined percentage shocks per symbol or group label.

    Each holding is shocked by at most one matching shock (first match wins,
    in the order supplied) to keep overlap rules explicit and non-additive.
    Whole-portfolio baseline/shocked totals are only reported when every
    holding resolved to a known value; otherwise only the explicitly-labeled
    covered subtotal is reported, never a silently substituted "total".
    """
    covered_baseline_total = Decimal(0)
    covered_shocked_total = Decimal(0)
    applied: list[dict] = []
    unresolved_symbols: list[str] = []
    for v in valuations:
        if v.market_value_aud is None:
            unresolved_symbols.append(v.symbol)
            continue
        covered_baseline_total += v.market_value_aud
        match = next((s for s in shocks if s["symbol_or_group"].upper() == v.symbol.upper()
                     or s["symbol_or_group"].upper() == (v.sector or "").upper()), None)
        if match:
            pct = Decimal(match["pct_change"])
            new_value = v.market_value_aud * (1 + pct / Decimal(100))
            applied.append({"symbol": v.symbol, "shock": match["label"], "pct_change": str(pct)})
        else:
            new_value = v.market_value_aud
        covered_shocked_total += new_value
    coverage_complete = not unresolved_symbols
    return {
        "coverage_complete": coverage_complete,
        "baseline_total_aud": str(covered_baseline_total) if coverage_complete else None,
        "shocked_total_aud": str(covered_shocked_total) if coverage_complete else None,
        "change_aud": str(covered_shocked_total - covered_baseline_total) if coverage_complete else None,
        "covered_baseline_total_aud": str(covered_baseline_total),
        "covered_shocked_total_aud": str(covered_shocked_total),
        "covered_change_aud": str(covered_shocked_total - covered_baseline_total),
        "applied_shocks": applied,
        "unresolved_symbols": unresolved_symbols,
        "assumption_note": "Deterministic user-defined shock scenario, not a prediction.",
    }


def pearson_correlation(series_a: list[Decimal], series_b: list[Decimal], minimum_points: int = 20) -> dict:
    """Correlation of aligned, equal-length return series. Returns unavailable if too short."""
    if len(series_a) != len(series_b):
        return {"available": False, "reason": "Return series are not aligned to the same dates"}
    n = len(series_a)
    if n < minimum_points:
        return {"available": False, "reason": f"Only {n} aligned observations; {minimum_points} required"}
    mean_a = sum(series_a) / n
    mean_b = sum(series_b) / n
    cov = sum((a - mean_a) * (b - mean_b) for a, b in zip(series_a, series_b))
    var_a = sum((a - mean_a) ** 2 for a in series_a)
    var_b = sum((b - mean_b) ** 2 for b in series_b)
    if var_a == 0 or var_b == 0:
        return {"available": False, "reason": "A return series has zero variance"}
    correlation = cov / (var_a.sqrt() * var_b.sqrt())
    return {"available": True, "correlation": str(correlation), "observations": n}
