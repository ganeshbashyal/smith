"""Deterministic long-only portfolio analytics.

Coverage-aware: any holding without a known, dated market value is reported
as missing rather than silently excluded or renormalized away.
"""
from dataclasses import dataclass
from decimal import Decimal


@dataclass
class HoldingValuation:
    symbol: str
    units: Decimal
    is_cash: bool
    market_value_aud: Decimal | None
    valuation_date: str | None
    valuation_source: str
    sector: str | None


def value_holding(holding: dict, latest_price_by_symbol: dict[str, dict]) -> HoldingValuation:
    symbol = holding["symbol"]
    units = Decimal(holding["units"])
    is_cash = bool(holding.get("is_cash"))
    if is_cash:
        return HoldingValuation(symbol, units, True, units, holding.get("valuation_date"), "cash", holding.get("sector"))
    manual_value = holding.get("manual_aud_value")
    if manual_value is not None:
        return HoldingValuation(
            symbol, units, False, Decimal(manual_value), holding.get("valuation_date"),
            "manual_aud_entry", holding.get("sector"),
        )
    bar = latest_price_by_symbol.get(symbol)
    if bar and bar.get("close") is not None:
        value = Decimal(bar["close"]) * units
        return HoldingValuation(symbol, units, False, value, bar.get("observed_on"), "asx_quote", holding.get("sector"))
    return HoldingValuation(symbol, units, False, None, None, "unavailable", holding.get("sector"))


@dataclass
class PortfolioAnalysis:
    total_value_aud: str | None
    coverage_count: int
    total_count: int
    weights: dict[str, str]
    sector_concentration: dict[str, str]
    largest_positions: list[dict]
    missing_valuations: list[str]


def analyze_portfolio(valuations: list[HoldingValuation]) -> PortfolioAnalysis:
    covered = [v for v in valuations if v.market_value_aud is not None]
    missing = [v.symbol for v in valuations if v.market_value_aud is None]
    total = sum((v.market_value_aud for v in covered), Decimal(0)) if covered else None
    weights: dict[str, str] = {}
    sector_totals: dict[str, Decimal] = {}
    if total and total > 0:
        for v in covered:
            weights[v.symbol] = str(v.market_value_aud / total)
            sector = v.sector or "Unknown"
            sector_totals[sector] = sector_totals.get(sector, Decimal(0)) + v.market_value_aud
    sector_concentration = {
        sector: str(value / total) for sector, value in sector_totals.items()
    } if total and total > 0 else {}
    largest = sorted(covered, key=lambda v: v.market_value_aud, reverse=True)[:10]
    return PortfolioAnalysis(
        total_value_aud=str(total) if total is not None else None,
        coverage_count=len(covered),
        total_count=len(valuations),
        weights=weights,
        sector_concentration=sector_concentration,
        largest_positions=[
            {"symbol": v.symbol, "market_value_aud": str(v.market_value_aud),
             "valuation_date": v.valuation_date, "valuation_source": v.valuation_source}
            for v in largest
        ],
        missing_valuations=missing,
    )


def apply_shocks(valuations: list[HoldingValuation], shocks: list[dict]) -> dict:
    """Apply user-defined percentage shocks per symbol or group label.

    Each holding is shocked by at most one matching shock (first match wins,
    in the order supplied) to keep overlap rules explicit and non-additive.
    """
    shocked_total = Decimal(0)
    baseline_total = Decimal(0)
    applied: list[dict] = []
    unresolved_symbols: list[str] = []
    for v in valuations:
        if v.market_value_aud is None:
            unresolved_symbols.append(v.symbol)
            continue
        baseline_total += v.market_value_aud
        match = next((s for s in shocks if s["symbol_or_group"].upper() == v.symbol.upper()
                     or s["symbol_or_group"].upper() == (v.sector or "").upper()), None)
        if match:
            pct = Decimal(match["pct_change"])
            new_value = v.market_value_aud * (1 + pct / Decimal(100))
            applied.append({"symbol": v.symbol, "shock": match["label"], "pct_change": str(pct)})
        else:
            new_value = v.market_value_aud
        shocked_total += new_value
    return {
        "baseline_total_aud": str(baseline_total),
        "shocked_total_aud": str(shocked_total),
        "change_aud": str(shocked_total - baseline_total),
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
