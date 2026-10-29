"""Build a one-instrument, source-dated research report in the background.

Examples:
    python -m scripts.research_instrument --instrument ASX:NCK --allow-network
    python -m scripts.research_instrument --instrument XAUUSD --asset-type auto

The worker only runs this fixed workflow; it never executes model-supplied
commands or code. Network access is opt-in. Reports, progress, and issues are
written to the selected job directory.
"""

import argparse
import asyncio
import csv
from datetime import date, datetime, timedelta, timezone
from html import escape
import io
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import re
import socket
import sys
from typing import Awaitable, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
import zipfile
import httpx

from pypdf import PdfReader


logger = logging.getLogger(__name__)
RBA_CASH_RATE_URL = "https://www.rba.gov.au/statistics/tables/xls/f01d.xlsx"
RBA_AUD_USD_URL = "https://www.rba.gov.au/statistics/tables/csv/f11.1-data.csv"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
MAX_REPORT_BYTES = 25_000_000
MAX_MARKET_BYTES = 5_000_000
MAX_MACRO_BYTES = 2_000_000
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
INSTRUMENT_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9=.^/_:-]{0,20}$")
ASSET_TYPES = ("auto", "asx_equity", "equity", "etf", "index", "fx", "commodity", "crypto", "other")
METRICS = (
    ("revenue_m", re.compile(r"\bRevenue\b", re.I)),
    ("ebitda_m", re.compile(r"\bEBITDA\b", re.I)),
    ("npat_m", re.compile(r"\bNet profit after tax\b", re.I)),
    ("eps_cents", re.compile(r"\bEarnings per share\b", re.I)),
    ("dividend_cents", re.compile(r"\bOrdinary dividends?(?: paid)? per share\b", re.I)),
    ("share_price_fy_end", re.compile(r"\bShare price at (?:the )?(?:financial year end|30 June)\b", re.I)),
    ("stores", re.compile(r"\bStores\b", re.I)),
)


class ResearchError(RuntimeError):
    """A required research input or source could not be processed."""


def select_method(instrument: str, requested_type: str = "auto") -> dict:
    """Select a bounded research method; bare symbols default to the ASX."""
    raw = instrument.strip().upper()
    if not raw or len(raw) > 40 or any(char in raw for char in ",;\n\r"):
        raise ValueError("Enter one instrument at a time (for example ASX:NCK or XAUUSD)")
    if requested_type not in ASSET_TYPES:
        raise ValueError(f"Unsupported asset type: {requested_type}")

    explicit_type = requested_type
    body = raw
    if ":" in raw and not raw.startswith("^") and not raw.endswith("=X"):
        prefix, _, suffix = raw.partition(":")
        aliases = {
            "ASX": "asx_equity", "AU": "asx_equity",
            "EQUITY": "equity", "ETF": "etf", "INDEX": "index",
            "FX": "fx", "FOREX": "fx", "COMMODITY": "commodity",
            "CRYPTO": "crypto", "OTHER": "other",
        }
        if prefix in aliases:
            explicit_type = aliases[prefix]
            body = suffix
    if explicit_type == "auto":
        if body in ("GOLD", "XAUUSD", "XAU/USD", "XAUUSD=X", "GC=F", "CL=F"):
            explicit_type = "commodity"
        elif body.startswith("^"):
            explicit_type = "index"
        elif re.fullmatch(r"[A-Z]{3}/?[A-Z]{3}(?:=X)?", body):
            explicit_type = "fx"
        elif re.fullmatch(r"[A-Z0-9]{3,5}\.AX", body):
            explicit_type = "asx_equity"
        elif re.fullmatch(r"[A-Z0-9]{1,5}", body):
            explicit_type = "asx_equity"
        elif re.fullmatch(r"[A-Z0-9]{2,12}-USD", body):
            explicit_type = "crypto"
        else:
            explicit_type = "other"

    if explicit_type == "auto":
        raise ValueError("Could not select an instrument type")
    if not body or not INSTRUMENT_PATTERN.fullmatch(body):
        raise ValueError("Instrument contains unsupported characters")

    provisional = requested_type == "auto" and ":" not in raw and not (
        body.startswith("^") or body in ("GOLD", "XAUUSD", "XAU/USD", "XAUUSD=X", "GC=F", "CL=F")
        or re.fullmatch(r"[A-Z]{3}/?[A-Z]{3}(?:=X)?", body)
        or re.fullmatch(r"[A-Z0-9]{2,12}-USD", body)
        or body.endswith(".AX")
    )
    if explicit_type == "asx_equity":
        source_symbol = body if body.endswith(".AX") else f"{body}.AX"
        method = (
            "ASX company research: audited annual-report history and corporate actions "
            "when issuer sources are available; price/technical context; Australian macro context."
        )
    elif explicit_type in ("equity", "etf"):
        source_symbol = body
        method = (
            "Issuer/security research: dated price and corporate-action context; fundamentals "
            "only from linked or supplied issuer filings."
        )
    elif explicit_type == "index":
        source_symbol = body
        method = "Index method: price trend, volatility, constituent/sector context, and macro regime; no single-company fundamentals."
    elif explicit_type == "fx":
        pair = body.replace("/", "").removesuffix("=X")
        source_symbol = f"{pair}=X"
        method = "FX method: pair price structure, volatility, rate/inflation context, and conditional scenarios; no company fundamentals."
    elif explicit_type == "commodity":
        source_symbol = "XAUUSD=X" if body in ("GOLD", "XAUUSD", "XAU/USD", "XAUUSD=X") else body
        method = "Commodity method: timestamped proxy/price history, volatility, macro drivers, and conditional scenarios; no company fundamentals."
    elif explicit_type == "crypto":
        source_symbol = body
        method = "Crypto method: venue-specific price/liquidity and volatility review; no company-fundamental method."
    else:
        source_symbol = body
        method = "General instrument method: attempt allowlisted public price history; require an approved instrument-specific source for fundamentals or full event history."

    warning = (
        ["A bare symbol is treated as an ASX candidate; confirm the exchange if that is not intended."]
        if provisional else []
    )
    return {
        "instrument": raw,
        "normalized_instrument": body,
        "asset_type": explicit_type,
        "source_symbol": source_symbol,
        "method": method,
        "classification": "provisional" if provisional else "identified",
        "warnings": warning,
    }


def _validated_public_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ResearchError("Source URLs must use HTTPS and may not include credentials")
    if parsed.hostname.casefold() in ("localhost",):
        raise ResearchError("Local and private-network source URLs are not permitted")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, None)
    except OSError:
        return url
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
        except ValueError:
            continue
        if not ip.is_global:
            raise ResearchError("Local and private-network source URLs are not permitted")
    return url


def fetch_bytes(url: str, limit: int, timeout: float = 30) -> bytes:
    _validated_public_url(url)
    request = Request(url, headers={"User-Agent": "MrSmith-local-research/1.0"})
    try:
        with urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
            _validated_public_url(final_url)
            raw = response.read(limit + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise ResearchError(f"Could not retrieve source ({type(exc).__name__})") from exc
    if len(raw) > limit:
        raise ResearchError(f"Source exceeds the {limit:,}-byte limit")
    return raw


def parse_report_history(raw_pdf: bytes) -> dict[int, dict]:
    """Extract the issuer's five-year performance table from a text PDF."""
    try:
        reader = PdfReader(io.BytesIO(raw_pdf))
        if reader.is_encrypted:
            raise ResearchError("Annual report is password-protected")
        pages = [page.extract_text() or "" for page in reader.pages]
    except ResearchError:
        raise
    except (OSError, ValueError, IndexError, KeyError) as exc:
        raise ResearchError(f"Annual report PDF could not be read ({type(exc).__name__})") from exc
    text = " ".join(" ".join(pages).split())
    section = re.search(
        r"past five years\s*:\s*((?:20\d{2}\*?\s*){5})",
        text, re.I,
    )
    if not section:
        raise ResearchError("No machine-readable five-year financial table found in annual report")
    years = [int(value) for value in re.findall(r"20\d{2}", section.group(1))]
    if len(years) != 5 or years != sorted(years):
        raise ResearchError("Five-year table has invalid financial-year labels")
    table = text[section.end():section.end() + 2600]
    series = {year: {"fy": year} for year in years}
    for field, pattern in METRICS:
        match = pattern.search(table)
        if not match:
            continue
        next_metric = [
            other.search(table, match.end())
            for other_field, other in METRICS
            if other_field != field
        ]
        next_metric = [item.start() for item in next_metric if item]
        end = min(next_metric, default=min(len(table), match.end() + 150))
        values_text = table[match.end():end]
        tokens = re.findall(r"\(?-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\)?", values_text)
        if len(tokens) > 6:
            for index in range(len(tokens) - 1):
                if "." in tokens[index] and re.fullmatch(r"\d", tokens[index + 1]):
                    tokens[index:index + 2] = [tokens[index] + tokens[index + 1]]
                    break
        values = []
        for token in tokens[:5]:
            negative = token.startswith("(") and token.endswith(")")
            number = float(token.strip("()").replace(",", ""))
            values.append(-number if negative else number)
        if len(values) == 5:
            for year, value in zip(years, values):
                series[year][field] = value
    if not all("revenue_m" in row and "npat_m" in row for row in series.values()):
        raise ResearchError("Annual report table is missing revenue or net profit figures")
    return series


def parse_rba_cash_rates(raw_xlsx: bytes) -> list[tuple[date, float]]:
    try:
        workbook = zipfile.ZipFile(io.BytesIO(raw_xlsx))
        shared_root = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
        strings = [
            "".join(element.text or "" for element in item.findall(".//m:t", NS))
            for item in shared_root.findall("m:si", NS)
        ]
        sheet = ET.fromstring(workbook.read("xl/worksheets/sheet1.xml"))
    except (KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise ResearchError("RBA cash-rate workbook is not a valid XLSX file") from exc
    observations = []
    for row in sheet.findall(".//m:sheetData/m:row", NS):
        cells = {}
        for cell in row.findall("m:c", NS):
            ref = cell.attrib.get("r", "")
            column = re.match(r"[A-Z]+", ref)
            if not column or column.group(0) not in ("A", "B"):
                continue
            value = cell.find("m:v", NS)
            if value is None or value.text is None:
                continue
            raw_value = value.text
            if cell.attrib.get("t") == "s":
                raw_value = strings[int(raw_value)]
            cells[column.group(0)] = raw_value
        try:
            observed = (datetime(1899, 12, 30) + timedelta(days=float(cells["A"]))).date()
            rate = float(cells["B"])
        except (KeyError, ValueError, OverflowError):
            continue
        if math.isfinite(rate) and 0 <= rate <= 100:
            observations.append((observed, rate))
    if not observations:
        raise ResearchError("RBA cash-rate workbook contained no valid observations")
    return sorted(observations)


def parse_rba_aud_usd(raw_csv: bytes) -> list[tuple[date, float]]:
    try:
        rows = csv.reader(io.StringIO(raw_csv.decode("utf-8-sig")))
        items = list(rows)
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ResearchError("RBA exchange-rate CSV could not be decoded") from exc
    observations = []
    for row in items[11:]:
        if len(row) < 2:
            continue
        try:
            observed = datetime.strptime(row[0], "%d-%b-%Y").date()
            rate = float(row[1])
        except (ValueError, TypeError):
            continue
        if math.isfinite(rate) and rate > 0:
            observations.append((observed, rate))
    if not observations:
        raise ResearchError("RBA exchange-rate CSV contained no valid observations")
    return sorted(observations)


def financial_year_end_series(observations: list[tuple[date, float]], years: list[int]) -> dict[int, float]:
    result = {}
    for year in years:
        cutoff = date(year, 6, 30)
        values = [value for observed, value in observations if observed <= cutoff]
        if values:
            result[year] = values[-1]
    return result


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    temporary.replace(path)


class Progress:
    def __init__(self, job_dir: Path, instrument: str):
        self.path = job_dir / "status.json"
        self.payload = {
            "instrument": instrument, "status": "queued", "progress_percent": 0,
            "stage": "queued", "steps": [], "issues": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None, "report": None,
        }
        self.update("running", "starting", 1, "Starting the instrument research worker")

    def update(self, status: str, stage: str, percent: int, detail: str) -> None:
        self.payload["status"] = status
        self.payload["stage"] = stage
        self.payload["progress_percent"] = max(0, min(100, percent))
        self.payload["steps"].append({
            "stage": stage, "detail": detail,
            "at": datetime.now(timezone.utc).isoformat(),
        })
        self.payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        if status in ("completed", "failed"):
            self.payload["finished_at"] = self.payload["updated_at"]
        _atomic_json(self.path, self.payload)

    def issue(self, message: str, *, severity: str = "warning") -> None:
        self.payload["issues"].append({"severity": severity, "message": message})
        self.payload["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_json(self.path, self.payload)


def _load_company_manifest(data_dir: Path, symbol: str) -> dict | None:
    path = data_dir / "company_sources" / f"{symbol}.json"
    if not path.exists():
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchError(f"Company source manifest is unreadable: {path.name}") from exc
    if manifest.get("symbol", "").upper() != symbol:
        raise ResearchError("Company source manifest symbol does not match the requested instrument")
    return manifest


def _fetch_yahoo_history(source_symbol: str) -> dict:
    url = f"{YAHOO_CHART_URL}{quote(source_symbol, safe='')}"
    url += "?" + urlencode({"range": "10y", "interval": "1d", "events": "div,splits"})
    raw = fetch_bytes(url, MAX_MARKET_BYTES)
    try:
        chart = json.loads(raw)["chart"]
        if chart.get("error"):
            raise ResearchError("Yahoo Finance returned an error for this instrument")
        payload = chart["result"][0]
        meta = payload["meta"]
        timestamps = payload.get("timestamp") or []
        quote_block = payload["indicators"]["quote"][0]
        opens = quote_block.get("open") or []
        highs = quote_block.get("high") or []
        lows = quote_block.get("low") or []
        closes = quote_block.get("close") or []
        volumes = quote_block.get("volume") or []
        adjusted_block = payload["indicators"].get("adjclose") or []
        adjusted = adjusted_block[0].get("adjclose") if adjusted_block else []
        bars = []
        for index, timestamp in enumerate(timestamps):
            close = closes[index] if index < len(closes) else None
            if not isinstance(close, (int, float)) or not math.isfinite(close) or close <= 0:
                continue
            adj = adjusted[index] if index < len(adjusted or []) else None
            bars.append({
                "date": datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat(),
                "open": opens[index] if index < len(opens) and isinstance(opens[index], (int, float)) and math.isfinite(opens[index]) else None,
                "high": highs[index] if index < len(highs) and isinstance(highs[index], (int, float)) and math.isfinite(highs[index]) else None,
                "low": lows[index] if index < len(lows) and isinstance(lows[index], (int, float)) and math.isfinite(lows[index]) else None,
                "close": close,
                "adjusted_close": adj if isinstance(adj, (int, float)) and math.isfinite(adj) else None,
                "volume": volumes[index] if index < len(volumes) and isinstance(volumes[index], int) else None,
            })
        return {
            "status": "ok" if bars else "no_data",
            "source": "Yahoo Finance public chart endpoint (unofficial; may be delayed)",
            "currency": meta.get("currency"),
            "exchange_timezone": meta.get("exchangeTimezoneName"),
            "bars": bars,
            "events": payload.get("events", {}),
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
        }
    except ResearchError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ResearchError("Yahoo Finance returned an invalid chart response") from exc


def _annual_report_data(manifest: dict, progress: Progress) -> tuple[dict[int, dict], list[dict]]:
    combined: dict[int, dict] = {}
    citations = []
    reports = manifest.get("annual_reports", [])
    if not isinstance(reports, list):
        raise ResearchError("Annual report sources must be a list")
    for index, source in enumerate(reports, start=1):
        if not isinstance(source, dict) or not isinstance(source.get("url"), str):
            progress.issue(f"Annual report source {index} is incomplete")
            continue
        label = str(source.get("label") or f"Annual report {index}")
        progress.update("running", "issuer_reports", min(25 + index * 6, 55),
                        f"Downloading and extracting {label}")
        raw = fetch_bytes(source["url"], MAX_REPORT_BYTES, timeout=60)
        parsed = parse_report_history(raw)
        citations.append({"label": label, "url": source["url"]})
        for year, row in parsed.items():
            if year in combined:
                for key, value in row.items():
                    if key == "fy":
                        continue
                    if key in combined[year] and combined[year][key] != value:
                        raise ResearchError(
                            f"Conflicting {key} values for FY{year} across official reports"
                        )
                    combined[year][key] = value
            else:
                combined[year] = row
    return dict(sorted(combined.items())), citations


def _macro_series(years: list[int], progress: Progress, allow_network: bool) -> dict:
    result = {"rba_cash_rate_pct": {}, "rba_aud_usd": {}, "sources": []}
    if not allow_network:
        progress.issue("External feeds are disabled; historical RBA macro series were not retrieved.")
        return result
    progress.update("running", "macro_data", 58, "Fetching official RBA cash-rate history")
    try:
        cash = parse_rba_cash_rates(fetch_bytes(RBA_CASH_RATE_URL, MAX_MACRO_BYTES))
        result["rba_cash_rate_pct"] = financial_year_end_series(cash, years)
        result["sources"].append({"label": "RBA Statistical Table F1", "url": RBA_CASH_RATE_URL})
    except ResearchError as exc:
        progress.issue(f"RBA cash-rate series unavailable: {exc}")
    progress.update("running", "macro_data", 64, "Fetching official RBA AUD/USD history")
    try:
        fx = parse_rba_aud_usd(fetch_bytes(RBA_AUD_USD_URL, MAX_MACRO_BYTES))
        result["rba_aud_usd"] = financial_year_end_series(fx, years)
        result["sources"].append({"label": "RBA Statistical Table F11.1", "url": RBA_AUD_USD_URL})
    except ResearchError as exc:
        progress.issue(f"RBA AUD/USD series unavailable: {exc}")
    return result


def _write_history_csv(path: Path, history: dict[int, dict], macro: dict) -> None:
    fields = [
        "financial_year", "revenue_aud_m", "ebitda_aud_m", "npat_aud_m",
        "npat_margin_pct", "eps_cents", "dividend_cents", "share_price_fy_end_aud",
        "stores", "rba_cash_rate_june_pct", "rba_aud_usd_june",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for year, row in sorted(history.items()):
            revenue = row.get("revenue_m")
            npat = row.get("npat_m")
            writer.writerow({
                "financial_year": year,
                "revenue_aud_m": revenue,
                "ebitda_aud_m": row.get("ebitda_m"),
                "npat_aud_m": npat,
                "npat_margin_pct": round(npat / revenue * 100, 2) if revenue else None,
                "eps_cents": row.get("eps_cents"),
                "dividend_cents": row.get("dividend_cents"),
                "share_price_fy_end_aud": row.get("share_price_fy_end"),
                "stores": row.get("stores"),
                "rba_cash_rate_june_pct": macro["rba_cash_rate_pct"].get(year),
                "rba_aud_usd_june": macro["rba_aud_usd"].get(year),
            })


def _file_slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-._") or "instrument"


def market_statistics(market: dict) -> dict:
    """Calculate transparent price-only context from chronological daily bars."""
    bars = market.get("bars", [])
    if not bars:
        return {"status": "unavailable", "reason": "No valid daily closes"}
    closes = [float(bar["close"]) for bar in bars]
    latest = closes[-1]
    changes = {}
    for label, days in (("1_month_pct", 21), ("3_month_pct", 63), ("6_month_pct", 126),
                        ("1_year_pct", 252), ("5_year_pct", 1260), ("10_year_pct", 2520)):
        if len(closes) > days:
            changes[label] = round((latest / closes[-days - 1] - 1) * 100, 2)
    daily_returns = [
        closes[index] / closes[index - 1] - 1
        for index in range(1, len(closes))
        if closes[index - 1] > 0
    ]
    recent_returns = daily_returns[-252:]
    mean = sum(recent_returns) / len(recent_returns) if recent_returns else 0
    variance = sum((value - mean) ** 2 for value in recent_returns) / max(1, len(recent_returns) - 1)
    peak = closes[0]
    max_drawdown = 0.0
    for close in closes:
        peak = max(peak, close)
        max_drawdown = min(max_drawdown, close / peak - 1)
    volumes = [bar.get("volume") for bar in bars if isinstance(bar.get("volume"), int) and bar["volume"] >= 0]
    latest_volume = bars[-1].get("volume")
    return {
        "status": "available",
        "observations": len(bars),
        "from": bars[0]["date"],
        "to": bars[-1]["date"],
        "latest_close": latest,
        "period_change_pct": changes,
        "52_week_low": min(closes[-252:]),
        "52_week_high": max(closes[-252:]),
        "annualized_volatility_pct_1y": round(math.sqrt(variance) * math.sqrt(252) * 100, 2) if recent_returns else None,
        "maximum_close_drawdown_pct_observed": round(max_drawdown * 100, 2),
        "latest_reported_volume": latest_volume,
        "average_reported_volume_20d": round(sum(volumes[-20:]) / len(volumes[-20:]), 2) if volumes else None,
    }


def _market_svg(path: Path, instrument: str, market: dict) -> bool:
    bars = market.get("bars", [])
    if not bars:
        return False
    width, height = 1400, 620
    left, right, top, bottom = 90, 1350, 100, 520
    closes = [float(item["close"]) for item in bars]
    minimum, maximum = min(closes), max(closes)
    span = maximum - minimum or 1
    xstep = (right - left) / max(1, len(bars) - 1)
    points = [
        (left + index * xstep, bottom - (value - minimum) / span * (bottom - top))
        for index, value in enumerate(closes)
    ]
    points_text = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
        f'<text x="{left}" y="42" font-family="Arial" font-size="24" font-weight="700">{escape(instrument)} — daily market history</text>',
        f'<text x="{left}" y="70" font-family="Arial" font-size="13" fill="#444">Yahoo Finance public chart data; unofficial and potentially delayed. Raw close shown in {escape(str(market.get("currency") or "unknown currency"))}.</text>',
        f'<text x="{left}" y="90" font-family="Arial" font-size="12" fill="#555">{escape(bars[0]["date"])} to {escape(bars[-1]["date"])}; {len(bars)} valid observations.</text>',
        f'<line x1="{left}" y1="{bottom}" x2="{right}" y2="{bottom}" stroke="#777"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" stroke="#777"/>',
        f'<polyline points="{points_text}" fill="none" stroke="#2673b8" stroke-width="2.2"/>',
        f'<text x="{left}" y="{bottom+30}" font-family="Arial" font-size="12">{escape(bars[0]["date"])}</text>',
        f'<text x="{right}" y="{bottom+30}" text-anchor="end" font-family="Arial" font-size="12">{escape(bars[-1]["date"])}</text>',
        f'<text x="{left}" y="{top-10}" font-family="Arial" font-size="12">High: {maximum:.4g}</text>',
        f'<text x="{left}" y="{bottom-5}" font-family="Arial" font-size="12">Low: {minimum:.4g}</text>',
        "</svg>",
    ]
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return True


def _chart_svg(path: Path, company: str, history: dict[int, dict], macro: dict) -> bool:
    if not history:
        return False
    years = sorted(history)
    width, height = 1500, 1150
    left, right = 95, 1400
    xstep = (right - left) / max(1, len(years) - 1)
    panels = [
        ("Revenue and NPAT", "AUD millions", lambda r: (r.get("revenue_m"), r.get("npat_m"))),
        ("Net profit margin", "Percent", lambda r: (r.get("npat_m") / r.get("revenue_m") * 100
                                                    if r.get("revenue_m") else None,)),
        ("EPS and dividends paid", "Cents per share", lambda r: (r.get("eps_cents"), r.get("dividend_cents"))),
        ("Share price at financial year-end", "AUD per share", lambda r: (r.get("share_price_fy_end"),)),
        ("Australian macro context", "Cash rate (%) / AUD per USD", None),
    ]
    colors = ("#2673b8", "#c65b29", "#31936b", "#9d3b88")
    top, panel_height, gap = 125, 175, 35
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{left}" y="42" font-family="Arial" font-size="26" font-weight="700">{escape(company)} ({history[years[0]]["fy"]}–{history[years[-1]]["fy"]}): financial and macro history</text>',
        f'<text x="{left}" y="72" font-family="Arial" font-size="14" fill="#444">Audited annual-report figures; financial years ended 30 June. Macro observations are June-end RBA data.</text>',
        f'<text x="{left}" y="95" font-family="Arial" font-size="13" fill="#555">Separate panels use separate units. Price history is not total shareholder return; historical levels are not forecasts.</text>',
    ]
    for panel_index, (title, units, getter) in enumerate(panels):
        y0 = top + panel_index * (panel_height + gap)
        y1 = y0 + panel_height
        parts.append(f'<text x="{left}" y="{y0 - 8}" font-family="Arial" font-size="18" font-weight="700">{title}</text>')
        parts.append(f'<text x="{right}" y="{y0 - 8}" text-anchor="end" font-family="Arial" font-size="12" fill="#555">{units}</text>')
        parts.append(f'<line x1="{left}" y1="{y1}" x2="{right}" y2="{y1}" stroke="#777"/>')
        parts.append(f'<line x1="{left}" y1="{y0}" x2="{left}" y2="{y1}" stroke="#777"/>')
        if panel_index < 4:
            series = [getter(history[year]) for year in years]
            n_series = len(series[0]) if series else 0
            for series_index in range(n_series):
                values = [item[series_index] for item in series]
                valid = [float(value) for value in values if value is not None and math.isfinite(float(value))]
                if not valid:
                    continue
                maximum = max(valid) * 1.12 or 1
                points = []
                for i, value in enumerate(values):
                    if value is None:
                        continue
                    x = left + i * xstep
                    y = y1 - float(value) / maximum * (panel_height - 18)
                    points.append((x, y))
                    if panel_index == 0 and series_index == 0:
                        parts.append(f'<rect x="{x-13:.1f}" y="{y:.1f}" width="26" height="{y1-y:.1f}" fill="{colors[0]}" opacity="0.82"/>')
                    else:
                        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{colors[min(series_index + (1 if panel_index else 0), 3)]}"/>')
                if len(points) > 1:
                    color_index = 1 if panel_index == 0 else series_index + (1 if panel_index == 2 else 0)
                    points_text = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
                    parts.append(f'<polyline points="{points_text}" fill="none" stroke="{colors[min(color_index, 3)]}" stroke-width="2.5"/>')
            legends = {
                0: ("Revenue", "NPAT"),
                1: ("NPAT margin",),
                2: ("EPS", "Dividend"),
                3: ("Share price",),
            }[panel_index]
            for index, label in enumerate(legends):
                color_index = 0 if panel_index == 0 and index == 0 else (
                    1 if panel_index == 0 else index + (1 if panel_index == 2 else 0)
                )
                parts.append(
                    f'<text x="{left + index * 145}" y="{y0 + 17}" font-family="Arial" font-size="12" fill="{colors[min(color_index, 3)]}">{label}</text>'
                )
        else:
            rate_items = [(year, macro["rba_cash_rate_pct"].get(year)) for year in years]
            fx_items = [(year, macro["rba_aud_usd"].get(year)) for year in years]
            for values, max_value, color in (
                (rate_items, max([v for _, v in rate_items if v is not None] or [1]) * 1.12, colors[2]),
                (fx_items, max([v for _, v in fx_items if v is not None] or [1]) * 1.12, colors[3]),
            ):
                points = []
                for year, value in values:
                    if value is None:
                        continue
                    x = left + years.index(year) * xstep
                    y = y1 - float(value) / max_value * (panel_height - 18)
                    points.append((x, y))
                if len(points) > 1:
                    parts.append(
                        f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in points)}" '
                        f'fill="none" stroke="{color}" stroke-width="2.5"/>'
                    )
                for x, y in points:
                    parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>')
            parts.append(f'<text x="{left}" y="{y0 + 17}" font-family="Arial" font-size="12" fill="{colors[2]}">RBA cash-rate target</text>')
            parts.append(f'<text x="{left + 190}" y="{y0 + 17}" font-family="Arial" font-size="12" fill="{colors[3]}">AUD/USD (RBA daily series available from 2023 in this extract)</text>')
        for index, year in enumerate(years):
            if len(years) <= 8 or index % 2 == 0 or index == len(years) - 1:
                x = left + index * xstep
                parts.append(f'<text x="{x:.1f}" y="{y1 + 20}" text-anchor="middle" font-family="Arial" font-size="11" fill="#333">{year}</text>')
    parts.append("</svg>")
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return True


def _markdown_report(
    path: Path, selected: dict, manifest: dict | None, history: dict[int, dict],
    citations: list[dict], market: dict | None, macro: dict, issues: list[dict],
) -> None:
    company = (manifest or {}).get("company") or selected["normalized_instrument"]
    lines = [
        f"# {company} — instrument research",
        "",
        f"- **Instrument:** `{selected['instrument']}`",
        f"- **Method selected:** {selected['method']}",
        f"- **Classification:** {selected['classification']} ({selected['asset_type']})",
        f"- **Generated:** {datetime.now(timezone.utc).isoformat()}",
        "- **Model use:** None; source extraction, calculations, and chart generation are deterministic.",
        "",
        "## Work status",
        "",
        "This report was generated by a bounded Python worker for one instrument. See `status.json` for stage-by-stage progress and issues.",
        "",
    ]
    if selected["warnings"]:
        lines += ["**Classification warning:** " + " ".join(selected["warnings"]), ""]
    profile = (manifest or {}).get("profile")
    if profile:
        lines += ["## Company history and business", "", str(profile), ""]
    actions = (manifest or {}).get("corporate_actions", [])
    if actions:
        lines += ["## Corporate actions, acquisitions, mergers, and splits", ""]
        for action in actions:
            url = action.get("source_url", "")
            label = f"[source]({url})" if url else "source not supplied"
            lines.append(f"- **{action.get('date', 'Date not supplied')} — {action.get('title', 'Corporate event')}:** {action.get('detail', '')} ({label})")
        coverage = (manifest or {}).get("corporate_action_coverage")
        if coverage:
            lines += ["", f"**Coverage limit:** {coverage}"]
        lines.append("")
    if history:
        lines += [
            "## Historical financial performance",
            "",
            "Figures are transcribed by the script from issuer annual-report five-year summary tables; values are AUD millions unless labelled otherwise. No missing field is converted to zero.",
            "",
            "| FY | Revenue | EBITDA | NPAT | NPAT margin | EPS (c) | Dividend/share (c) | FY-end price (A$) | Stores |",
            "|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for year, row in history.items():
            revenue, npat = row.get("revenue_m"), row.get("npat_m")
            margin = npat / revenue * 100 if revenue else None
            values = (
                year, row.get("revenue_m"), row.get("ebitda_m"), row.get("npat_m"),
                f"{margin:.2f}" if margin is not None else None,
                row.get("eps_cents"), row.get("dividend_cents"),
                row.get("share_price_fy_end"), row.get("stores"),
            )
            lines.append("| " + " | ".join("" if value is None else str(value) for value in values) + " |")
        first, last = history[min(history)], history[max(history)]
        span = max(history) - min(history)
        if span > 0:
            for label, field in (("Revenue", "revenue_m"), ("NPAT", "npat_m")):
                start, end = first.get(field), last.get(field)
                cagr = ((end / start) ** (1 / span) - 1) * 100 if start and end and start > 0 and end > 0 else None
                if cagr is not None:
                    lines.append("")
                    lines.append(f"{label} CAGR from FY{min(history)} to FY{max(history)}: **{cagr:.1f}%** (calculated from rounded annual-report values).")
        lines += ["", f"![{company} historical performance chart](./{_file_slug(selected['source_symbol'])}-history.svg)", ""]
        lines += ["### Annual-report sources", ""]
        for source in citations:
            lines.append(f"- [{source['label']}]({source['url']})")
        lines += ["", "The chart is a single multi-panel view with consistent financial-year alignment. Each panel has its own units and scale; the share-price line excludes dividends and should not be read as total shareholder return.", ""]
    else:
        lines += [
            "## Financial history",
            "",
            "No issuer annual-report sources are configured for this instrument. Company fundamentals, full corporate-action history, and merger/split completeness are therefore **unavailable**, not zero.",
            "",
        ]
    if market:
        bars = market.get("bars", [])
        latest = bars[-1] if bars else None
        stats = market_statistics(market)
        lines += ["## Market data and event coverage", ""]
        lines.append(f"- **Source:** {market.get('source')}")
        lines.append(f"- **Retrieved:** {market.get('retrieved_at')}")
        lines.append(f"- **Currency / exchange timezone:** {market.get('currency')} / {market.get('exchange_timezone')}")
        lines.append(f"- **Bars:** {len(bars)} daily observations in the requested ten-year window")
        if latest:
            lines.append(f"- **Latest observed close:** {latest['close']} on {latest['date']} (raw close; not adjusted)")
        lines.append(f"- **Calculated price context:** `{json.dumps(stats, sort_keys=True)}`")
        split_events = market.get("events", {}).get("splits", {})
        dividend_events = market.get("events", {}).get("dividends", {})
        lines.append(f"- **Yahoo corporate-action events returned in this ten-year window:** {len(split_events)} split(s), {len(dividend_events)} dividend(s); this feed is unofficial and is not a full merger/scheme register.")
        if market.get("bars"):
            lines += ["", f"![{selected['instrument']} daily market history](./market-history.svg)"]
        lines.append("")
    if history:
        lines += ["## Australian macro reference", ""]
        for year in sorted(history):
            rate = macro["rba_cash_rate_pct"].get(year)
            fx = macro["rba_aud_usd"].get(year)
            if rate is not None or fx is not None:
                lines.append(f"- FY{year}: RBA cash-rate target at financial-year end {rate if rate is not None else 'unavailable'}%; AUD/USD {fx if fx is not None else 'unavailable'} (RBA daily series available from 2023 in this extraction).")
        lines += [
            "",
            "Macro observations are contextual, not causal proof. The F11.1 AUD/USD series in this report begins in 2023; earlier values are left blank.",
            "- [RBA Statistical Table F1 — cash-rate target](https://www.rba.gov.au/statistics/cash-rate/)",
            "- [RBA Statistical Table F11.1 — daily exchange rates](https://www.rba.gov.au/statistics/tables/)",
            "",
        ]
    if issues:
        lines += ["## Issues and limitations", ""]
        lines += [f"- **{issue['severity'].upper()}:** {issue['message']}" for issue in issues]
        lines.append("")
    lines += [
        "## Decision boundary",
        "",
        "This is research context, not a buy/sell instruction or personalized financial advice. A potential entry range or target requires validated current pricing, a reviewed valuation method, and explicit assumptions; missing evidence is not filled by the model.",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def run_research(
    instrument: str,
    asset_type: str,
    job_dir: Path,
    data_dir: Path,
    allow_network: bool,
) -> int:
    job_dir.mkdir(parents=True, exist_ok=True)
    progress = Progress(job_dir, instrument)
    try:
        selected = select_method(instrument, asset_type)
        progress.payload["method"] = selected
        progress.update("running", "method_selected", 10, f"Selected: {selected['method']}")
        for warning in selected["warnings"]:
            progress.issue(warning)

        manifest = None
        history = {}
        citations = []
        if selected["asset_type"] == "asx_equity":
            manifest = _load_company_manifest(data_dir, selected["normalized_instrument"])
            if manifest:
                history, citations = _annual_report_data(manifest, progress)
            else:
                progress.issue(
                    f"No issuer-source manifest found for {selected['normalized_instrument']}; "
                    "annual financial history and complete corporate-action research are unavailable."
                )

        market = None
        if allow_network:
            progress.update("running", "market_data", 68, f"Fetching public price history for {selected['source_symbol']}")
            try:
                market = _fetch_yahoo_history(selected["source_symbol"])
                if market["status"] != "ok":
                    progress.issue("The market-data provider returned no usable daily price history.")
            except ResearchError as exc:
                progress.issue(f"Market data unavailable: {exc}")
        else:
            progress.issue("External feeds are disabled; no live price history was requested.")

        years = sorted(history)
        macro = _macro_series(years, progress, allow_network) if years else {
            "rba_cash_rate_pct": {}, "rba_aud_usd": {}, "sources": [],
        }
        if history:
            history_path = job_dir / f"{_file_slug(selected['source_symbol'])}-history.csv"
            _write_history_csv(history_path, history, macro)
            chart_path = history_path.with_suffix(".svg")
            _chart_svg(chart_path, (manifest or {}).get("company") or selected["normalized_instrument"], history, macro)
        if market and market.get("bars"):
            _market_svg(job_dir / "market-history.svg", selected["instrument"], market)

        report_path = job_dir / "report.md"
        _markdown_report(report_path, selected, manifest, history, citations, market, macro, progress.payload["issues"])
        if market:
            _atomic_json(job_dir / "market-data.json", {
                "instrument": selected["instrument"],
                "symbol": selected["source_symbol"],
                "source": market["source"],
                "retrieved_at": market["retrieved_at"],
                "currency": market.get("currency"),
                "statistics": market_statistics(market),
                "bars": market["bars"],
                "events": market["events"],
            })
        progress.payload["report"] = report_path.name
        progress.update("completed", "complete", 100, "Report, progress log, and available charts are ready")
        return 0
    except (ResearchError, OSError, ValueError, KeyError, TypeError) as exc:
        progress.issue(str(exc), severity="error")
        progress.update("failed", "failed", progress.payload["progress_percent"], "Research worker could not complete")
        print(f"Research failed: {exc}", file=sys.stderr)
        return 1


def load_instruments_file(path: Path) -> list[dict]:
    """Load tickers from JSON or CSV without importing portfolio values."""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ResearchError(f"Could not read instrument list: {path}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        try:
            rows = list(csv.DictReader(io.StringIO(raw)))
        except csv.Error as exc:
            raise ResearchError("Instrument list must be valid JSON or CSV") from exc
        if not rows:
            raise ResearchError("Instrument CSV has no data rows")
        results = []
        for row in rows:
            symbol = row.get("symbol") or row.get("Symbol") or row.get("code") or row.get("Code")
            if symbol and symbol.strip():
                results.append({"instrument": symbol.strip(), "asset_type": "auto"})
        return _deduplicate_batch_items(results)
    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        if isinstance(payload.get("instruments"), list):
            items = payload["instruments"]
        elif isinstance(payload.get("lists"), dict):
            items = [
                {"instrument": symbol, "asset_type": "auto", "list_name": name}
                for name, symbols in payload["lists"].items()
                if isinstance(symbols, list)
                for symbol in symbols
            ]
        else:
            raise ResearchError("JSON instrument list must contain an 'instruments' array or named 'lists'")
    else:
        raise ResearchError("JSON instrument list must be an array or object")
    normalized = []
    for item in items:
        if isinstance(item, str):
            normalized.append({"instrument": item, "asset_type": "auto"})
        elif isinstance(item, dict) and isinstance(item.get("instrument") or item.get("symbol"), str):
            normalized.append({
                "instrument": item.get("instrument") or item.get("symbol"),
                "asset_type": item.get("asset_type", "auto"),
                "list_name": item.get("list_name"),
                "list_names": item.get("list_names", []),
            })
        else:
            normalized.append({"instrument": str(item), "asset_type": "auto"})
    return _deduplicate_batch_items(normalized)


def _deduplicate_batch_items(items: list[dict]) -> list[dict]:
    results = []
    seen = {}
    for item in items:
        instrument = str(item.get("instrument", "")).strip()
        key = instrument.upper()
        if not key:
            continue
        if key in seen:
            names = [*item.get("list_names", [])]
            if item.get("list_name"):
                names.append(item["list_name"])
            memberships = results[seen[key]]["list_names"]
            memberships.extend(name for name in names if name and name not in memberships)
            continue
        seen[key] = len(results)
        names = [*item.get("list_names", [])]
        if item.get("list_name"):
            names.append(item["list_name"])
        results.append({
            "instrument": instrument,
            "asset_type": item.get("asset_type", "auto"),
            "list_names": list(dict.fromkeys(names)),
        })
    if not results:
        raise ResearchError("No instrument symbols were found in the list")
    if len(results) > 5000:
        raise ResearchError("A research batch is limited to 5,000 unique instruments")
    return results


def _write_batch_summary(batch_dir: Path, status: dict) -> None:
    lines = [
        "# ASX research batch summary",
        "",
        f"- **Status:** {status['status']}",
        f"- **Completed / failed:** {status['completed']} / {status['failed']} of {status['total']}",
        f"- **Local model:** {status['model']}",
        f"- **External feeds enabled:** {status['external_feeds_enabled']}",
        f"- **Finished:** {status.get('finished_at')}",
        "",
        "Price history is from Yahoo Finance's unofficial public chart endpoint and can be delayed. "
        "Local-model analyses are drafts, not independently verified. These outputs are research "
        "material, not personalized financial advice or buy/sell instructions.",
        "",
        "| Instrument | List | Latest close (date) | 1Y change | 5Y change | 10Y change | Issuer financial history | Result | Files |",
        "|---|---|---:|---:|---:|---:|---|---|---|",
    ]
    for item in status["items"]:
        folder = item.get("job_directory", "")
        market_path = batch_dir / folder / "market-data.json"
        close, close_date, change_1y, change_5y, change_10y = "Unavailable", "", "Unavailable", "Unavailable", "Unavailable"
        try:
            market = json.loads(market_path.read_text(encoding="utf-8"))
            stats = market.get("statistics", {})
            close = f"{stats['latest_close']:.4g}" if isinstance(stats.get("latest_close"), (int, float)) else "Unavailable"
            close_date = stats.get("to", "")
            changes = stats.get("period_change_pct", {})
            if "1_year_pct" in changes:
                change_1y = f"{changes['1_year_pct']}%"
            if "5_year_pct" in changes:
                change_5y = f"{changes['5_year_pct']}%"
            if "10_year_pct" in changes:
                change_10y = f"{changes['10_year_pct']}%"
        except (OSError, json.JSONDecodeError):
            pass
        report_path = batch_dir / folder / "report.md"
        issuer_history = "Unavailable"
        try:
            report_text = report_path.read_text(encoding="utf-8")
            if "## Historical financial performance" in report_text:
                issuer_history = "Report data available"
        except OSError:
            pass
        files = []
        if item.get("report"):
            files.append(f"[data](./{item['report']})")
        if item.get("analysis"):
            files.append(f"[Llama analysis](./{item['analysis']})")
        details = f"{item['status']}"
        if item.get("error"):
            details += ": " + item["error"].replace("|", "\\|")
        lines.append(
            f"| {item['instrument']} | {', '.join(item.get('list_names', [])) or 'Manual'} "
            f"| {close} ({close_date}) | {change_1y} | {change_5y} | {change_10y} | {issuer_history} "
            f"| {details} | {' · '.join(files) or 'No output'} |"
        )
    lines += [
        "",
        "",
        "## One-year comparison",
        "",
        "![One-year adjusted-close return comparison for all batch instruments](./comparison.svg)",
        "",
        "[Download the comparison data](./comparison.csv).",
        "",
        "Small-cap issuer financials and complete corporate-action coverage are unavailable unless "
        "an issuer-source manifest was configured and its reports were successfully parsed. "
        "Unavailability is not zero and is not evidence that no event occurred.",
        "",
    ]
    (batch_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def write_comparison_chart(batch_dir: Path) -> dict:
    """Create a one-year adjusted-close return comparison for every batch item."""
    status_path = batch_dir / "status.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResearchError("Cannot build comparison: batch status is unavailable or unreadable") from exc

    results = []
    for item in status.get("items", []):
        symbol = item.get("instrument", "unknown")
        row = {
            "instrument": symbol,
            "list_names": item.get("list_names", []),
            "status": "unavailable",
            "period_start": None,
            "latest_date": None,
            "latest_close": None,
            "return_pct": None,
            "price_basis": None,
            "observations": 0,
            "note": "No usable market history was saved",
        }
        folder = item.get("job_directory", "")
        if not re.fullmatch(r"\d{2,3}-[A-Za-z0-9._-]+", folder):
            results.append(row)
            continue
        try:
            market = json.loads((batch_dir / folder / "market-data.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            results.append(row)
            continue
        bars = [
            bar for bar in market.get("bars", [])
            if isinstance(bar.get("date"), str)
            and isinstance(bar.get("close"), (int, float))
            and math.isfinite(bar["close"])
            and bar["close"] > 0
        ]
        bars.sort(key=lambda bar: bar["date"])
        if not bars:
            row["note"] = "No valid daily closes"
            results.append(row)
            continue
        adjusted = all(
            isinstance(bar.get("adjusted_close"), (int, float))
            and math.isfinite(bar["adjusted_close"])
            and bar["adjusted_close"] > 0
            for bar in bars
        )
        field = "adjusted_close" if adjusted else "close"
        latest = bars[-1]
        latest_date = date.fromisoformat(latest["date"])
        cutoff = latest_date - timedelta(days=365)
        baseline = next((bar for bar in reversed(bars) if date.fromisoformat(bar["date"]) <= cutoff), None)
        row.update({
            "latest_date": latest["date"],
            "latest_close": latest[field],
            "price_basis": "Yahoo adjusted close (dividend/split adjusted)" if adjusted else "Yahoo raw close (not dividend adjusted)",
            "observations": len(bars),
        })
        if baseline:
            baseline_value = baseline[field]
            row.update({
                "status": "available",
                "period_start": baseline["date"],
                "return_pct": round((latest[field] / baseline_value - 1) * 100, 2),
                "note": "",
            })
        else:
            row["note"] = f"Less than one year of usable history; oldest observation {bars[0]['date']}"
        results.append(row)

    available = [row for row in results if row["status"] == "available"]
    unavailable = len(results) - len(available)
    if available:
        available.sort(key=lambda row: row["return_pct"], reverse=True)
    ordered = available + [row for row in results if row["status"] != "available"]
    csv_path = batch_dir / "comparison.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fields = (
            "instrument", "list_names", "status", "period_start", "latest_date",
            "latest_close", "return_pct", "price_basis", "observations", "note",
        )
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in ordered:
            writer.writerow({
                **row,
                "list_names": "; ".join(row["list_names"]),
            })

    width = 1500
    row_height = 30
    top, bottom = 135, 85
    height = top + row_height * len(ordered) + bottom
    left, right = 320, 1370
    returns = [row["return_pct"] for row in available]
    minimum = min([-100.0, *returns]) if returns else -100.0
    maximum = max([100.0, *returns]) if returns else 100.0
    padding = max(10.0, (maximum - minimum) * 0.06)
    minimum -= padding
    maximum += padding
    scale = (right - left) / (maximum - minimum or 1)
    zero_x = left + (0 - minimum) * scale
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#fff"/>',
        '<text x="55" y="40" font-family="Arial" font-size="26" font-weight="700">ASX watchlist + holdings: one-year performance comparison</text>',
        f'<text x="55" y="70" font-family="Arial" font-size="14" fill="#444">Adjusted close return where available; same 365-day window per instrument. {len(available)} with a full year; {unavailable} unavailable.</text>',
        '<text x="55" y="94" font-family="Arial" font-size="13" fill="#555">Yahoo Finance public data is unofficial and may be delayed. Share-price performance is not a forecast or investment recommendation.</text>',
    ]
    ticks = 6
    for index in range(ticks + 1):
        value = minimum + (maximum - minimum) * index / ticks
        x = left + index / ticks * (right - left)
        parts.append(f'<line x1="{x:.1f}" y1="{top-14}" x2="{x:.1f}" y2="{top + row_height * len(ordered)}" stroke="#ddd"/>')
        parts.append(f'<text x="{x:.1f}" y="{top-24}" text-anchor="middle" font-family="Arial" font-size="12" fill="#333">{value:.0f}%</text>')
    for index, row in enumerate(ordered):
        y = top + index * row_height
        group = " + ".join(row["list_names"])
        label = f"{row['instrument']}  ({group})" if group else row["instrument"]
        parts.append(f'<text x="{left-12}" y="{y+17}" text-anchor="end" font-family="Arial" font-size="12">{escape(label)}</text>')
        if row["status"] != "available":
            parts.append(f'<text x="{zero_x:.1f}" y="{y+17}" font-family="Arial" font-size="12" fill="#666">Unavailable — {escape(row["note"])}</text>')
            continue
        value = row["return_pct"]
        end_x = left + (value - minimum) * scale
        bar_x, bar_width = min(zero_x, end_x), abs(end_x - zero_x)
        color = "#287a49" if value >= 0 else "#b54735"
        parts.append(f'<rect x="{bar_x:.1f}" y="{y+3}" width="{max(1.0, bar_width):.1f}" height="20" rx="2" fill="{color}"/>')
        label_x = end_x + 6 if value >= 0 else end_x - 6
        anchor = "start" if value >= 0 else "end"
        parts.append(f'<text x="{label_x:.1f}" y="{y+18}" text-anchor="{anchor}" font-family="Arial" font-size="12" fill="#222">{value:+.1f}%</text>')
    parts += [
        f'<line x1="{zero_x:.1f}" y1="{top-16}" x2="{zero_x:.1f}" y2="{top + row_height * len(ordered)}" stroke="#333" stroke-width="2"/>',
        f'<text x="{left}" y="{height-30}" font-family="Arial" font-size="12" fill="#444">Period starts at the last available close on or before {max((row["period_start"] or "" for row in available), default="")}; each ticker has its own exact start date in comparison.csv.</text>',
        '</svg>',
    ]
    svg_path = batch_dir / "comparison.svg"
    svg_path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    markdown = [
        "# ASX watchlist and holdings comparison",
        "",
        f"**One-year return calculated for {len(available)} of {len(results)} instruments; "
        f"{unavailable} do not have a full year of valid history.**",
        "",
        "![One-year adjusted-close return comparison for all batch instruments](./comparison.svg)",
        "",
        "[Download the ticker-level comparison CSV](./comparison.csv).",
        "",
        "Returns use adjusted closes (dividend/split adjusted) where the provider supplied a complete "
        "series for that instrument; raw closes are used and labeled otherwise. Each ticker uses its "
        "own last available close on or before the 365-day cutoff. Exact dates and data basis are in "
        "the CSV. Yahoo Finance's public chart data is unofficial and may be delayed. Past returns "
        "are not forecasts, fair value, or trade recommendations.",
        "",
        "| Instrument | Watchlist/holdings | One-year change | Period | Basis / status |",
        "|---|---|---:|---|---|",
    ]
    for row in ordered:
        returned = f"{row['return_pct']:+.2f}%" if row["return_pct"] is not None else "Unavailable"
        period = f"{row['period_start']} to {row['latest_date']}" if row["period_start"] else "—"
        basis = row["price_basis"] if row["status"] == "available" else row["note"]
        markdown.append(
            f"| {row['instrument']} | {', '.join(row['list_names']) or 'Manual'} "
            f"| {returned} | {period} | {basis} |"
        )
    (batch_dir / "comparison.md").write_text("\n".join(markdown) + "\n", encoding="utf-8")
    return {
        "chart": svg_path.name,
        "csv": csv_path.name,
        "markdown": "comparison.md",
        "instruments": len(results),
        "available": len(available),
        "unavailable": unavailable,
        "period_days": 365,
    }


async def run_batch(
    items: list[dict],
    batch_dir: Path,
    data_dir: Path,
    *,
    allow_network: bool,
    model: str,
    analyze: Callable[[str, str, str], Awaitable[tuple[str, str]]],
    resume: bool = False,
    reanalyze_completed: bool = False,
    on_item_complete: Callable[[dict, dict], None] | None = None,
) -> dict:
    """Run every unique instrument sequentially; keep terminal feedback per item."""
    batch_dir.mkdir(parents=True, exist_ok=True)
    normalized = _deduplicate_batch_items(items)
    now = datetime.now(timezone.utc).isoformat()
    existing = None
    status_path = batch_dir / "status.json"
    if resume and status_path.exists():
        try:
            existing = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ResearchError("Cannot resume because the saved batch status is unreadable") from exc
        old_symbols = [str(item.get("instrument", "")).upper() for item in existing.get("items", [])]
        new_symbols = [item["instrument"].upper() for item in normalized]
        if old_symbols != new_symbols:
            raise ResearchError("Cannot resume: the saved batch instrument list does not match")
    status = existing or {
        "status": "running",
        "stage": "starting",
        "progress_percent": 0,
        "model": model,
        "external_feeds_enabled": allow_network,
        "started_at": now,
        "updated_at": now,
        "finished_at": None,
        "total": len(normalized),
        "completed": 0,
        "failed": 0,
        "current_instrument": None,
        "items": [],
    }
    if not existing:
        for index, item in enumerate(normalized, 1):
            slug = _file_slug(item["instrument"])
            status["items"].append({
                "index": index,
                "instrument": item["instrument"],
                "asset_type": item.get("asset_type", "auto"),
                "list_names": item.get("list_names", []),
                "status": "queued",
                "stage": "queued",
                "job_directory": f"{index:02d}-{slug}",
                "report": None,
                "analysis": None,
                "error": None,
            })
    if existing:
        status["status"] = "running"
        status["stage"] = "resuming"
        status["model"] = model
        status["external_feeds_enabled"] = allow_network
        status["finished_at"] = None
        status["failed"] = 0
        for item_status in status["items"]:
            if item_status.get("status") != "completed":
                item_status.update({
                    "status": "queued", "stage": "queued", "error": None,
                    "finished_at": None,
                })
        status["completed"] = 0
        for item_status in status["items"]:
            was_completed = item_status.get("status") == "completed"
            analyze_only = bool(
                item_status.get("report")
                and (was_completed and reanalyze_completed or item_status.get("status") == "failed")
            )
            if was_completed and not reanalyze_completed:
                status["completed"] += 1
                continue
            item_status["analysis_only"] = analyze_only
            item_status.update({"status": "queued", "stage": "queued", "error": None, "finished_at": None})

    def persist() -> None:
        status["updated_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_json(batch_dir / "status.json", status)

    persist()
    for index, item_status in enumerate(status["items"], 1):
        if item_status["status"] == "completed":
            continue
        item_status["status"] = "running"
        item_status["stage"] = "research"
        status["stage"] = "research"
        status["current_instrument"] = item_status["instrument"]
        status["progress_percent"] = round((index - 1) / len(normalized) * 100)
        persist()
        item_dir = batch_dir / item_status["job_directory"]
        try:
            if not item_status.pop("analysis_only", False):
                result = await asyncio.to_thread(
                    run_research,
                    item_status["instrument"],
                    item_status["asset_type"],
                    item_dir,
                    data_dir,
                    allow_network,
                )
                if result != 0:
                    details = json.loads((item_dir / "status.json").read_text(encoding="utf-8"))
                    errors = [issue["message"] for issue in details.get("issues", []) if issue.get("severity") == "error"]
                    raise ResearchError("; ".join(errors) or "The data research worker failed")
            item_status["stage"] = "local_model_analysis"
            status["stage"] = "local_model_analysis"
            report_path = item_dir / "report.md"
            item_status["report"] = f"{item_status['job_directory']}/report.md"
            persist()
            report = report_path.read_text(encoding="utf-8")
            analysis, actual_model = await analyze(item_status["instrument"], report, model)
            analysis_path = item_dir / "analysis.md"
            if analysis_path.exists():
                previous = item_dir / "analysis-previous.md"
                if not previous.exists():
                    analysis_path.replace(previous)
            analysis_path.write_text(
                f"# Smith's evidence review — {item_status['instrument']}\n\n"
                "**Unverified local-model draft. Check each claim against the cited report before using it.**\n\n"
                f"- **Model:** {actual_model}\n"
                f"- **Generated:** {datetime.now(timezone.utc).isoformat()}\n"
                f"- **Source packet:** `report.md`; see it for source dates and limitations.\n\n"
                f"{analysis.strip()}\n",
                encoding="utf-8",
            )
            item_status["status"] = "completed"
            item_status["stage"] = "complete"
            item_status["report"] = f"{item_status['job_directory']}/report.md"
            item_status["analysis"] = f"{item_status['job_directory']}/analysis.md"
            status["completed"] += 1
        except Exception as exc:
            item_status["status"] = "failed"
            item_status["stage"] = "failed"
            item_status["error"] = f"{type(exc).__name__}: {exc}"
            status["failed"] += 1
            logger.exception("Instrument research batch item failed: %s", item_status["instrument"])
        finally:
            item_status["finished_at"] = datetime.now(timezone.utc).isoformat()
            status["progress_percent"] = round(index / len(normalized) * 100)
            persist()
            if on_item_complete:
                on_item_complete(item_status, status)
    status["status"] = "completed" if status["failed"] == 0 else "completed_with_errors"
    status["stage"] = "complete"
    status["current_instrument"] = None
    status["finished_at"] = datetime.now(timezone.utc).isoformat()
    persist()
    try:
        status["comparison"] = write_comparison_chart(batch_dir)
        _write_batch_summary(batch_dir, status)
        status["summary"] = "summary.md"
    except OSError:
        logger.exception("Could not write research batch summary")
        status["summary_error"] = "Batch finished, but the Markdown summary could not be written"
    persist()
    return status


async def _ollama_generate(client: httpx.AsyncClient, model: str, instrument: str, report: str) -> tuple[str, str]:
    instructions = (
        "You are a careful Australian equity research analyst. Use only facts explicitly present "
        "in the supplied report; do not rely on prior model knowledge. Every company-specific "
        "factual statement must identify the exact report section and quote or figure supporting "
        "it. If no direct support exists, omit the claim or mark it 'not established by this packet'. "
        "Never invent financial statements, company facts, customers, suppliers, market share, "
        "industry conditions, catalysts, corporate actions, prices, citations, or valuation inputs. "
        "A general possibility is not evidence that it applies to this company. Distinguish reported "
        "facts, calculations, interpretation, and unknowns. Do not describe a price trend without "
        "stating the measured period and return from the report. Assess business quality and "
        "potential opportunity versus downside only to the extent supported by evidence. If the "
        "packet lacks fundamental or valuation evidence, say there is insufficient evidence to "
        "assess fair value or an investment opportunity. Do not tell the user to buy or sell or "
        "provide personalized financial advice. Do not calculate a fair value, price target, or "
        "entry range without sufficient cited financial data and explicit assumptions; list what "
        "is needed instead. Make uncertainty prominent. Format "
        "with: Executive summary; Evidence-based business/financial picture; Price and trend "
        "context; Potential opportunity case; Counter-case and risks; What would change the view; "
        "Missing evidence and next research steps; Sources/coverage limits."
    )
    prompt = json.dumps({
        "task": f"Review the evidence packet for {instrument}.",
        "evidence_packet": report,
    })
    response = await client.post("/api/chat", json={
        "model": model,
        "stream": False,
        "keep_alive": "5m",
        "options": {"num_ctx": 16384, "num_predict": 3072},
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": prompt},
        ],
    })
    response.raise_for_status()
    result = response.json()
    answer = result.get("message", {}).get("content")
    if not isinstance(answer, str) or not answer.strip():
        raise ResearchError(f"Local model {model} returned an empty analysis for {instrument}")
    return answer.strip(), model


async def _run_cli_batch(args, instruments: list[dict]) -> int:
    base_url = args.ollama_url.rstrip("/")
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=300) as client:
            tags_response = await client.get("/api/tags")
            tags_response.raise_for_status()
            model_names = {item.get("name") for item in tags_response.json().get("models", [])}
            if args.model not in model_names:
                raise ResearchError(
                    f"Local model {args.model!r} is not installed. Installed models: "
                    + ", ".join(sorted(name for name in model_names if isinstance(name, str)))
                )
            async def analyze(instrument: str, report: str, model: str) -> tuple[str, str]:
                return await _ollama_generate(client, model, instrument, report)
            def progress(item: dict, batch: dict) -> None:
                detail = f": {item['error']}" if item.get("error") else ""
                print(
                    f"[{item['index']}/{batch['total']}] {item['instrument']}: "
                    f"{item['status']}{detail}",
                    flush=True,
                )
            result = await run_batch(
                instruments, args.batch_dir.resolve(), args.data_dir.resolve(),
                allow_network=args.allow_network, model=args.model, analyze=analyze,
                resume=args.resume,
                reanalyze_completed=args.reanalyze_completed,
                on_item_complete=progress,
            )
    except (httpx.HTTPError, ResearchError, OSError, ValueError, KeyError) as exc:
        print(f"Batch could not start: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "completed" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--instrument", help="One instrument, e.g. ASX:NCK, XAUUSD, EUR/USD, BTC-USD")
    parser.add_argument("--asset-type", choices=ASSET_TYPES, default="auto")
    parser.add_argument("--job-dir", type=Path, default=Path("data/research_jobs/manual"))
    parser.add_argument("--batch-file", type=Path, help="Run each unique symbol in a JSON or CSV list sequentially")
    parser.add_argument("--batch-dir", type=Path, default=Path("data/research_jobs/manual-batch"))
    parser.add_argument("--resume", action="store_true", help="Resume a saved batch directory; completed instruments are skipped")
    parser.add_argument("--reanalyze-completed", action="store_true", help="Re-run Llama analysis on existing completed reports (requires --resume)")
    parser.add_argument("--model", default=os.environ.get("MR_SMITH_RESEARCH_BATCH_MODEL", "llama3.2:latest"))
    parser.add_argument("--ollama-url", default=os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434"))
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--allow-network", action="store_true", help="Allow fixed public Yahoo and RBA data sources")
    args = parser.parse_args(argv)
    if args.batch_file:
        if args.reanalyze_completed and not args.resume:
            parser.error("--reanalyze-completed requires --resume")
        try:
            instruments = load_instruments_file(args.batch_file)
        except ResearchError as exc:
            parser.error(str(exc))
        return asyncio.run(_run_cli_batch(args, instruments))
    if not args.instrument:
        parser.error("--instrument is required unless --batch-file is supplied")
    return run_research(
        args.instrument, args.asset_type, args.job_dir.resolve(),
        args.data_dir.resolve(), args.allow_network,
    )


if __name__ == "__main__":
    raise SystemExit(main())
