"""Typed request/response models for the finance module.

Money, quantities and rates use Decimal with exact string JSON serialization
(never float) so ratio and valuation arithmetic cannot silently lose precision.
Missing figures are represented as ``None`` (unavailable), never coerced to 0.
"""
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Period = Literal["annual", "interim", "ttm"]
Currency = Literal["AUD", "USD", "EUR", "GBP", "NZD", "JPY", "CAD", "CNY", "HKD", "SGD"]
EvidenceSourceType = Literal["issuer_filing", "asx_announcement", "annual_report", "user_upload", "manual_entry"]
DocumentType = Literal["annual_report", "half_year_report", "announcement", "transcript", "prospectus", "other"]
ThesisStance = Literal["bull", "base", "bear"]
AlertKind = Literal["developing", "material", "thesis_review", "confirmation"]


class DecimalModel(BaseModel):
    """Base model that serializes Decimal fields as exact strings, not floats."""
    model_config = ConfigDict(json_encoders={Decimal: str})


def parse_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


class Security(DecimalModel):
    symbol: str = Field(min_length=1, max_length=12, pattern=r"^[A-Za-z0-9.]+$")
    name: str = Field(min_length=1, max_length=200)
    sector: str | None = Field(default=None, max_length=120)
    notes: str = Field(default="", max_length=20000)

    @field_validator("symbol")
    @classmethod
    def upper(cls, value: str) -> str:
        return value.upper()


class EvidenceDocumentIn(DecimalModel):
    symbol: str = Field(min_length=1, max_length=12)
    title: str = Field(min_length=1, max_length=300)
    publisher: str = Field(min_length=1, max_length=200)
    source_type: EvidenceSourceType
    document_type: DocumentType
    source_url: str = Field(default="", max_length=2000)
    published_on: date | None = None
    period: Period | None = None
    text: str = Field(min_length=1, max_length=2_000_000)


class StatementFigureIn(DecimalModel):
    symbol: str = Field(min_length=1, max_length=12)
    period: Period
    fiscal_period_end: date
    currency: Currency
    scale: Literal["units", "thousands", "millions"] = "units"
    consolidated: bool = True
    # Point-in-time balance-sheet figures.
    cash_and_equivalents: str | None = None
    total_debt: str | None = None
    shares_diluted: str | None = None
    # Period flow figures.
    revenue: str | None = None
    ebitda: str | None = None
    net_income: str | None = None
    operating_cash_flow: str | None = None
    capital_expenditure: str | None = None
    dividends_paid: str | None = None
    evidence_id: int | None = Field(default=None, description="Confirmed evidence document this figure traces to")
    source_note: str = Field(default="", max_length=2000, description="Required when evidence_id is absent (manual entry origin)")

    @field_validator(
        "cash_and_equivalents", "total_debt", "shares_diluted", "revenue", "ebitda",
        "net_income", "operating_cash_flow", "capital_expenditure", "dividends_paid",
    )
    @classmethod
    def valid_decimal(cls, value: str | None) -> str | None:
        if value is None:
            return None
        Decimal(value)
        return value


class ThesisRevisionIn(DecimalModel):
    symbol: str = Field(min_length=1, max_length=12)
    thesis: str = Field(min_length=1, max_length=20000)
    counter_thesis: str = Field(default="", max_length=20000)
    assumptions: str = Field(default="", max_length=20000)
    sensitivities: str = Field(default="", max_length=20000)
    catalysts: str = Field(default="", max_length=20000)
    risks: str = Field(default="", max_length=20000)
    invalidation_criteria: str = Field(default="", max_length=20000)
    evidence_ids: list[int] = Field(default_factory=list)


class WatchlistIn(DecimalModel):
    name: str = Field(min_length=1, max_length=120)
    symbols: list[str] = Field(min_length=1, max_length=50)
    price_alert_threshold_pct: str | None = Field(default=None, description="Optional user-set percentage move threshold")

    @field_validator("symbols")
    @classmethod
    def dedupe_upper(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for symbol in value:
            upper = symbol.strip().upper()
            if upper and upper not in seen:
                seen.append(upper)
        if not seen:
            raise ValueError("At least one symbol is required")
        if len(seen) > 50:
            raise ValueError("A watchlist refresh is capped at 50 symbols")
        return seen


class CatalystIn(DecimalModel):
    symbol: str = Field(min_length=1, max_length=12)
    title: str = Field(min_length=1, max_length=300)
    event_date: date
    timezone: str = Field(default="Australia/Sydney", max_length=64)
    source_url: str = Field(default="", max_length=2000)
    is_manual: bool = True


class RatioInputs(DecimalModel):
    price: str | None = None
    shares_diluted: str | None = None
    cash_and_equivalents: str | None = None
    total_debt: str | None = None
    revenue: str | None = None
    ebitda: str | None = None
    net_income: str | None = None
    operating_cash_flow: str | None = None
    capital_expenditure: str | None = None
    dividends_paid: str | None = None


class DCFScenarioIn(DecimalModel):
    name: str = Field(min_length=1, max_length=60)
    projected_fcff: list[str] = Field(min_length=1, max_length=15)
    discount_rate: str
    terminal_growth_rate: str
    net_debt: str
    shares_diluted: str
    probability: str | None = None


class DCFRequest(DecimalModel):
    symbol: str = Field(min_length=1, max_length=12)
    scenarios: list[DCFScenarioIn] = Field(min_length=1, max_length=5)
    current_price: str | None = None


class HoldingRow(DecimalModel):
    symbol: str = Field(min_length=1, max_length=12)
    units: str
    cost_basis_total: str | None = None
    sector: str | None = None
    exposure_notes: str = ""
    is_cash: bool = False
    manual_aud_value: str | None = Field(default=None, description="Required for manually valued overseas holdings")
    valuation_date: date | None = None


class PortfolioImportPreview(DecimalModel):
    portfolio_id: int
    rows: list[dict]


class ShockIn(DecimalModel):
    label: str = Field(min_length=1, max_length=120)
    symbol_or_group: str = Field(min_length=1, max_length=120)
    pct_change: str
