"""Finance API: /api/finance/* endpoints.

Every private read/write/context path here requires the existing admin
Bearer-token dependency used by the rest of the app. The router is built with
the same settings/store/admin dependency the main app already constructed, so
there is exactly one auth mechanism and no embedded/alternate token.
"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import asyncio
import csv
import io
import json
import logging
import re
from pathlib import Path
import threading
import uuid

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field, ValidationError

from assistant_api.finance import calculators, market_data
from assistant_api.finance import macro_data
from assistant_api.finance import portfolio as portfolio_calc
from assistant_api.finance import research as instrument_research
from assistant_api.finance.evidence import (
    content_hash, extract_pdf_text, passages_with_page_numbers, plain_text_passages,
)
from assistant_api.finance.repository import FinanceStore
from assistant_api.finance.schemas import (
    CatalystIn, DCFRequest, DCFSensitivityRequest, EvidenceDocumentIn, HoldingRow,
    PdfEvidenceMeta, RatioValuationRequest, Security, ShockIn, StatementFigureIn,
    ThesisRevisionIn, WatchlistIn,
)
from assistant_api.finance.instruments import (
    MACRO_SOURCE_CATALOG, XAUUSD_SYMBOL, GoldMacroEvidenceIn, InstrumentIn,
    RiskCalculationIn, TradingJournalCloseIn, TradingJournalIn,
)

logger = logging.getLogger(__name__)

SCALE_MULTIPLIER = {"units": Decimal(1), "thousands": Decimal(1000), "millions": Decimal(1_000_000)}
ACTIVE_RESEARCH_JOBS: set[str] = set()
ACTIVE_RESEARCH_JOBS_LOCK = threading.Lock()
MONETARY_FIGURE_FIELDS = (
    "cash_and_equivalents", "total_debt", "revenue", "ebitda", "net_income",
    "operating_cash_flow", "capital_expenditure", "dividends_paid",
)


class InstrumentResearchRequest(BaseModel):
    instrument: str = Field(min_length=1, max_length=40)
    asset_type: str = "auto"


class InstrumentResearchBatchRequest(BaseModel):
    instruments: list[InstrumentResearchRequest] = Field(min_length=1, max_length=50)
    model: str = Field(default="llama3.2:latest", min_length=1, max_length=200,
                       pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


class InstrumentResearchBatchResumeRequest(BaseModel):
    model: str | None = Field(default=None, min_length=1, max_length=200,
                              pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


class _LazyStoreProxy:
    """Forwards attribute access to a store created after router construction.

    The finance router is wired into the FastAPI app before the lifespan
    context (which creates the real FinanceStore) has run, so every call must
    resolve the store lazily, at request time.
    """

    def __init__(self, get_store):
        self._get_store = get_store

    def __getattr__(self, name):
        return getattr(self._get_store(), name)


def build_router(*, get_store, admin_dependency, feeds_client_getter,
                 external_feeds_enabled=lambda: False,
                 data_dir: Path | None = None,
                 model_status_getter=None,
                 model_analysis_generator=None) -> APIRouter:
    store: FinanceStore = _LazyStoreProxy(get_store)
    research_data_dir = Path(data_dir or "data").resolve()
    router = APIRouter(prefix="/api/finance", tags=["finance"], dependencies=[Depends(admin_dependency)])

    def run_instrument_research(job_id: str, instrument: str, asset_type: str, job_dir: Path) -> None:
        try:
            instrument_research.run_research(
                instrument, asset_type, job_dir, research_data_dir,
                allow_network=bool(external_feeds_enabled()),
            )
        finally:
            with ACTIVE_RESEARCH_JOBS_LOCK:
                ACTIVE_RESEARCH_JOBS.discard(job_id)

    async def run_instrument_batch(
        batch_id: str, instruments: list[dict], batch_dir: Path, model: str, *, resume: bool = False,
    ) -> None:
        try:
            if model_analysis_generator is None:
                raise RuntimeError("Local model analysis is not configured for this application")
            await instrument_research.run_batch(
                instruments, batch_dir, research_data_dir,
                allow_network=bool(external_feeds_enabled()),
                model=model,
                analyze=model_analysis_generator,
                resume=resume,
            )
        except Exception as exc:
            logger.exception("Instrument research batch could not complete")
            status_path = batch_dir / "status.json"
            try:
                status = json.loads(status_path.read_text(encoding="utf-8"))
                status.update({
                    "status": "failed",
                    "stage": "failed",
                    "current_instrument": None,
                    "fatal_error": f"{type(exc).__name__}: {exc}",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                })
                instrument_research._atomic_json(status_path, status)
            except (OSError, json.JSONDecodeError):
                logger.exception("Could not persist failed batch status")
        finally:
            with ACTIVE_RESEARCH_JOBS_LOCK:
                ACTIVE_RESEARCH_JOBS.discard(batch_id)

    @router.get("/research-batches/seed")
    async def get_research_batch_seed():
        seed_path = research_data_dir / "research_lists" / "watchlist_and_holdings.json"
        try:
            seed = json.loads(seed_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise HTTPException(404, "No local research list has been prepared") from exc
        except (OSError, json.JSONDecodeError) as exc:
            logger.exception("Could not load local research list")
            raise HTTPException(500, "Local research list is unreadable") from exc
        return seed

    @router.post("/research-batches", status_code=202)
    async def start_instrument_research_batch(
        body: InstrumentResearchBatchRequest,
        background_tasks: BackgroundTasks,
    ):
        unique_items = []
        seen = set()
        for item in body.instruments:
            try:
                instrument = instrument_research.select_method(item.instrument, item.asset_type)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            key = instrument["normalized_instrument"].upper()
            if key not in seen:
                unique_items.append(item.model_dump())
                seen.add(key)
        if model_status_getter is None or model_analysis_generator is None:
            raise HTTPException(503, "Local model analysis is not configured")
        try:
            model_status = await model_status_getter()
        except Exception as exc:
            logger.warning("Local model preflight failed: %s", type(exc).__name__)
            raise HTTPException(503, "Could not check the local model service; batch was not started") from exc
        installed = model_status.get("installed_models", [])
        if body.model not in installed:
            raise HTTPException(
                422,
                f"Local model {body.model!r} is not installed; choose an installed model from /api/models",
            )
        with ACTIVE_RESEARCH_JOBS_LOCK:
            if ACTIVE_RESEARCH_JOBS:
                raise HTTPException(429, "Only one instrument research job or batch may run at a time")
            batch_id = uuid.uuid4().hex
            ACTIVE_RESEARCH_JOBS.add(batch_id)
        batch_dir = research_data_dir / "research_batches" / batch_id
        now = datetime.now(timezone.utc).isoformat()
        try:
            batch_dir.mkdir(parents=True, exist_ok=False)
            instrument_research._atomic_json(batch_dir / "status.json", {
                "status": "queued",
                "stage": "queued",
                "progress_percent": 0,
                "model": body.model,
                "external_feeds_enabled": bool(external_feeds_enabled()),
                "started_at": now,
                "updated_at": now,
                "finished_at": None,
                "total": len(unique_items),
                "completed": 0,
                "failed": 0,
                "current_instrument": None,
                "items": [
                    {"index": i, "instrument": item["instrument"], "status": "queued",
                     "asset_type": item.get("asset_type", "auto"),
                     "list_names": item.get("list_names", []),
                     "stage": "queued", "error": None,
                     "job_directory": f"{i:02d}-{instrument_research._file_slug(item['instrument'])}",
                     "report": None, "analysis": None}
                    for i, item in enumerate(unique_items, 1)
                ],
            })
        except OSError as exc:
            with ACTIVE_RESEARCH_JOBS_LOCK:
                ACTIVE_RESEARCH_JOBS.discard(batch_id)
            logger.exception("Could not create instrument research batch directory")
            raise HTTPException(500, "Could not create the local research batch") from exc
        background_tasks.add_task(run_instrument_batch, batch_id, unique_items, batch_dir, body.model)
        return {
            "batch_id": batch_id,
            "status": "queued",
            "unique_instruments": len(unique_items),
            "duplicates_skipped": len(body.instruments) - len(unique_items),
            "model": body.model,
            "status_url": f"/api/finance/research-batches/{batch_id}",
            "external_feeds_enabled": bool(external_feeds_enabled()),
            "storage": "Per-company reports, LLM analyses, and durable batch status under the local data directory.",
        }

    @router.get("/research-batches/{batch_id}")
    async def get_instrument_research_batch(batch_id: str):
        if not re.fullmatch(r"[a-f0-9]{32}", batch_id):
            raise HTTPException(404, "Unknown research batch")
        status_path = research_data_dir / "research_batches" / batch_id / "status.json"
        try:
            return json.loads(status_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise HTTPException(404, "Unknown research batch") from exc
        except (OSError, json.JSONDecodeError) as exc:
            logger.exception("Could not read instrument research batch status")
            raise HTTPException(500, "Research batch status is unreadable") from exc

    @router.post("/research-batches/{batch_id}/resume", status_code=202)
    async def resume_instrument_research_batch(
        batch_id: str,
        body: InstrumentResearchBatchResumeRequest,
        background_tasks: BackgroundTasks,
    ):
        if not re.fullmatch(r"[a-f0-9]{32}", batch_id):
            raise HTTPException(404, "Unknown research batch")
        batch_dir = research_data_dir / "research_batches" / batch_id
        try:
            status = json.loads((batch_dir / "status.json").read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise HTTPException(404, "Unknown research batch") from exc
        except (OSError, json.JSONDecodeError) as exc:
            logger.exception("Could not read instrument research batch status for resume")
            raise HTTPException(500, "Research batch status is unreadable") from exc
        if status.get("status") == "completed" and not status.get("failed"):
            raise HTTPException(409, "This research batch has no failed or unfinished instruments")
        instruments = [
            {
                "instrument": item["instrument"],
                "asset_type": item.get("asset_type", "auto"),
                "list_names": item.get("list_names", []),
            }
            for item in status.get("items", [])
            if isinstance(item.get("instrument"), str)
        ]
        if not instruments:
            raise HTTPException(409, "This research batch has no resumable instruments")
        model = body.model or status.get("model", "llama3.2:latest")
        if model_status_getter is None or model_analysis_generator is None:
            raise HTTPException(503, "Local model analysis is not configured")
        try:
            model_status = await model_status_getter()
        except Exception as exc:
            logger.warning("Local model preflight failed: %s", type(exc).__name__)
            raise HTTPException(503, "Could not check the local model service; batch was not resumed") from exc
        if model not in model_status.get("installed_models", []):
            raise HTTPException(422, f"Local model {model!r} is not installed")
        with ACTIVE_RESEARCH_JOBS_LOCK:
            if ACTIVE_RESEARCH_JOBS:
                raise HTTPException(429, "Only one instrument research job or batch may run at a time")
            ACTIVE_RESEARCH_JOBS.add(batch_id)
        status.update({
            "status": "queued",
            "stage": "resuming",
            "model": model,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
        })
        try:
            instrument_research._atomic_json(batch_dir / "status.json", status)
        except OSError as exc:
            with ACTIVE_RESEARCH_JOBS_LOCK:
                ACTIVE_RESEARCH_JOBS.discard(batch_id)
            logger.exception("Could not queue instrument research batch resume")
            raise HTTPException(500, "Could not queue research batch resume") from exc
        background_tasks.add_task(
            run_instrument_batch, batch_id, instruments, batch_dir, model, resume=True,
        )
        return {"batch_id": batch_id, "status": "queued", "model": model}

    @router.get("/research-batches/{batch_id}/items/{item_index}/{artifact}", response_class=PlainTextResponse)
    async def get_instrument_research_batch_artifact(batch_id: str, item_index: int, artifact: str):
        if not re.fullmatch(r"[a-f0-9]{32}", batch_id) or artifact not in ("report", "analysis"):
            raise HTTPException(404, "Unknown research batch artifact")
        batch_dir = research_data_dir / "research_batches" / batch_id
        try:
            status = json.loads((batch_dir / "status.json").read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise HTTPException(404, "Unknown research batch") from exc
        item = next((entry for entry in status.get("items", []) if entry.get("index") == item_index), None)
        if not item:
            raise HTTPException(404, "Unknown research batch item")
        filename = item.get("analysis" if artifact == "analysis" else "report")
        if not filename or not re.fullmatch(r"\d{2}-[A-Za-z0-9._-]+/(?:analysis|report)\.md", filename):
            raise HTTPException(404, "Research artifact is not ready")
        try:
            return PlainTextResponse((batch_dir / filename).read_text(encoding="utf-8"), media_type="text/markdown")
        except FileNotFoundError as exc:
            raise HTTPException(404, "Research artifact is not ready") from exc
        except OSError as exc:
            logger.exception("Could not read instrument research artifact")
            raise HTTPException(500, "Research artifact is unreadable") from exc

    @router.post("/research-jobs", status_code=202)
    async def start_instrument_research(body: InstrumentResearchRequest, background_tasks: BackgroundTasks):
        try:
            selected = instrument_research.select_method(body.instrument, body.asset_type)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        with ACTIVE_RESEARCH_JOBS_LOCK:
            if ACTIVE_RESEARCH_JOBS:
                raise HTTPException(429, "Only one instrument research job may run at a time")
            job_id = uuid.uuid4().hex
            ACTIVE_RESEARCH_JOBS.add(job_id)
        job_dir = research_data_dir / "research_jobs" / job_id
        try:
            job_dir.mkdir(parents=True, exist_ok=False)
            instrument_research._atomic_json(job_dir / "status.json", {
                "instrument": body.instrument,
                "status": "queued",
                "progress_percent": 0,
                "stage": "queued",
                "steps": [],
                "issues": [],
                "started_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "finished_at": None,
                "report": None,
                "method": selected,
            })
        except OSError as exc:
            with ACTIVE_RESEARCH_JOBS_LOCK:
                ACTIVE_RESEARCH_JOBS.discard(job_id)
            logger.exception("Could not create instrument research job directory")
            raise HTTPException(500, "Could not create the local research job") from exc
        background_tasks.add_task(run_instrument_research, job_id, body.instrument, body.asset_type, job_dir)
        return {
            "job_id": job_id,
            "status": "queued",
            "status_url": f"/api/finance/research-jobs/{job_id}",
            "report_url": f"/api/finance/research-jobs/{job_id}/report",
            "external_feeds_enabled": bool(external_feeds_enabled()),
            "method": selected,
            "storage": "Local job files; no finance database changes.",
        }

    @router.get("/research-jobs/{job_id}")
    async def get_instrument_research_status(job_id: str):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise HTTPException(404, "Unknown research job")
        status_path = research_data_dir / "research_jobs" / job_id / "status.json"
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise HTTPException(404, "Unknown research job") from exc
        except (OSError, json.JSONDecodeError) as exc:
            logger.exception("Could not read instrument research job status")
            raise HTTPException(500, "Research job status is unreadable") from exc
        status["status_url"] = f"/api/finance/research-jobs/{job_id}"
        status["report_url"] = f"/api/finance/research-jobs/{job_id}/report"
        return status

    @router.get("/research-jobs/{job_id}/report", response_class=PlainTextResponse)
    async def get_instrument_research_report(job_id: str):
        if not re.fullmatch(r"[a-f0-9]{32}", job_id):
            raise HTTPException(404, "Unknown research job")
        report_path = research_data_dir / "research_jobs" / job_id / "report.md"
        try:
            return PlainTextResponse(report_path.read_text(encoding="utf-8"), media_type="text/markdown")
        except FileNotFoundError as exc:
            raise HTTPException(404, "Research report is not ready") from exc
        except OSError as exc:
            logger.exception("Could not read instrument research report")
            raise HTTPException(500, "Research report is unreadable") from exc

    # -- Companies / securities -------------------------------------------------
    @router.post("/securities")
    async def upsert_security(body: Security):
        store.upsert_security(body.symbol, body.name, body.sector, body.notes)
        return store.get_security(body.symbol)

    @router.get("/securities")
    async def list_securities():
        return {"securities": store.list_securities()}

    @router.get("/securities/{symbol}")
    async def get_security(symbol: str):
        security = store.get_security(symbol.upper())
        if not security:
            raise HTTPException(404, "Unknown security; create it first")
        return security

    def security_deep_dive(symbol: str) -> dict:
        symbol = symbol.upper()
        security = store.get_security(symbol)
        daily_bars = list(reversed(store.market_history(symbol, "1d", 250)))
        intraday_bars = list(reversed(store.market_history(symbol, "1h", 72)))
        latest_bar = daily_bars[-1] if daily_bars else None
        latest_statement = store.latest_statement(symbol) if security else None
        thesis = store.list_thesis_revisions(symbol) if security else []
        evidence = store.list_evidence(symbol) if security else []
        if latest_statement and latest_statement.get("evidence_id"):
            source_doc = store.get_evidence(latest_statement["evidence_id"])
            latest_statement["source_evidence"] = (
                {key: source_doc.get(key) for key in ("title", "publisher", "source_url", "published_on")}
                if source_doc else None
            )
        return {
            "symbol": symbol,
            "status": "available" if security else "unregistered",
            "company": security,
            "market_data": {
                "status": latest_bar["freshness"] if latest_bar else "unavailable",
                "observed_at": latest_bar["observed_at"] if latest_bar else None,
                "source": latest_bar["source"] if latest_bar else None,
                "currency": latest_bar["currency"] if latest_bar else None,
                "close": latest_bar["close"] if latest_bar else None,
                "daily_bars_cached": len(daily_bars),
                "warning": None if latest_bar else "No cached OHLC data; refresh the ASX watchlist first.",
            },
            "technical_levels": (
                calculators.calculate_daily_level_scenarios(daily_bars, intraday_bars)
                if len(daily_bars) >= 2 else {
                    "status": "unavailable",
                    "reason": "At least two cached daily OHLC bars are required.",
                    "entry_scenarios": [],
                }
            ),
            "latest_statement": latest_statement,
            "evidence_documents": [{
                key: doc.get(key)
                for key in ("id", "title", "publisher", "source_url", "published_on", "document_type", "limitations")
            } for doc in evidence],
            "latest_thesis": thesis[-1] if thesis else None,
            "catalysts": store.list_catalysts(symbol) if security else [],
            "valuation_runs": store.list_valuation_runs(symbol)[:5] if security else [],
            "alerts": store.list_alerts(symbol)[:10] if security else [],
            "limitations": [
                "No automatic Market Index scraping; announcements require manual or authorized licensed sources.",
                "Entry areas and targets are conditional technical reference levels, not fair value or personalized advice.",
                "Missing statements, evidence, catalyst records, or stale prices remain explicitly unavailable.",
            ],
        }

    @router.get("/securities/{symbol}/analysis")
    async def security_analysis(symbol: str):
        return security_deep_dive(symbol)

    @router.get("/watchlists/{watchlist_id}/analysis")
    async def watchlist_analysis(watchlist_id: int):
        watchlist = store.get_watchlist(watchlist_id)
        if not watchlist:
            raise HTTPException(404, "Unknown watchlist")
        return {
            "watchlist": watchlist["name"],
            "symbols": [security_deep_dive(symbol) for symbol in watchlist["symbols"]],
        }

    # -- Generic instruments / gold trading -----------------------------------
    @router.post("/instruments")
    async def upsert_instrument(body: InstrumentIn):
        source_symbol = body.source_symbol
        if body.instrument_type == "xauusd_spot_proxy" and source_symbol is None:
            source_symbol = XAUUSD_SYMBOL
        return store.upsert_instrument({
            **body.model_dump(), "source_symbol": source_symbol,
        })

    @router.get("/instruments")
    async def list_instruments():
        instruments = store.list_instruments()
        if not any(item["symbol"] == XAUUSD_SYMBOL for item in instruments):
            instruments.append({
                "symbol": XAUUSD_SYMBOL, "name": "Gold / US dollar (Yahoo proxy)",
                "instrument_type": "xauusd_spot_proxy", "currency": "USD",
                "source_symbol": XAUUSD_SYMBOL, "registered": False,
            })
        return {"instruments": instruments}

    @router.get("/instruments/{symbol}")
    async def get_instrument(symbol: str):
        normalized = symbol.upper()
        instrument = store.get_instrument(normalized)
        if not instrument and normalized == XAUUSD_SYMBOL:
            return {
                "symbol": XAUUSD_SYMBOL, "name": "Gold / US dollar (Yahoo proxy)",
                "instrument_type": "xauusd_spot_proxy", "currency": "USD",
                "source_symbol": XAUUSD_SYMBOL, "registered": False,
            }
        if not instrument:
            raise HTTPException(404, "Unknown instrument")
        return instrument

    @router.get("/xauusd/timeframes")
    async def xauusd_timeframes():
        from assistant_api.finance.market_data import XAUUSD_TIMEFRAMES
        return {
            "instrument": XAUUSD_SYMBOL,
            "timeframes": list(XAUUSD_TIMEFRAMES),
            "source": market_data.XAUUSD_SOURCE,
            "warning": "Yahoo Finance XAUUSD=X is an unofficial proxy, not an authoritative executable spot quote.",
        }

    @router.post("/xauusd/refresh")
    async def refresh_xauusd(timeframe: str = Query("1h", pattern=r"^(1m|5m|15m|1h|1d)$")):
        if not external_feeds_enabled():
            raise HTTPException(
                503,
                "External feeds are disabled (MR_SMITH_EXTERNAL_FEEDS is not set to 1); "
                "XAUUSD refresh requires live network access to be explicitly enabled",
            )
        result = await market_data.fetch_xauusd_history(feeds_client_getter(), timeframe)
        if result["status"] in ("error", "invalid_timeframe", "no_results"):
            return result
        new_observations = store.save_market_observations(result["bars"])
        return {
            **{key: value for key, value in result.items() if key != "bars"},
            "observations": len(result["bars"]),
            "new_observations": new_observations,
        }

    @router.post("/xauusd/quote/refresh")
    async def refresh_xauusd_quote():
        if not external_feeds_enabled():
            raise HTTPException(
                503,
                "External feeds are disabled (MR_SMITH_EXTERNAL_FEEDS is not set to 1); "
                "XAUUSD quote refresh requires explicit live network access",
            )
        result = await market_data.fetch_xauusd_quote(feeds_client_getter())
        if result["status"] in ("error", "invalid_timeframe", "no_results"):
            return result
        store.save_market_observations([result])
        return result

    @router.get("/xauusd/quote")
    async def xauusd_quote():
        bars = store.market_history(XAUUSD_SYMBOL, "1m", 1)
        if not bars:
            return {
                "instrument": XAUUSD_SYMBOL, "status": "unavailable",
                "reason": "No cached quote; use the explicit quote refresh endpoint when external feeds are enabled.",
                "source": market_data.XAUUSD_SOURCE, "currency": "USD",
            }
        bar = bars[0]
        return {
            **bar, "status": "ok" if bar["freshness"] == "fresh" else "stale",
            "warning": "Cached Yahoo Finance XAUUSD=X proxy; not an authoritative executable spot quote.",
        }

    @router.get("/xauusd/history")
    async def xauusd_history(
        timeframe: str = Query("1h", pattern=r"^(1m|5m|15m|1h|1d)$"),
        limit: int = Query(250, ge=1, le=1000),
    ):
        bars = store.market_history(XAUUSD_SYMBOL, timeframe, limit)
        return {
            "instrument": XAUUSD_SYMBOL,
            "status": "ok" if bars else "unavailable",
            "reason": None if bars else "No cached bars for this timeframe.",
            "source": market_data.XAUUSD_SOURCE,
            "timeframe": timeframe,
            "bars": list(reversed(bars)),
        }

    @router.get("/xauusd/analysis")
    async def xauusd_analysis(
        timeframe: str = Query("1h", pattern=r"^(1m|5m|15m|1h|1d)$"),
        limit: int = Query(250, ge=1, le=1000),
    ):
        bars = list(reversed(store.market_history(XAUUSD_SYMBOL, timeframe, limit)))
        if not bars:
            return {
                "status": "unavailable", "instrument": XAUUSD_SYMBOL,
                "timeframe": timeframe, "reason": "No cached bars; refresh data first.",
                "daily_levels": calculators.calculate_daily_level_scenarios(
                    list(reversed(store.market_history(XAUUSD_SYMBOL, "1d", 250))),
                    list(reversed(store.market_history(XAUUSD_SYMBOL, "1h", 72))),
                ),
                "macro_context": macro_data.summarize_gold_macro_context(store),
            }
        try:
            daily_bars = list(reversed(store.market_history(XAUUSD_SYMBOL, "1d", 250)))
            intraday_bars = list(reversed(store.market_history(XAUUSD_SYMBOL, "1h", 72)))
            return {
                **calculators.analyze_ohlc(bars),
                "daily_levels": calculators.calculate_daily_level_scenarios(
                    daily_bars, intraday_bars,
                ),
                "macro_context": macro_data.summarize_gold_macro_context(store),
            }
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/xauusd/levels")
    async def xauusd_daily_levels():
        return calculators.calculate_daily_level_scenarios(
            list(reversed(store.market_history(XAUUSD_SYMBOL, "1d", 250))),
            list(reversed(store.market_history(XAUUSD_SYMBOL, "1h", 72))),
        )

    @router.post("/xauusd/risk")
    async def xauusd_risk(body: RiskCalculationIn):
        result = calculators.calculate_trade_risk(body.model_dump())
        return {
            "instrument": body.instrument,
            **result,
            "note": "Conditional scenario calculation only; not a buy/sell recommendation.",
        }

    @router.post("/xauusd/journal")
    async def add_xauusd_journal_entry(body: TradingJournalIn):
        entry = body.model_dump()
        if entry["instrument"] not in (XAUUSD_SYMBOL, "XAUUSD", "GOLD"):
            raise HTTPException(422, "This journal route accepts only XAUUSD proxy entries")
        entry["instrument"] = XAUUSD_SYMBOL
        entry_id = store.add_trading_journal_entry(entry)
        return {"id": entry_id, "created": True}

    @router.get("/xauusd/journal")
    async def list_xauusd_journal(limit: int = Query(100, ge=1, le=500)):
        return {"entries": store.list_trading_journal(XAUUSD_SYMBOL, limit)}

    @router.post("/xauusd/journal/{entry_id}/close")
    async def close_xauusd_journal_entry(entry_id: int, body: TradingJournalCloseIn):
        closed_at = body.closed_at or datetime.now(timezone.utc).isoformat()
        updated = store.close_trading_journal_entry(
            entry_id, body.actual_result, closed_at, body.lessons,
        )
        if not updated:
            raise HTTPException(404, "Unknown or already closed journal entry")
        return {"id": entry_id, "closed_at": closed_at}

    @router.get("/xauusd/macro/sources")
    async def xauusd_macro_sources():
        return {
            "category": "gold_macro",
            "sources": list(MACRO_SOURCE_CATALOG),
            "warning": (
                "Refreshes are manual and require MR_SMITH_EXTERNAL_FEEDS=1. "
                "Automated Market Index access is prohibited by its published terms."
            ),
        }

    @router.post("/xauusd/macro/refresh")
    async def refresh_xauusd_macro():
        if not external_feeds_enabled():
            raise HTTPException(
                503,
                "External feeds are disabled (MR_SMITH_EXTERNAL_FEEDS is not set to 1); "
                "macro refresh requires explicit live network access",
            )
        client = feeds_client_getter()
        symbols = (*macro_data.MACRO_YAHOO_SYMBOLS, market_data.XAUUSD_SOURCE_SYMBOL)
        tasks = [
            market_data.fetch_yahoo_market_history(client, symbol, "1d")
            for symbol in symbols
        ]
        tasks.extend((
            macro_data.fetch_real_yield_curve(client),
            macro_data.fetch_federal_reserve_headlines(client),
            macro_data.fetch_geopolitical_news(client),
        ))
        outcomes = await asyncio.gather(*tasks)
        sources = {}
        saved = {}
        evidence_count = 0
        for result in outcomes:
            source = result["source"]
            sources[source] = {
                key: result.get(key)
                for key in ("status", "error", "warning")
                if result.get(key) is not None
            }
            bars = result.get("bars", result.get("items", []))
            if bars and bars[0].get("instrument"):
                saved[source] = store.save_market_observations(bars)
            for item in result.get("items", []):
                if item.get("topic"):
                    store.add_gold_macro_evidence(item)
                    evidence_count += 1
        return {
            "category": "gold_macro",
            "sources": sources,
            "new_observations": saved,
            "headlines_saved": evidence_count,
            "context": macro_data.summarize_gold_macro_context(store),
        }

    @router.get("/xauusd/macro/context")
    async def get_xauusd_macro_context():
        return macro_data.summarize_gold_macro_context(store)

    @router.get("/market/snapshot")
    async def finance_market_snapshot():
        return macro_data.summarize_market_snapshot(store)

    @router.get("/opportunity/benchmark")
    async def opportunity_benchmark():
        candidates = sorted(research_data_dir.glob("opportunity_first_run_*/benchmark.json"))
        path = candidates[-1] if candidates else None
        if path is None or not path.is_file():
            raise HTTPException(404, "No ASX opportunity benchmark snapshot has been generated")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(500, "ASX opportunity benchmark snapshot is unreadable") from exc

    @router.get("/opportunity/profiles")
    async def opportunity_profiles():
        candidates = sorted(research_data_dir.glob("asx_entity_profiles_*/profiles.json"))
        path = candidates[-1] if candidates else None
        if path is None or not path.is_file():
            raise HTTPException(404, "No ASX entity profiles have been generated")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(500, "ASX entity profiles are unreadable") from exc

    @router.get("/opportunity/profiles/{symbol}/report")
    async def opportunity_profile_report(symbol: str):
        code = symbol.upper().removesuffix(".AX").removeprefix("ASX:")
        candidates = sorted(research_data_dir.glob(f"research_jobs/*-ASX-{code}/report.md"))
        if not candidates:
            raise HTTPException(404, "No detailed report is available for this company")
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(candidates[-1].read_text(encoding="utf-8"))

    @router.get("/chart-analysis/{symbol}")
    async def chart_analysis(symbol: str):
        code = symbol.upper().removesuffix(".AX").removeprefix("ASX:")
        candidates = sorted(research_data_dir.glob(f"research_jobs/*ASX-{code}/market-data.json"))
        if not candidates:
            raise HTTPException(404, "No market packet is available for this company")
        try:
            packet = json.loads(candidates[-1].read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(500, "Market packet is unreadable") from exc
        bars = packet.get("bars", [])
        return {"instrument": f"ASX:{code}", "bars": bars, "statistics": packet.get("statistics", {}), "source": packet.get("source"), "retrieved_at": packet.get("retrieved_at")}

    @router.get("/opportunity/snapshot")
    async def opportunity_snapshot():
        candidates = sorted(research_data_dir.glob("asx_opportunity_snapshot_*/snapshot.json"))
        path = candidates[-1] if candidates else None
        if path is None or not path.is_file():
            raise HTTPException(404, "No ASX deep-dive snapshot has been generated")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HTTPException(500, "ASX deep-dive snapshot is unreadable") from exc

    @router.post("/xauusd/macro/evidence")
    async def add_xauusd_macro_evidence(body: GoldMacroEvidenceIn):
        evidence_id = store.add_gold_macro_evidence(body.model_dump())
        return {"id": evidence_id, "created": True, "category": "gold_macro"}

    @router.get("/xauusd/macro/evidence")
    async def list_xauusd_macro_evidence(
        topic: str | None = Query(None, pattern=r"^(us_rates|real_yields|usd|inflation|federal_reserve|central_bank_demand|geopolitical_events|risk_sentiment)$"),
        limit: int = Query(100, ge=1, le=500),
    ):
        return {
            "category": "gold_macro",
            "evidence": store.list_gold_macro_evidence(topic, limit),
        }

    # -- Evidence -----------------------------------------------------------
    @router.post("/securities/{symbol}/evidence")
    async def add_evidence(symbol: str, body: EvidenceDocumentIn):
        symbol = symbol.upper()
        if not store.get_security(symbol):
            raise HTTPException(422, "Create the security before attaching evidence")
        passages = plain_text_passages(body.text)
        digest = content_hash(body.text.encode())
        evidence_id, created = store.add_evidence(
            symbol, body.title, body.publisher, body.source_type, body.document_type,
            body.source_url, body.published_on, body.period, digest, passages, page_count=1,
        )
        return {"id": evidence_id, "created": created}

    @router.post("/securities/{symbol}/evidence/pdf")
    async def add_evidence_pdf(symbol: str, file: UploadFile, meta: PdfEvidenceMeta = Depends()):
        symbol = symbol.upper()
        if not store.get_security(symbol):
            raise HTTPException(422, "Create the security before attaching evidence")
        raw = await file.read()
        if len(raw) > 25_000_000:
            raise HTTPException(422, "PDF exceeds the 25MB import budget")
        pages, limitations = extract_pdf_text(raw)
        if not pages:
            # Never save a blank evidence document pretending it has substantive
            # content; surface the extraction limitation instead.
            raise HTTPException(422, limitations or "No pages could be read from this PDF")
        passages = passages_with_page_numbers(pages)
        digest = content_hash(raw)
        evidence_id, created = store.add_evidence(
            symbol, meta.title, meta.publisher, meta.source_type, meta.document_type, meta.source_url,
            meta.published_on, meta.period, digest, passages, page_count=len(pages), limitations=limitations,
        )
        return {"id": evidence_id, "created": created, "pages": len(pages), "limitations": limitations}

    @router.get("/securities/{symbol}/evidence")
    async def list_evidence(symbol: str):
        return {"evidence": store.list_evidence(symbol.upper())}

    # -- Statements (immutable, versioned) -----------------------------------
    @router.post("/securities/{symbol}/statements")
    async def save_statement(symbol: str, body: StatementFigureIn):
        symbol = symbol.upper()
        if not store.get_security(symbol):
            raise HTTPException(422, "Create the security before confirming statement figures")
        if body.evidence_id is None and not body.source_note.strip():
            raise HTTPException(422, "A figure without linked evidence requires a manual-entry source note")
        _check_evidence_ownership(symbol, body.evidence_id)
        figures = body.model_dump(exclude={"symbol", "period", "fiscal_period_end", "currency", "scale",
                                           "consolidated", "evidence_id", "source_note"})
        statement_id, revision = store.save_statement(
            symbol, body.period, body.fiscal_period_end, body.currency, body.scale,
            body.consolidated, figures, body.evidence_id, body.source_note,
        )
        return {"id": statement_id, "revision": revision}

    @router.get("/securities/{symbol}/statements")
    async def list_statements(symbol: str):
        return {"statements": store.list_statements(symbol.upper())}

    @router.get("/securities/{symbol}/statements/{period}/{fiscal_period_end}/revisions")
    async def statement_revisions(symbol: str, period: str, fiscal_period_end: str):
        return {"revisions": store.list_statement_revisions(symbol.upper(), period, fiscal_period_end)}

    @router.post("/securities/{symbol}/statements/preview")
    async def preview_statements(symbol: str, file: UploadFile):
        symbol = symbol.upper()
        if not store.get_security(symbol):
            raise HTTPException(422, "Create the security before importing statement figures")
        raw = (await file.read()).decode("utf-8-sig", errors="replace")
        rows, errors = _parse_statements(raw, file.filename or "", symbol)
        return {"rows": [r.model_dump(mode="json") for r in rows], "errors": errors, "valid": not errors}

    @router.post("/securities/{symbol}/statements/confirm")
    async def confirm_statements(symbol: str, file: UploadFile):
        symbol = symbol.upper()
        if not store.get_security(symbol):
            raise HTTPException(422, "Create the security before importing statement figures")
        raw = (await file.read()).decode("utf-8-sig", errors="replace")
        rows, errors = _parse_statements(raw, file.filename or "", symbol)
        if errors:
            raise HTTPException(422, {"message": "Import rejected atomically; fix the rows below", "errors": errors})
        saved = []
        for row in rows:
            _check_evidence_ownership(symbol, row.evidence_id)
            figures = row.model_dump(exclude={"symbol", "period", "fiscal_period_end", "currency", "scale",
                                              "consolidated", "evidence_id", "source_note"})
            statement_id, revision = store.save_statement(
                symbol, row.period, row.fiscal_period_end, row.currency, row.scale,
                row.consolidated, figures, row.evidence_id, row.source_note,
            )
            saved.append({"id": statement_id, "period": row.period,
                         "fiscal_period_end": row.fiscal_period_end.isoformat(), "revision": revision})
        return {"imported": len(saved), "statements": saved}

    # -- Thesis revisions -----------------------------------------------------
    @router.post("/securities/{symbol}/thesis")
    async def add_thesis(symbol: str, body: ThesisRevisionIn):
        symbol = symbol.upper()
        if not store.get_security(symbol):
            raise HTTPException(422, "Create the security before saving a thesis")
        for evidence_id in body.evidence_ids:
            _check_evidence_ownership(symbol, evidence_id)
        revision = store.add_thesis_revision(
            symbol, thesis=body.thesis, counter_thesis=body.counter_thesis,
            assumptions=body.assumptions, sensitivities=body.sensitivities,
            catalysts=body.catalysts, risks=body.risks,
            invalidation_criteria=body.invalidation_criteria, evidence_ids=body.evidence_ids,
        )
        return {"revision": revision}

    @router.get("/securities/{symbol}/thesis")
    async def list_thesis(symbol: str):
        return {"revisions": store.list_thesis_revisions(symbol.upper())}

    @router.get("/securities/{symbol}/thesis/compare")
    async def compare_thesis(symbol: str, from_revision: int, to_revision: int):
        revisions = {r["revision"]: r for r in store.list_thesis_revisions(symbol.upper())}
        if from_revision not in revisions or to_revision not in revisions:
            raise HTTPException(404, "Unknown revision")
        before, after = revisions[from_revision], revisions[to_revision]
        changed_fields = [
            field for field in ("thesis", "counter_thesis", "assumptions", "sensitivities",
                                "catalysts", "risks", "invalidation_criteria")
            if before[field] != after[field]
        ]
        newly_linked = sorted(set(after["evidence_ids"]) - set(before["evidence_ids"]))
        evidence_changed = set(after["evidence_ids"]) != set(before["evidence_ids"])
        if evidence_changed and "evidence_ids" not in changed_fields:
            changed_fields = [*changed_fields, "evidence_ids"]
        return {
            "what_changed": changed_fields,
            "why_it_matters": f"Revision carries evidence IDs: {newly_linked} newly linked",
            "thesis_impact": "Review changed_fields against linked evidence before treating this as decision-grade.",
        }

    @router.get("/securities/{symbol}/thesis/export")
    async def export_thesis(symbol: str):
        symbol = symbol.upper()
        revisions = store.list_thesis_revisions(symbol)
        if not revisions:
            raise HTTPException(404, "No thesis revisions saved")
        latest = revisions[-1]
        evidence_lines = ["_None linked_"]
        if latest["evidence_ids"]:
            evidence_lines = []
            for evidence_id in latest["evidence_ids"]:
                doc = store.get_evidence(evidence_id)
                if not doc:
                    evidence_lines.append(f"- evidence #{evidence_id}: _no longer available_")
                    continue
                evidence_lines.append(
                    f"- [{doc['title']}]({doc['source_url']}) — {doc['publisher']}, "
                    f"{doc['published_on'] or 'undated'} (evidence #{evidence_id})"
                )
        lines = [f"# {symbol} investment thesis (revision {latest['revision']})", "",
                "*Sentiment and scenarios are analytical tools, not financial advice.*", "",
                "## Thesis", latest["thesis"], "", "## Counter-thesis", latest["counter_thesis"] or "_None recorded_",
                "", "## Assumptions", latest["assumptions"] or "_None recorded_", "",
                "## Sensitivities", latest["sensitivities"] or "_None recorded_", "",
                "## Catalysts", latest["catalysts"] or "_None recorded_", "",
                "## Risks", latest["risks"] or "_None recorded_", "",
                "## Invalidation criteria", latest["invalidation_criteria"] or "_None recorded_", "",
                "## Source evidence", *evidence_lines]
        return {"markdown": "\n".join(lines)}

    # -- Watchlists / price history / alerts ----------------------------------
    @router.post("/watchlists")
    async def create_watchlist(body: WatchlistIn):
        watchlist_id = store.create_watchlist(body.name, body.symbols, body.price_alert_threshold_pct)
        return store.get_watchlist(watchlist_id)

    @router.post("/watchlists/import")
    async def import_watchlist(
        file: UploadFile,
        name: str = Form(min_length=1, max_length=120),
    ):
        if not name.strip():
            raise HTTPException(422, "Watchlist name cannot be blank")
        raw = await file.read()
        if len(raw) > 2_000_000:
            raise HTTPException(422, "Watchlist CSV exceeds the 2 MB import limit")
        try:
            symbols, companies, warnings = _parse_watchlist_csv(
                raw.decode("utf-8-sig", errors="strict"),
            )
        except (UnicodeDecodeError, ValueError) as exc:
            raise HTTPException(422, str(exc)) from exc
        watchlist_id = store.import_watchlist(name.strip(), symbols, companies)
        return {
            "id": watchlist_id,
            "name": name.strip(),
            "imported_symbols": symbols,
            "registered_companies": len(companies),
            "warnings": warnings,
        }

    @router.get("/watchlists")
    async def list_watchlists():
        return {"watchlists": store.list_watchlists()}

    @router.post("/watchlists/{watchlist_id}/refresh")
    async def refresh_watchlist(watchlist_id: int):
        watchlist = store.get_watchlist(watchlist_id)
        if not watchlist:
            raise HTTPException(404, "Unknown watchlist")
        if not external_feeds_enabled():
            raise HTTPException(
                503,
                "External feeds are disabled (MR_SMITH_EXTERNAL_FEEDS is not set to 1); "
                "watchlist refresh requires live network access to be explicitly enabled",
            )
        client = feeds_client_getter()
        try:
            results = await market_data.refresh_history(client, watchlist["symbols"])
            market_results = await market_data.refresh_market_history(
                client, watchlist["symbols"], "1d",
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        market_by_symbol = {item["instrument"]: item for item in market_results}
        now = datetime.now(timezone.utc).isoformat()
        threshold = watchlist.get("price_alert_threshold_pct")
        summary = []
        for result in results:
            new_bars = 0
            for bar in result["bars"]:
                is_new = store.save_price_bar(
                    result["symbol"], bar["observed_on"], bar["currency"], bar["close"],
                    bar["adjusted_close"], bar["volume"], bar["retrieved_at"],
                    observed_at_utc=bar.get("observed_at_utc"), exchange_timezone=bar.get("exchange_timezone"),
                )
                if is_new:
                    new_bars += 1
            summary.append({"symbol": result["symbol"], "status": result["status"],
                            "error": result["error"], "new_observations": new_bars,
                            "technical_data_status": market_by_symbol[result["symbol"]]["status"],
                            "new_ohlc_observations": store.save_market_observations(
                                market_by_symbol[result["symbol"]].get("bars", []),
                            )})
            if threshold and len(result["bars"]) >= 2:
                latest, previous = result["bars"][-1], result["bars"][-2]
                if latest["close"] and previous["close"]:
                    change_pct = (Decimal(latest["close"]) - Decimal(previous["close"])) / Decimal(previous["close"]) * 100
                    if abs(change_pct) >= Decimal(threshold):
                        store.add_alert(
                            result["symbol"], "developing",
                            f"{result['symbol']} moved {change_pct:.2f}% vs prior observation",
                            f"close {previous['close']} -> {latest['close']}", latest["observed_on"],
                        )
        return {"refreshed_at": now, "results": summary}

    @router.get("/securities/{symbol}/prices")
    async def price_history(symbol: str):
        return {"bars": store.price_history(symbol.upper())}

    @router.get("/alerts")
    async def list_alerts(symbol: str | None = None):
        return {"alerts": store.list_alerts(symbol.upper() if symbol else None)}

    @router.post("/alerts/evaluate")
    async def evaluate_alerts():
        """On-demand, deterministic alert evaluation (no scheduling, no scores):
        (a) a catalyst event_date within the next 7 days -> 'material';
        (b) evidence newer than the latest thesis revision -> 'thesis_review'."""
        created = []
        today = datetime.now(timezone.utc).date()
        for security in store.list_securities():
            symbol = security["symbol"]
            for catalyst in store.list_catalysts(symbol):
                event_date = date.fromisoformat(catalyst["event_date"])
                days_out = (event_date - today).days
                if 0 <= days_out <= 7:
                    message = f"{catalyst['title']} is scheduled within {days_out} day(s)"
                    if store.add_alert(symbol, "material", message, catalyst["source_url"], catalyst["event_date"]):
                        created.append({"symbol": symbol, "kind": "material", "message": message})
            revisions = store.list_thesis_revisions(symbol)
            evidence = store.list_evidence(symbol)
            if revisions and evidence:
                latest_revision_at = revisions[-1]["created_at"]
                newer_evidence = [e for e in evidence if e["created_at"] > latest_revision_at]
                for doc in newer_evidence:
                    message = f"New evidence '{doc['title']}' postdates thesis revision {revisions[-1]['revision']}"
                    if store.add_alert(symbol, "thesis_review", message, doc["source_url"],
                                       doc["published_on"] or doc["created_at"][:10]):
                        created.append({"symbol": symbol, "kind": "thesis_review", "message": message})
        return {"created": created}

    @router.post("/securities/{symbol}/catalysts")
    async def add_catalyst(symbol: str, body: CatalystIn):
        symbol = symbol.upper()
        catalyst_id, created = store.add_catalyst(
            symbol, body.title, body.event_date, body.timezone, body.source_url, body.is_manual,
        )
        if created:
            store.add_alert(symbol, "developing", f"New catalyst: {body.title}",
                            body.source_url or "manual entry", body.event_date.isoformat())
        return {"id": catalyst_id, "created": created}

    @router.get("/securities/{symbol}/catalysts")
    async def list_catalysts(symbol: str):
        return {"catalysts": store.list_catalysts(symbol.upper())}

    # -- Valuation -------------------------------------------------------------
    @router.post("/securities/{symbol}/valuation/ratios")
    async def valuation_ratios(symbol: str, body: RatioValuationRequest):
        symbol = symbol.upper()
        statement = store.get_statement(symbol, body.statement_period, body.statement_fiscal_period_end.isoformat())
        if not statement:
            raise HTTPException(
                422,
                f"No confirmed statement for {symbol} {body.statement_period} "
                f"{body.statement_fiscal_period_end.isoformat()}; confirm a statement before computing ratios",
            )
        if body.price_currency != statement["currency"]:
            raise HTTPException(
                422,
                f"price_currency {body.price_currency!r} does not match the confirmed statement's "
                f"currency {statement['currency']!r}; no automatic FX conversion is performed. "
                "Supply a price quoted in the statement's currency, or confirm a statement in "
                f"{body.price_currency!r}.",
            )
        multiplier = SCALE_MULTIPLIER[statement["scale"]]
        figures = statement["figures"]
        inputs = {"price": body.price, "shares_diluted": figures.get("shares_diluted")}
        for field_name in MONETARY_FIGURE_FIELDS:
            raw = figures.get(field_name)
            inputs[field_name] = str(Decimal(raw) * multiplier) if raw is not None else None
        result = calculators.compute_ratios(inputs)
        run_id = store.save_valuation_run(symbol, "ratios", {
            "price": body.price, "price_currency": body.price_currency, "price_date": body.price_date.isoformat(),
            "statement_period": body.statement_period,
            "statement_fiscal_period_end": body.statement_fiscal_period_end.isoformat(),
            "statement_revision": statement["revision"], "statement_scale": statement["scale"],
            "statement_currency": statement["currency"],
        }, result.to_dict(), statement["id"])
        return {
            "id": run_id, **result.to_dict(),
            "provenance": {
                "statement_revision": statement["revision"], "statement_currency": statement["currency"],
                "statement_scale": statement["scale"], "price_date": body.price_date.isoformat(),
                "price_currency": body.price_currency,
                "note": f"Monetary figures converted from {statement['scale']} to units. "
                       f"Price currency ({body.price_currency}) was verified to match the statement's "
                       f"currency ({statement['currency']}); no automatic FX conversion is performed.",
            },
        }

    @router.post("/securities/{symbol}/valuation/dcf")
    async def valuation_dcf(symbol: str, body: DCFRequest):
        symbol = symbol.upper()
        try:
            report = calculators.compute_dcf_report(
                [s.model_dump() for s in body.scenarios], body.current_price,
                body.current_price_date.isoformat() if body.current_price_date else None,
            )
        except (ValueError, InvalidOperation) as exc:
            raise HTTPException(422, str(exc)) from exc
        result = {
            "scenarios": [s.__dict__ for s in report.scenarios],
            "probability_weighted_value_per_share": report.probability_weighted_value_per_share,
            "probability_weighted_reason": report.probability_weighted_reason,
            "margin_of_safety": report.margin_of_safety,
            "margin_of_safety_reason": report.margin_of_safety_reason,
        }
        run_id = store.save_valuation_run(symbol, "dcf", body.model_dump(mode="json"), result, None)
        return {"id": run_id, **result}

    @router.post("/securities/{symbol}/valuation/dcf/sensitivity")
    async def valuation_dcf_sensitivity(symbol: str, body: DCFSensitivityRequest):
        symbol = symbol.upper()
        try:
            grid = calculators.compute_dcf_sensitivity(
                body.base.model_dump(), body.discount_rate_deltas, body.terminal_growth_deltas,
            )
        except (ValueError, InvalidOperation) as exc:
            raise HTTPException(422, str(exc)) from exc
        run_id = store.save_valuation_run(symbol, "dcf_sensitivity", body.model_dump(mode="json"), grid, None)
        return {"id": run_id, **grid}

    @router.get("/securities/{symbol}/valuation/dcf/export")
    async def export_dcf(symbol: str, run_id: int):
        symbol = symbol.upper()
        runs = {r["id"]: r for r in store.list_valuation_runs(symbol)}
        run = runs.get(run_id)
        if not run or run["kind"] != "dcf":
            raise HTTPException(404, "Unknown DCF valuation run")
        result = run["result"]
        lines = [f"# {symbol} DCF valuation (run #{run_id})", "",
                "*Analytical scenario output, not a recommendation.*", ""]
        for scenario in result["scenarios"]:
            lines += [f"## Scenario: {scenario['name']}",
                      f"- Value per share: {scenario['value_per_share']}",
                      f"- Probability: {scenario.get('probability') or '_not set_'}", ""]
        weighted = result.get("probability_weighted_value_per_share")
        if weighted:
            lines.append(f"**Probability-weighted value per share:** {weighted}")
        else:
            lines.append(f"**Probability-weighted value per share:** "
                        f"_unavailable: {result.get('probability_weighted_reason')}_")
        if result.get("margin_of_safety"):
            mos = result["margin_of_safety"]
            lines.append(f"**Margin of safety:** {mos['margin_of_safety']} "
                        f"(fair value {mos['fair_value']} vs price {mos['price']} on {mos['price_date']})")
        else:
            lines.append(f"**Margin of safety:** _unavailable: {result.get('margin_of_safety_reason')}_")
        return {"markdown": "\n".join(lines)}

    @router.get("/securities/{symbol}/valuation/runs")
    async def valuation_runs(symbol: str):
        return {"runs": store.list_valuation_runs(symbol.upper())}

    # -- Portfolios --------------------------------------------------------
    @router.post("/portfolios")
    async def create_portfolio(name: str = Body(embed=True)):
        portfolio_id = store.create_portfolio(name)
        return {"id": portfolio_id, "name": name}

    @router.get("/portfolios")
    async def list_portfolios():
        return {"portfolios": store.list_portfolios()}

    @router.post("/portfolios/{portfolio_id}/holdings/preview")
    async def preview_holdings(portfolio_id: int, file: UploadFile):
        if not store.get_portfolio(portfolio_id):
            raise HTTPException(404, "Unknown portfolio")
        raw = (await file.read()).decode("utf-8-sig", errors="replace")
        rows, errors = _parse_holdings(raw, file.filename or "")
        return {"rows": rows, "errors": errors, "valid": not errors}

    @router.post("/portfolios/{portfolio_id}/holdings/confirm")
    async def confirm_holdings(portfolio_id: int, file: UploadFile):
        if not store.get_portfolio(portfolio_id):
            raise HTTPException(404, "Unknown portfolio")
        raw = (await file.read()).decode("utf-8-sig", errors="replace")
        rows, errors = _parse_holdings(raw, file.filename or "")
        if errors:
            raise HTTPException(422, {"message": "Import rejected atomically; fix the rows below", "errors": errors})
        count = store.replace_holdings(portfolio_id, rows)
        return {"imported": count}

    @router.get("/portfolios/{portfolio_id}/holdings")
    async def list_holdings(portfolio_id: int):
        if not store.get_portfolio(portfolio_id):
            raise HTTPException(404, "Unknown portfolio")
        return {"holdings": store.list_holdings(portfolio_id)}

    @router.post("/portfolios/{portfolio_id}/holdings")
    async def add_holding(portfolio_id: int, body: HoldingRow):
        if not store.get_portfolio(portfolio_id):
            raise HTTPException(404, "Unknown portfolio")
        existing = {h["symbol"] for h in store.list_holdings(portfolio_id)}
        if body.symbol in existing:
            raise HTTPException(422, f"{body.symbol} already exists in this portfolio; use PUT to update it")
        holding_id = store.add_holding(portfolio_id, body.model_dump())
        return {"id": holding_id}

    @router.put("/portfolios/{portfolio_id}/holdings/{holding_id}")
    async def update_holding(portfolio_id: int, holding_id: int, body: HoldingRow):
        if not store.get_portfolio(portfolio_id):
            raise HTTPException(404, "Unknown portfolio")
        if not store.update_holding(portfolio_id, holding_id, body.model_dump()):
            raise HTTPException(404, "Unknown holding in this portfolio")
        return {"updated": True}

    @router.delete("/portfolios/{portfolio_id}/holdings/{holding_id}")
    async def delete_holding(portfolio_id: int, holding_id: int):
        if not store.get_portfolio(portfolio_id):
            raise HTTPException(404, "Unknown portfolio")
        if not store.delete_holding(portfolio_id, holding_id):
            raise HTTPException(404, "Unknown holding in this portfolio")
        return {"deleted": True}

    @router.get("/portfolios/{portfolio_id}/analysis")
    async def portfolio_analysis(portfolio_id: int):
        if not store.get_portfolio(portfolio_id):
            raise HTTPException(404, "Unknown portfolio")
        analysis = portfolio_calc.analyze_portfolio(_value_portfolio(portfolio_id))
        return analysis.__dict__

    @router.post("/portfolios/{portfolio_id}/scenario")
    async def portfolio_scenario(portfolio_id: int, shocks: list[ShockIn] = Body(...)):
        if not store.get_portfolio(portfolio_id):
            raise HTTPException(404, "Unknown portfolio")
        valuations = _value_portfolio(portfolio_id)
        return portfolio_calc.apply_shocks(valuations, [s.model_dump() for s in shocks])

    @router.get("/portfolios/{portfolio_id}/correlation")
    async def portfolio_correlation(
        portfolio_id: int, symbol_a: str, symbol_b: str,
        window: int = Query(250, gt=20, le=365,
                             description="Trading-day lookback window; must be >20 and <=365."),
    ):
        if not store.get_portfolio(portfolio_id):
            raise HTTPException(404, "Unknown portfolio")
        series_a = _return_series(store.price_history(symbol_a.upper(), limit_days=window + 1))
        series_b = _return_series(store.price_history(symbol_b.upper(), limit_days=window + 1))
        if series_a is None or series_b is None:
            return {
                "symbol_a": symbol_a.upper(), "symbol_b": symbol_b.upper(),
                "available": False,
                "reason": (
                    "One or both symbols have price bars with inconsistent or missing "
                    "adjusted_close values; a reliable like-for-like return series cannot "
                    "be derived without silently falling back to raw close."
                ),
            }
        aligned_a, aligned_b = _align_series(series_a, series_b)
        result = portfolio_calc.pearson_correlation(aligned_a, aligned_b)
        return {"symbol_a": symbol_a.upper(), "symbol_b": symbol_b.upper(), **result}

    def _value_portfolio(portfolio_id: int) -> list[portfolio_calc.HoldingValuation]:
        holdings = store.list_holdings(portfolio_id)
        latest_prices = {}
        for holding in holdings:
            bars = store.price_history(holding["symbol"], limit_days=1)
            if bars:
                latest_prices[holding["symbol"]] = bars[0]
        return [portfolio_calc.value_holding(h, latest_prices) for h in holdings]

    def _check_evidence_ownership(symbol: str, evidence_id: int | None) -> None:
        if evidence_id is None:
            return
        doc = store.get_evidence(evidence_id)
        if not doc:
            raise HTTPException(422, f"Unknown evidence_id {evidence_id}")
        if doc["symbol"] != symbol:
            raise HTTPException(422, f"evidence_id {evidence_id} belongs to {doc['symbol']}, not {symbol}")

    def _parse_statements(raw: str, filename: str, symbol: str) -> tuple[list[StatementFigureIn], list[dict]]:
        rows: list[StatementFigureIn] = []
        errors: list[dict] = []
        if filename.lower().endswith(".json"):
            try:
                records = json.loads(raw)
            except json.JSONDecodeError as exc:
                return [], [{"row": 0, "error": f"Invalid JSON: {exc}"}]
            if not isinstance(records, list):
                return [], [{"row": 0, "error": "JSON must be a list of statement objects"}]
        else:
            records = list(csv.DictReader(io.StringIO(raw)))
        for index, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                errors.append({"row": index, "error": "Each statement record must be a JSON object"})
                continue
            cleaned = {k: (None if v == "" else v) for k, v in record.items()}
            try:
                figure = StatementFigureIn.model_validate({**cleaned, "symbol": symbol})
            except ValidationError as exc:
                errors.append({"row": index, "error": exc.errors()[0]["msg"] if exc.errors() else str(exc)})
                continue
            if figure.evidence_id is None and not figure.source_note.strip():
                errors.append({"row": index, "error": "A figure without evidence_id requires a source_note"})
                continue
            rows.append(figure)
        return rows, errors

    return router


def _return_series(bars: list[dict]) -> list[tuple[str, str, Decimal]] | None:
    """Day-over-day returns, oldest first, each keyed by its exact
    (start_date, end_date) interval rather than only the end date, so a
    multi-day return spanning a gap is never later compared against a
    different symbol's one-day return over an overlapping end date.

    Requires every bar to carry `adjusted_close` (splits/dividends already
    accounted for). If any bar in the window is missing it, the whole series
    is inconsistent and `None` is returned (available:false upstream) rather
    than silently falling back to raw `close` for just that bar — mixing
    adjusted and unadjusted bases within one series would introduce
    artificial jumps around corporate actions.
    """
    ordered = sorted(bars, key=lambda b: b["observed_on"])
    if any(b.get("adjusted_close") is None for b in ordered):
        return None
    series: list[tuple[str, str, Decimal]] = []
    for previous, current in zip(ordered, ordered[1:]):
        prev_close = Decimal(previous["adjusted_close"])
        if prev_close == 0:
            continue
        cur_close = Decimal(current["adjusted_close"])
        ret = (cur_close - prev_close) / prev_close
        series.append((previous["observed_on"], current["observed_on"], ret))
    return series


def _align_series(series_a: list[tuple[str, str, Decimal]] | None,
                  series_b: list[tuple[str, str, Decimal]] | None) -> tuple[list[Decimal], list[Decimal]]:
    """Keep only returns whose exact (start_date, end_date) interval occurs in
    both series — comparing a 2-day return against another symbol's 1-day
    return just because they share an end date would compare different
    economic intervals."""
    if series_a is None or series_b is None:
        return [], []
    map_a = {(start, end): ret for start, end, ret in series_a}
    map_b = {(start, end): ret for start, end, ret in series_b}
    shared_intervals = sorted(set(map_a) & set(map_b))
    return [map_a[i] for i in shared_intervals], [map_b[i] for i in shared_intervals]


def _parse_holdings(raw: str, filename: str) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    errors: list[dict] = []
    seen_symbols: set[str] = set()
    if filename.lower().endswith(".json"):
        try:
            records = json.loads(raw)
        except json.JSONDecodeError as exc:
            return [], [{"row": 0, "error": f"Invalid JSON: {exc}"}]
        if not isinstance(records, list):
            return [], [{"row": 0, "error": "JSON must be a list of holding objects"}]
    else:
        broker_rows = _parse_broker_holdings_export(raw)
        if broker_rows is not None:
            return broker_rows
        records = list(csv.DictReader(io.StringIO(raw)))
    if len(records) > 500:
        return [], [{"row": 0, "error": "Import is capped at 500 rows per file"}]
    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            errors.append({"row": index, "error": "Each holding record must be a JSON object"})
            continue
        cleaned = {k: (None if v == "" else v) for k, v in record.items()}
        try:
            row = HoldingRow.model_validate(cleaned)
        except (ValidationError, InvalidOperation) as exc:
            message = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) and exc.errors() else str(exc)
            errors.append({"row": index, "error": message})
            continue
        if row.symbol in seen_symbols:
            errors.append({"row": index, "error": f"Duplicate symbol {row.symbol!r}; each symbol may appear once"})
            continue
        seen_symbols.add(row.symbol)
        rows.append({
            "symbol": row.symbol, "units": row.units, "cost_basis_total": row.cost_basis_total,
            "sector": row.sector, "exposure_notes": row.exposure_notes, "is_cash": row.is_cash,
            "manual_aud_value": row.manual_aud_value, "valuation_date": row.valuation_date,
        })
    return rows, errors


def _parse_broker_holdings_export(raw: str) -> tuple[list[dict], list[dict]] | None:
    """Normalize a dated ASX broker holdings export into previewable positions."""
    lines = raw.lstrip("\ufeff").splitlines()
    header_index = None
    for index, line in enumerate(lines):
        names = {name.strip().casefold() for name in next(csv.reader([line]))}
        if {"code", "avail units", "purchase $", "mkt value $"}.issubset(names):
            header_index = index
            break
    if header_index is None:
        return None

    date_match = re.search(
        r"\b(\d{1,2}\s+[A-Za-z]{3}\.?\s+\d{4})\b",
        "\n".join(lines[:header_index]), re.IGNORECASE,
    )
    valuation_date = None
    if date_match:
        date_text = date_match.group(1).replace(".", "")
        try:
            valuation_date = datetime.strptime(date_text, "%d %b %Y").date()
        except ValueError:
            valuation_date = None
    if valuation_date is None:
        return [], [{
            "row": header_index + 1,
            "error": "Broker holdings export must contain a parseable dated 'As of' snapshot before the header",
        }]

    records = csv.DictReader(io.StringIO("\n".join(lines[header_index:])))
    output: list[dict] = []
    errors: list[dict] = []
    seen: set[str] = set()
    for row_number, record in enumerate(records, start=header_index + 2):
        symbol = (record.get("Code") or "").strip().upper()
        units_text = (record.get("Avail Units") or "").strip().replace(",", "")
        if not symbol or not units_text:
            continue
        if symbol in ("SUBTOTAL", "TOTAL"):
            continue
        if not re.fullmatch(r"[A-Z0-9]{1,8}(?:\.AX)?", symbol):
            errors.append({"row": row_number, "error": f"Invalid ASX code {symbol!r}"})
            continue
        symbol = symbol if symbol.endswith(".AX") else f"{symbol}.AX"
        if symbol in seen:
            errors.append({"row": row_number, "error": f"Duplicate symbol {symbol!r}"})
            continue
        seen.add(symbol)
        try:
            units = Decimal(units_text)
            purchase_raw = (record.get("Purchase $") or "").strip().replace(",", "")
            market_raw = (record.get("Mkt Value $") or "").strip().replace(",", "")
            purchase_price = Decimal(purchase_raw) if purchase_raw else None
            market_value = Decimal(market_raw) if market_raw else None
            if not units.is_finite() or any(
                value is not None and not value.is_finite()
                for value in (purchase_price, market_value)
            ):
                raise InvalidOperation
            if purchase_price is not None and purchase_price < 0:
                raise ValueError("Purchase price cannot be negative")
            if market_value is not None and market_value < 0:
                raise ValueError("Market value cannot be negative")
            candidate = {
                "symbol": symbol,
                "units": str(units),
                "cost_basis_total": (
                    str(units * purchase_price)
                    if purchase_price is not None else None
                ),
                "sector": None,
                "exposure_notes": f"Imported broker holdings snapshot dated {valuation_date.isoformat()}",
                "is_cash": False,
                "manual_aud_value": str(market_value) if market_value is not None else None,
                "valuation_date": valuation_date.isoformat(),
            }
            row = HoldingRow.model_validate(candidate)
        except (InvalidOperation, ValueError, ValidationError) as exc:
            message = (
                exc.errors()[0]["msg"]
                if isinstance(exc, ValidationError) and exc.errors()
                else str(exc) if isinstance(exc, ValueError) and str(exc)
                else "Invalid units, purchase price, or market value"
            )
            errors.append({"row": row_number, "error": message})
            continue
        output.append(row.model_dump())
    if not output and not errors:
        errors.append({"row": header_index + 1, "error": "No holding rows found in broker export"})
    return output, errors


def _parse_watchlist_csv(raw: str) -> tuple[list[str], list[dict], list[str]]:
    reader = csv.DictReader(io.StringIO(raw.lstrip("\ufeff")))
    if not reader.fieldnames:
        raise ValueError("CSV is empty or has no header row")
    fields = {
        field.strip().casefold(): field
        for field in reader.fieldnames if field is not None
    }
    if "code" not in fields:
        raise ValueError("Watchlist CSV requires a Code column")
    symbols: list[str] = []
    companies: list[dict] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for row_number, record in enumerate(reader, start=2):
        raw_code = (record.get(fields["code"]) or "").strip().upper()
        if not raw_code:
            continue
        exchange = (
            (record.get(fields.get("exch", fields.get("exchange", ""))) or "")
            .strip().upper()
        )
        name_key = fields.get("name")
        company_name = (record.get(name_key) or "").strip() if name_key else ""
        if exchange and exchange not in ("ASX", "AUSTRALIAN SECURITIES EXCHANGE"):
            warnings.append(f"Row {row_number}: {raw_code} skipped because exchange is {exchange!r}.")
            continue
        if not exchange and not company_name and (name_key or "exch" in fields or "exchange" in fields):
            warnings.append(f"Row {row_number}: {raw_code} skipped because its company metadata is blank.")
            continue
        if not re.fullmatch(r"[A-Z0-9]{1,8}(?:\.AX)?", raw_code):
            warnings.append(f"Row {row_number}: invalid ASX code {raw_code!r} skipped.")
            continue
        symbol = raw_code if raw_code.endswith(".AX") else f"{raw_code}.AX"
        if symbol in seen:
            warnings.append(f"Row {row_number}: duplicate {symbol} skipped.")
            continue
        seen.add(symbol)
        symbols.append(symbol)
        if company_name:
            sector_key = fields.get("sector")
            industry_key = fields.get("industry")
            sector = (
                (record.get(sector_key) or "").strip() if sector_key else ""
            ) or (
                (record.get(industry_key) or "").strip() if industry_key else ""
            )
            companies.append({
                "symbol": symbol, "name": company_name,
                "sector": sector or None,
            })
        elif exchange:
            warnings.append(f"Row {row_number}: {symbol} added without company metadata.")
        if len(symbols) > 50:
            raise ValueError("Watchlist import is capped at 50 distinct ASX symbols")
    if not symbols:
        raise ValueError("No valid ASX symbols were found in the CSV")
    return symbols, companies, warnings
