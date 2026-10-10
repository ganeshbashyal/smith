"""Finance API: /api/finance/* endpoints.

Every private read/write/context path here requires the existing admin
Bearer-token dependency used by the rest of the app. The router is built with
the same settings/store/admin dependency the main app already constructed, so
there is exactly one auth mechanism and no embedded/alternate token.
"""
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import csv
import io
import json
import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query, UploadFile
from pydantic import ValidationError

from assistant_api.finance import calculators, market_data
from assistant_api.finance import portfolio as portfolio_calc
from assistant_api.finance.evidence import (
    content_hash, extract_pdf_text, passages_with_page_numbers, plain_text_passages,
)
from assistant_api.finance.repository import FinanceStore
from assistant_api.finance.schemas import (
    CatalystIn, DCFRequest, DCFSensitivityRequest, EvidenceDocumentIn, HoldingRow,
    PdfEvidenceMeta, RatioValuationRequest, Security, ShockIn, StatementFigureIn,
    ThesisRevisionIn, WatchlistIn,
)

logger = logging.getLogger(__name__)

SCALE_MULTIPLIER = {"units": Decimal(1), "thousands": Decimal(1000), "millions": Decimal(1_000_000)}
MONETARY_FIGURE_FIELDS = (
    "cash_and_equivalents", "total_debt", "revenue", "ebitda", "net_income",
    "operating_cash_flow", "capital_expenditure", "dividends_paid",
)


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
                 external_feeds_enabled=lambda: False) -> APIRouter:
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
                    observed_at_utc=bar.get("observed_at_utc"), exchange_timezone=bar.get("exchange_timezone"),
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
