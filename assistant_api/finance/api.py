"""Finance API: /api/finance/* endpoints.

Every private read/write/context path here requires the existing admin Bearer
token dependency used by the rest of the app. The router is built with the
same settings/store/admin dependency the main app already constructed, so
there is exactly one auth mechanism.
"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import csv
import io
import json
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile

from assistant_api.finance import calculators, market_data
from assistant_api.finance import portfolio as portfolio_calc
from assistant_api.finance.evidence import content_hash, extract_pdf_text, split_passages
from assistant_api.finance.repository import FinanceStore
from assistant_api.finance.schemas import (
    CatalystIn, DCFRequest, EvidenceDocumentIn, Security,
    StatementFigureIn, ThesisRevisionIn, WatchlistIn,
)

logger = logging.getLogger(__name__)


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


def build_router(*, get_store, admin_dependency, feeds_client_getter) -> APIRouter:
    store: FinanceStore = _LazyStoreProxy(get_store)
    router = APIRouter(prefix="/api/finance", tags=["finance"], dependencies=[Depends(admin_dependency)])

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

    # -- Evidence -----------------------------------------------------------
    @router.post("/securities/{symbol}/evidence")
    async def add_evidence(symbol: str, body: EvidenceDocumentIn):
        symbol = symbol.upper()
        if not store.get_security(symbol):
            raise HTTPException(422, "Create the security before attaching evidence")
        passages = split_passages(body.text)
        digest = content_hash(body.text.encode())
        evidence_id, created = store.add_evidence(
            symbol, body.title, body.publisher, body.source_type, body.document_type,
            body.source_url, body.published_on, body.period, digest, passages,
        )
        return {"id": evidence_id, "created": created}

    @router.post("/securities/{symbol}/evidence/pdf")
    async def add_evidence_pdf(symbol: str, title: str, publisher: str, source_type: str,
                               document_type: str, file: UploadFile, source_url: str = "",
                               published_on: str | None = None, period: str | None = None):
        symbol = symbol.upper()
        if not store.get_security(symbol):
            raise HTTPException(422, "Create the security before attaching evidence")
        raw = await file.read()
        if len(raw) > 25_000_000:
            raise HTTPException(422, "PDF exceeds the 25MB import budget")
        pages, limitations = extract_pdf_text(raw)
        passages = [p for page in pages for p in split_passages(page)] if pages else []
        digest = content_hash(raw)
        published = date.fromisoformat(published_on) if published_on else None
        evidence_id, created = store.add_evidence(
            symbol, title, publisher, source_type, document_type, source_url,
            published, period, digest, passages or [""], limitations,
        )
        return {"id": evidence_id, "created": created, "pages": len(pages), "limitations": limitations}

    @router.get("/securities/{symbol}/evidence")
    async def list_evidence(symbol: str):
        return {"evidence": store.list_evidence(symbol.upper())}

    # -- Statements -----------------------------------------------------------
    @router.post("/securities/{symbol}/statements")
    async def save_statement(symbol: str, body: StatementFigureIn):
        symbol = symbol.upper()
        if not store.get_security(symbol):
            raise HTTPException(422, "Create the security before confirming statement figures")
        if body.evidence_id is None and not body.source_note.strip():
            raise HTTPException(422, "A figure without linked evidence requires a manual-entry source note")
        figures = body.model_dump(exclude={"symbol", "period", "fiscal_period_end", "currency", "scale",
                                           "consolidated", "evidence_id", "source_note"})
        statement_id = store.save_statement(
            symbol, body.period, body.fiscal_period_end, body.currency, body.scale,
            body.consolidated, figures, body.evidence_id, body.source_note,
        )
        return {"id": statement_id}

    @router.get("/securities/{symbol}/statements")
    async def list_statements(symbol: str):
        return {"statements": store.list_statements(symbol.upper())}

    # -- Thesis revisions -----------------------------------------------------
    @router.post("/securities/{symbol}/thesis")
    async def add_thesis(symbol: str, body: ThesisRevisionIn):
        symbol = symbol.upper()
        if not store.get_security(symbol):
            raise HTTPException(422, "Create the security before saving a thesis")
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
        return {
            "what_changed": changed_fields,
            "why_it_matters": "Revision carries evidence IDs: "
                              f"{sorted(set(after['evidence_ids']) - set(before['evidence_ids']))} newly linked",
            "thesis_impact": "Review changed_fields against linked evidence before treating this as decision-grade.",
        }

    @router.get("/securities/{symbol}/thesis/export")
    async def export_thesis(symbol: str):
        symbol = symbol.upper()
        revisions = store.list_thesis_revisions(symbol)
        if not revisions:
            raise HTTPException(404, "No thesis revisions saved")
        latest = revisions[-1]
        lines = [f"# {symbol} investment thesis (revision {latest['revision']})", "",
                "*Sentiment and scenarios are analytical tools, not financial advice.*", "",
                "## Thesis", latest["thesis"], "", "## Counter-thesis", latest["counter_thesis"] or "_None recorded_",
                "", "## Assumptions", latest["assumptions"] or "_None recorded_", "",
                "## Sensitivities", latest["sensitivities"] or "_None recorded_", "",
                "## Catalysts", latest["catalysts"] or "_None recorded_", "",
                "## Risks", latest["risks"] or "_None recorded_", "",
                "## Invalidation criteria", latest["invalidation_criteria"] or "_None recorded_"]
        return {"markdown": "\n".join(lines)}

    # -- Watchlists / price history / alerts ----------------------------------
    @router.post("/watchlists")
    async def create_watchlist(body: WatchlistIn):
        watchlist_id = store.create_watchlist(body.name, body.symbols, body.price_alert_threshold_pct)
        return store.get_watchlist(watchlist_id)

    @router.get("/watchlists")
    async def list_watchlists():
        return {"watchlists": store.list_watchlists()}

    @router.post("/watchlists/{watchlist_id}/refresh")
    async def refresh_watchlist(watchlist_id: int):
        watchlist = store.get_watchlist(watchlist_id)
        if not watchlist:
            raise HTTPException(404, "Unknown watchlist")
        client = feeds_client_getter()
        try:
            results = await market_data.refresh_history(client, watchlist["symbols"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        now = datetime.now(timezone.utc).isoformat()
        threshold = watchlist.get("price_alert_threshold_pct")
        summary = []
        for result in results:
            new_bars = 0
            for bar in result["bars"]:
                is_new = store.save_price_bar(
                    result["symbol"], bar["observed_on"], bar["currency"], bar["close"],
                    bar["adjusted_close"], bar["volume"], bar["retrieved_at"],
                )
                if is_new:
                    new_bars += 1
            summary.append({"symbol": result["symbol"], "status": result["status"],
                            "error": result["error"], "new_observations": new_bars})
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
    async def valuation_ratios(symbol: str, inputs: dict = Body(...)):
        symbol = symbol.upper()
        result = calculators.compute_ratios(inputs)
        run_id = store.save_valuation_run(symbol, "ratios", inputs, result.to_dict(), None)
        return {"id": run_id, **result.to_dict()}

    @router.post("/securities/{symbol}/valuation/dcf")
    async def valuation_dcf(symbol: str, body: DCFRequest):
        symbol = symbol.upper()
        try:
            report = calculators.compute_dcf_report(
                [s.model_dump() for s in body.scenarios], body.current_price,
            )
        except (ValueError, InvalidOperation) as exc:
            raise HTTPException(422, str(exc)) from exc
        result = {
            "scenarios": [s.__dict__ for s in report.scenarios],
            "probability_weighted_value_per_share": report.probability_weighted_value_per_share,
            "probability_weighted_reason": report.probability_weighted_reason,
            "margin_of_safety": report.margin_of_safety,
        }
        run_id = store.save_valuation_run(symbol, "dcf", body.model_dump(), result, None)
        return {"id": run_id, **result}

    @router.get("/securities/{symbol}/valuation/runs")
    async def valuation_runs(symbol: str):
        return {"runs": store.list_valuation_runs(symbol.upper())}

    # -- Portfolios --------------------------------------------------------
    @router.post("/portfolios")
    async def create_portfolio(name: str = Body(embed=True)):
        portfolio_id = store.create_portfolio(name)
        return {"id": portfolio_id, "name": name}

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

    @router.get("/portfolios/{portfolio_id}/analysis")
    async def portfolio_analysis(portfolio_id: int):
        if not store.get_portfolio(portfolio_id):
            raise HTTPException(404, "Unknown portfolio")
        holdings = store.list_holdings(portfolio_id)
        latest_prices = {}
        for holding in holdings:
            bars = store.price_history(holding["symbol"], limit_days=1)
            if bars:
                latest_prices[holding["symbol"]] = bars[0]
        valuations = [portfolio_calc.value_holding(h, latest_prices) for h in holdings]
        analysis = portfolio_calc.analyze_portfolio(valuations)
        return analysis.__dict__

    @router.post("/portfolios/{portfolio_id}/scenario")
    async def portfolio_scenario(portfolio_id: int, shocks: list[dict] = Body(...)):
        if not store.get_portfolio(portfolio_id):
            raise HTTPException(404, "Unknown portfolio")
        holdings = store.list_holdings(portfolio_id)
        latest_prices = {}
        for holding in holdings:
            bars = store.price_history(holding["symbol"], limit_days=1)
            if bars:
                latest_prices[holding["symbol"]] = bars[0]
        valuations = [portfolio_calc.value_holding(h, latest_prices) for h in holdings]
        return portfolio_calc.apply_shocks(valuations, shocks)

    return router


def _parse_holdings(raw: str, filename: str) -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    errors: list[dict] = []
    if filename.lower().endswith(".json"):
        try:
            records = json.loads(raw)
        except json.JSONDecodeError as exc:
            return [], [{"row": 0, "error": f"Invalid JSON: {exc}"}]
        if not isinstance(records, list):
            return [], [{"row": 0, "error": "JSON must be a list of holding objects"}]
    else:
        records = list(csv.DictReader(io.StringIO(raw)))
    for index, record in enumerate(records, start=1):
        try:
            symbol = str(record["symbol"]).strip().upper()
            if not symbol:
                raise ValueError("symbol is required")
            units = Decimal(str(record["units"]))
            cost_basis = record.get("cost_basis_total")
            manual_value = record.get("manual_aud_value")
            is_cash = str(record.get("is_cash", "")).strip().lower() in ("1", "true", "yes")
            valuation_date_raw = record.get("valuation_date")
            rows.append({
                "symbol": symbol, "units": str(units),
                "cost_basis_total": str(Decimal(str(cost_basis))) if cost_basis not in (None, "") else None,
                "sector": (record.get("sector") or "").strip() or None,
                "exposure_notes": record.get("exposure_notes", ""),
                "is_cash": is_cash,
                "manual_aud_value": str(Decimal(str(manual_value))) if manual_value not in (None, "") else None,
                "valuation_date": date.fromisoformat(valuation_date_raw) if valuation_date_raw else None,
            })
        except (KeyError, InvalidOperation, ValueError) as exc:
            errors.append({"row": index, "error": str(exc)})
    return rows, errors
