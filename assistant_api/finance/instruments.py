"""Generic finance instrument metadata, separate from ASX security rules."""

import re
from typing import Literal

from pydantic import Field, field_validator, model_validator

from assistant_api.finance.schemas import DecimalModel, finite_decimal, optional_finite_decimal

InstrumentKind = Literal["asx_equity", "xauusd_spot_proxy", "index", "etf", "currency"]
InstrumentTimeframe = Literal["1m", "5m", "15m", "1h", "1d"]
TradeDirection = Literal["long", "short"]

GENERIC_SYMBOL_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._=/^:-]{0,20}$")
XAUUSD_SYMBOL = "XAUUSD=X"

MACRO_SOURCE_CATALOG = (
    {"id": "treasury-real-yields", "name": "U.S. Treasury daily real yield curve",
     "topics": ["US real yields"],
     "connector_available": True, "enabled": False,
     "note": "Official daily 10-year real yield; refresh is opt-in and cached with source dates."},
    {"id": "yahoo-macro-proxies", "name": "Yahoo Finance macro proxies",
     "topics": ["DXY proxy", "EUR/USD", "USD/JPY", "VIX"],
     "connector_available": True, "enabled": False,
     "note": "Unofficial proxy feeds; delayed, cached with timestamps, and not execution prices."},
    {"id": "fxcm-forexconnect", "name": "FXCM ForexConnect API",
     "topics": ["XAU/USD broker quote", "XAU/USD historical prices"],
     "connector_available": False, "enabled": False,
     "note": "Official SDK documents live/history pricing but requires an FXCM account and signed EULA. The published Python package lists Python 3.5-3.7; verify a supported runtime before integration."},
    {"id": "federal-reserve", "name": "Federal Reserve",
     "topics": ["Federal Reserve", "US rates"],
     "connector_available": True, "enabled": False,
     "note": "Official press-release RSS headlines; full context requires reading the linked release."},
    {"id": "gdelt", "name": "GDELT DOC API",
     "topics": ["geopolitical events", "gold news"],
     "connector_available": True, "enabled": False,
     "note": "News-index headlines only; each publisher article is unverified until reviewed."},
    {"id": "market-index-announcements", "name": "Market Index",
     "topics": ["ASX company announcements"],
     "connector_available": False, "enabled": False,
     "note": "Automated access is prohibited by its published terms; use manual evidence import or an authorized licensed feed."},
    {"id": "world-gold-council", "name": "World Gold Council",
     "topics": ["central-bank demand"], "connector_available": False,
     "enabled": False, "note": "No licensed or terms-reviewed connector configured."},
)


class InstrumentIn(DecimalModel):
    symbol: str = Field(min_length=1, max_length=21)
    name: str = Field(min_length=1, max_length=200)
    instrument_type: InstrumentKind
    currency: str = Field(min_length=3, max_length=3)
    source_symbol: str | None = Field(default=None, min_length=1, max_length=32)

    @field_validator("symbol", "currency", "source_symbol")
    @classmethod
    def canonical_upper(cls, value: str | None) -> str | None:
        return value.strip().upper() if value is not None else None

    @field_validator("symbol")
    @classmethod
    def valid_symbol(cls, value: str) -> str:
        if not GENERIC_SYMBOL_PATTERN.fullmatch(value):
            raise ValueError("symbol contains unsupported characters")
        return value

    @model_validator(mode="after")
    def type_constraints(self):
        if self.instrument_type == "asx_equity" and not self.symbol.endswith(".AX"):
            raise ValueError("ASX equities must use an .AX suffix")
        if self.instrument_type == "xauusd_spot_proxy" and self.symbol != XAUUSD_SYMBOL:
            raise ValueError(f"XAUUSD spot proxy must use symbol {XAUUSD_SYMBOL}")
        return self


class RiskCalculationIn(DecimalModel):
    instrument: str = Field(default=XAUUSD_SYMBOL, min_length=1, max_length=32)
    account_equity: str | None = None
    risk_percent: str | None = None
    entry_price: str | None = None
    stop_price: str | None = None
    pip_or_point_value: str | None = Field(
        default=None,
        description="Quote-currency value of a one-price-unit move per contract",
    )
    contract_size: str | None = Field(
        default=None,
        description="Underlying units represented by one contract",
    )
    target_price: str | None = None
    account_currency: str = Field(default="USD", min_length=3, max_length=3)

    @field_validator("instrument", "account_currency")
    @classmethod
    def uppercase_text(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator(
        "account_equity", "risk_percent", "entry_price", "stop_price",
        "pip_or_point_value", "contract_size", "target_price",
    )
    @classmethod
    def valid_decimal(cls, value: str | None, info) -> str | None:
        parsed = optional_finite_decimal(value, field_name=info.field_name)
        if parsed is None:
            return None
        number = finite_decimal(parsed, field_name=info.field_name)
        if info.field_name in ("account_equity", "entry_price", "stop_price",
                               "pip_or_point_value", "contract_size", "target_price") and number <= 0:
            raise ValueError(f"{info.field_name} must be positive")
        if info.field_name == "risk_percent" and not 0 < number <= 100:
            raise ValueError("risk_percent must be greater than 0 and at most 100")
        return parsed


class TradingJournalIn(DecimalModel):
    instrument: str = Field(min_length=1, max_length=32)
    direction: TradeDirection
    timeframe: InstrumentTimeframe
    setup: str = Field(min_length=1, max_length=200)
    entry: str | None = None
    stop: str | None = None
    target: str | None = None
    planned_risk: str | None = None
    actual_result: str | None = None
    reasoning: str = Field(default="", max_length=10000)
    evidence: list[dict] = Field(default_factory=list, max_length=30)
    manual_chart_notes: str = Field(
        default="", max_length=10000,
        description="User-entered chart observations; never attributed to an automated source",
    )
    closed_at: str | None = Field(default=None, max_length=40)
    lessons: str = Field(default="", max_length=10000)

    @field_validator("instrument")
    @classmethod
    def uppercase_instrument(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("entry", "stop", "target", "planned_risk", "actual_result")
    @classmethod
    def valid_amount(cls, value: str | None, info) -> str | None:
        parsed = optional_finite_decimal(value, field_name=info.field_name)
        if parsed is not None:
            number = finite_decimal(parsed, field_name=info.field_name)
            if info.field_name in ("entry", "stop", "target") and number <= 0:
                raise ValueError(f"{info.field_name} must be positive")
            if info.field_name == "planned_risk" and number < 0:
                raise ValueError("planned_risk cannot be negative")
        return parsed

    @model_validator(mode="after")
    def stop_and_target_match_direction(self):
        if self.entry is not None and self.stop is not None:
            entry, stop = finite_decimal(self.entry), finite_decimal(self.stop)
            if self.direction == "long" and stop >= entry:
                raise ValueError("A long setup stop must be below entry")
            if self.direction == "short" and stop <= entry:
                raise ValueError("A short setup stop must be above entry")
        if self.entry is not None and self.target is not None:
            entry, target = finite_decimal(self.entry), finite_decimal(self.target)
            if self.direction == "long" and target <= entry:
                raise ValueError("A long setup target must be above entry")
            if self.direction == "short" and target >= entry:
                raise ValueError("A short setup target must be below entry")
        return self


class TradingJournalCloseIn(DecimalModel):
    actual_result: str | None = None
    closed_at: str | None = Field(default=None, max_length=40)
    lessons: str = Field(default="", max_length=10000)

    @field_validator("actual_result")
    @classmethod
    def valid_result(cls, value: str | None) -> str | None:
        return optional_finite_decimal(value, field_name="actual_result")


class GoldMacroEvidenceIn(DecimalModel):
    topic: Literal[
        "us_rates", "real_yields", "usd", "inflation", "federal_reserve",
        "central_bank_demand", "geopolitical_events", "risk_sentiment",
    ]
    title: str = Field(min_length=1, max_length=300)
    publisher: str = Field(min_length=1, max_length=200)
    source_url: str = Field(default="", max_length=2000)
    published_at: str | None = Field(default=None, max_length=40)
    observed_at: str = Field(min_length=1, max_length=40)
    summary: str = Field(min_length=1, max_length=10000)
    status: Literal["unverified", "reviewed"] = "unverified"
    evidence_ids: list[int] = Field(default_factory=list, max_length=30)
