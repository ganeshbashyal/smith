"""Typed request/response models for the finance module.

Money, quantities and rates use Decimal with exact string JSON serialization
(never float) so ratio and valuation arithmetic cannot silently lose precision.
Missing figures are represented as ``None`` (unavailable), never coerced to 0.
"""
import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Period = Literal["annual", "interim", "ttm"]
Currency = Literal["AUD", "USD", "EUR", "GBP", "NZD", "JPY", "CAD", "CNY", "HKD", "SGD"]
EvidenceSourceType = Literal["issuer_filing", "asx_announcement", "annual_report", "user_upload", "manual_entry"]
DocumentType = Literal["annual_report", "half_year_report", "announcement", "transcript", "prospectus", "other"]
ThesisStance = Literal["bull", "base", "bear"]
AlertKind = Literal["developing", "material", "thesis_review", "confirmation"]

ASX_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9]{1,8}\.AX$")


class DecimalModel(BaseModel):
    """Base model that serializes Decimal fields as exact strings, not floats."""
    model_config = ConfigDict(json_encoders={Decimal: str})


def parse_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))


def finite_decimal(value: str, *, field_name: str = "value") -> Decimal:
    """Parse a Decimal and reject NaN/Infinity, which InvalidOperation does not catch."""
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise ValueError(f"{field_name}: invalid decimal {value!r}") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field_name}: must be a finite number, got {value!r}")
    return parsed


def optional_finite_decimal(value: str | None, *, field_name: str = "value") -> str | None:
    if value is None or value == "":
        return None
    finite_decimal(value, field_name=field_name)
    return value


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

    @field_validator("symbol")
    @classmethod
    def upper_symbol(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator(
        "cash_and_equivalents", "total_debt", "shares_diluted", "revenue", "ebitda",
        "net_income", "operating_cash_flow", "capital_expenditure", "dividends_paid",
    )
    @classmethod
    def valid_decimal(cls, value: str | None, info) -> str | None:
        parsed = optional_finite_decimal(value, field_name=info.field_name)
        if info.field_name == "shares_diluted" and parsed is not None and finite_decimal(parsed) <= 0:
            raise ValueError("shares_diluted must be positive")
        return parsed


class PdfEvidenceMeta(DecimalModel):
    """Typed metadata for a PDF evidence upload (query params alongside the file).

    Replaces raw str/date.fromisoformat() parsing that previously 500'd on an
    invalid date; FastAPI now validates this before the handler body runs.
    """
    title: str = Field(min_length=1, max_length=300)
    publisher: str = Field(min_length=1, max_length=200)
    source_type: EvidenceSourceType
    document_type: DocumentType
    source_url: str = Field(default="", max_length=2000)
    published_on: date | None = None
    period: Period | None = None


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
            if not upper:
                continue
            if not ASX_SYMBOL_PATTERN.match(upper):
                raise ValueError(
                    f"{upper!r} is not a valid ASX symbol (expected e.g. 'BHP.AX'); "
                    "watchlists cover AUD-denominated ASX listings only"
                )
            if upper not in seen:
                seen.append(upper)
        if not seen:
            raise ValueError("At least one symbol is required")
        if len(seen) > 50:
            raise ValueError("A watchlist refresh is capped at 50 symbols")
        return seen

    @field_validator("price_alert_threshold_pct")
    @classmethod
    def valid_threshold(cls, value: str | None) -> str | None:
        parsed = optional_finite_decimal(value, field_name="price_alert_threshold_pct")
        if parsed is not None and finite_decimal(parsed) <= 0:
            raise ValueError("price_alert_threshold_pct must be positive")
        return parsed


class CatalystIn(DecimalModel):
    symbol: str = Field(min_length=1, max_length=12)
    title: str = Field(min_length=1, max_length=300)
    event_date: date
    timezone: str = Field(default="Australia/Sydney", max_length=64)
    source_url: str = Field(default="", max_length=2000)
    is_manual: bool = True


class RatioValuationRequest(DecimalModel):
    """Live price input for ratio valuation; all financial figures come from a
    confirmed statement revision, never from an arbitrary request body."""
    price: str
    price_currency: Currency = Field(
        description="Currency the price is quoted in; must match the confirmed statement's "
                    "currency since no automatic FX conversion is performed",
    )
    price_date: date = Field(description="Date the price was observed/approved by the user")
    statement_period: Period
    statement_fiscal_period_end: date

    @field_validator("price")
    @classmethod
    def valid_price(cls, value: str) -> str:
        parsed = finite_decimal(value, field_name="price")
        if parsed <= 0:
            raise ValueError("price must be positive")
        return value


# Retained for backward-compatible typed access to statement figures when
# composing ratio inputs internally (not accepted directly from a request body).
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

    @field_validator(
        "price", "shares_diluted", "cash_and_equivalents", "total_debt", "revenue",
        "ebitda", "net_income", "operating_cash_flow", "capital_expenditure", "dividends_paid",
    )
    @classmethod
    def valid_decimal(cls, value: str | None, info) -> str | None:
        return optional_finite_decimal(value, field_name=info.field_name)


class DCFScenarioIn(DecimalModel):
    name: str = Field(min_length=1, max_length=60)
    projected_fcff: list[str] = Field(min_length=1, max_length=15)
    discount_rate: str
    terminal_growth_rate: str
    net_debt: str
    shares_diluted: str
    probability: str | None = None

    @field_validator("projected_fcff")
    @classmethod
    def valid_fcff(cls, value: list[str]) -> list[str]:
        for item in value:
            finite_decimal(item, field_name="projected_fcff")
        return value

    @field_validator("discount_rate")
    @classmethod
    def valid_discount_rate(cls, value: str) -> str:
        parsed = finite_decimal(value, field_name="discount_rate")
        if parsed <= Decimal("-1"):
            raise ValueError("discount_rate must be greater than -100%")
        return value

    @field_validator("terminal_growth_rate")
    @classmethod
    def valid_terminal_growth(cls, value: str) -> str:
        finite_decimal(value, field_name="terminal_growth_rate")
        return value

    @field_validator("net_debt")
    @classmethod
    def valid_net_debt(cls, value: str) -> str:
        finite_decimal(value, field_name="net_debt")
        return value

    @field_validator("shares_diluted")
    @classmethod
    def valid_shares(cls, value: str) -> str:
        if finite_decimal(value, field_name="shares_diluted") <= 0:
            raise ValueError("shares_diluted must be positive")
        return value

    @field_validator("probability")
    @classmethod
    def valid_probability(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = finite_decimal(value, field_name="probability")
        if parsed < 0 or parsed > 1:
            raise ValueError("probability must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def discount_above_growth(self):
        if finite_decimal(self.discount_rate) <= finite_decimal(self.terminal_growth_rate):
            raise ValueError(
                f"Scenario {self.name!r}: discount_rate must be greater than terminal_growth_rate"
            )
        return self


class DCFRequest(DecimalModel):
    symbol: str = Field(min_length=1, max_length=12)
    scenarios: list[DCFScenarioIn] = Field(min_length=1, max_length=5)
    current_price: str | None = None
    current_price_date: date | None = Field(
        default=None, description="Required alongside current_price: the date the price was observed/approved"
    )

    @field_validator("current_price")
    @classmethod
    def valid_current_price(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if finite_decimal(value, field_name="current_price") <= 0:
            raise ValueError("current_price must be positive")
        return value

    @model_validator(mode="after")
    def price_requires_date(self):
        if self.current_price is not None and self.current_price_date is None:
            raise ValueError("current_price_date is required whenever current_price is supplied")
        return self


class DCFSensitivityRequest(DecimalModel):
    base: DCFScenarioIn
    discount_rate_deltas: list[str] = Field(min_length=1, max_length=11)
    terminal_growth_deltas: list[str] = Field(min_length=1, max_length=11)

    @field_validator("discount_rate_deltas", "terminal_growth_deltas")
    @classmethod
    def valid_deltas(cls, value: list[str], info) -> list[str]:
        for item in value:
            finite_decimal(item, field_name=info.field_name)
        return value


class HoldingRow(DecimalModel):
    symbol: str = Field(min_length=1, max_length=12)
    units: str
    cost_basis_total: str | None = None
    sector: str | None = None
    exposure_notes: str = ""
    is_cash: bool = False
    manual_aud_value: str | None = Field(default=None, description="Required for manually valued overseas holdings")
    valuation_date: date | None = None

    @field_validator("symbol")
    @classmethod
    def canonical_symbol(cls, value: str) -> str:
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError("symbol is required")
        return cleaned

    @field_validator("units")
    @classmethod
    def valid_units(cls, value: str) -> str:
        finite_decimal(value, field_name="units")
        return value

    @field_validator("cost_basis_total")
    @classmethod
    def valid_cost_basis(cls, value: str | None) -> str | None:
        parsed = optional_finite_decimal(value, field_name="cost_basis_total")
        if parsed is not None and finite_decimal(parsed) < 0:
            raise ValueError("cost_basis_total cannot be negative")
        return parsed

    @field_validator("manual_aud_value")
    @classmethod
    def valid_manual_value(cls, value: str | None) -> str | None:
        parsed = optional_finite_decimal(value, field_name="manual_aud_value")
        if parsed is not None and finite_decimal(parsed) < 0:
            raise ValueError("manual_aud_value cannot be negative")
        return parsed

    @model_validator(mode="after")
    def cross_field_rules(self):
        units = finite_decimal(self.units)
        if self.is_cash:
            if units < 0:
                raise ValueError(f"{self.symbol}: cash holdings cannot have negative units")
        else:
            if units <= 0:
                raise ValueError(f"{self.symbol}: units must be positive")
        if self.manual_aud_value is not None and self.valuation_date is None:
            raise ValueError(
                f"{self.symbol}: manual_aud_value requires a valuation_date "
                "(manually valued overseas holdings must be dated)"
            )
        if self.valuation_date is not None and self.valuation_date > date.today():
            raise ValueError(
                f"{self.symbol}: valuation_date cannot be in the future "
                "(a future-dated valuation is not valid current evidence)"
            )
        return self


class PortfolioImportPreview(DecimalModel):
    portfolio_id: int
    rows: list[dict]


class ShockIn(DecimalModel):
    label: str = Field(min_length=1, max_length=120)
    symbol_or_group: str = Field(min_length=1, max_length=120)
    pct_change: str

    @field_validator("pct_change")
    @classmethod
    def valid_pct(cls, value: str) -> str:
        parsed = finite_decimal(value, field_name="pct_change")
        if parsed < Decimal("-100"):
            raise ValueError("pct_change cannot be less than -100% (value cannot go below zero)")
        return value
