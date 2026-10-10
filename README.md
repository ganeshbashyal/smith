# Mr Smith

Standalone, local-first personal assistant for Python 3.12, FastAPI, SQLite and
Ollama. This is a fresh foundation, not a migration of an existing application;
it is independent of Aurora. Financial sentiment is not financial advice.

## Setup (Windows PowerShell)

Run from your project checkout:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
ollama pull llama3.1:8b
ollama pull gemma4:26b
$env:MR_SMITH_ADMIN_TOKEN = "choose-a-private-local-token"
.\.venv\Scripts\python.exe -m uvicorn assistant_api.main:app --reload
```

Ollama must be running at `http://127.0.0.1:11434`. Open
[the widget](http://127.0.0.1:8000/morning),
[model admin](http://127.0.0.1:8000/admin), or
[API docs](http://127.0.0.1:8000/docs).
Do not expose the server to a network: chat, model status and source evidence
are intended for trusted local use. Admin mutations require the environment
token; no token or secret is embedded in the application or browser storage.

## Architecture

`request -> deterministic router -> domain-filtered SQLite retrieval ->
allowlisted HTTP tools -> serialized Ollama generation -> SQLite history`

- `personas.py`: immutable General, Science, Engineering, Building, Finance,
  Business, Health, News, Sports and Automotive profiles, each with domain-specific
  brief and research formats. `/api/personas` exposes labels and both formats.
- `domain_personas.py`: developed non-finance reasoning frameworks, source
  standards, uncertainty handling, safety limits and explicit capability boundaries.
- `finance/__init__.py`: evidence-led market intelligence persona and separate brief/deep
  research templates: macro-to-company transmission, narrative versus reality,
  financial quality, positioning, counter-theses and invalidation conditions.
- `finance/` (repository, calculators, portfolio, market_data, evidence, api):
  the deterministic finance data/valuation/portfolio module described below in
  **Finance module (deep dive)** — separate from, and not consulted by, the
  chat persona's free-text reasoning.
- `routing.py`: ordered word-boundary rules, explicit persona overrides,
  knowledge categories, tool selection, model and output format. Health and
  building rules take precedence for ambiguous requests. Routing considers the
  question, not article text or model-generated instructions.
- `storage.py`: persistent daily model, attributed text documents, content-hash
  duplicate detection per source/category/type, deterministic token-overlap
  retrieval and answer history. Identical text from different sources retains
  separate attribution.
  Relevance is **not** evidence confidence. Source types remain separate:
  user context, source document, manufacturer evidence, regulatory source and
  working note. User context is never treated as authoritative evidence.
- `tools.py`: optional BBC news, Yahoo finance news and explicit `BHP.AX`-style
  quotes, plus F1 standings. Each result has a source, timestamp and explicit
  disabled/error status. External feeds are off by default; enable with
  `$env:MR_SMITH_EXTERNAL_FEEDS = "1"`. No arbitrary URL fetching or LLM tool calls.
- `models.py`: one process-wide lock spans selection, unloading, loading and
  generation. The installed-model list is checked before switching. Other
  loaded Ollama models are unloaded before the requested model is loaded; a
  failed unload aborts the operation. A daily selection is persisted only after
  successful activation. Research uses Gemma without overwriting the daily
  preference; the next daily request switches back.

Run **one Uvicorn worker** and use a dedicated Ollama instance. Other applications
or multiple server processes can bypass the in-process lock. Models expire
after five idle minutes; `/api/models` queries Ollama for actual loaded models,
health and last generation latency. First requests can be slow on CPU. Missing
models or Ollama failures return an explicit HTTP 502, never a fabricated answer.

Configuration: `MR_SMITH_DATA_DIR` (default `data`), `OLLAMA_URL`,
`MR_SMITH_DAILY_MODEL` (default `llama3.1:8b`),
`MR_SMITH_RESEARCH_MODEL` (default `gemma4:26b`),
`MR_SMITH_ADMIN_TOKEN`, `MR_SMITH_EXTERNAL_FEEDS`.

## API examples

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/route -Method Post -ContentType "application/json" -Body '{"question":"ASX BHP.AX news"}'
Invoke-RestMethod http://127.0.0.1:8000/api/chat -Method Post -ContentType "application/json" -Body '{"question":"Explain the risks and catalysts","persona":"finance"}'
Invoke-RestMethod http://127.0.0.1:8000/api/research -Method Post -ContentType "application/json" -Body '{"question":"What do I need to understand from this article?","material":"Article text or notes","persona":"building"}'
$headers = @{ Authorization = "Bearer $env:MR_SMITH_ADMIN_TOKEN" }
Invoke-RestMethod http://127.0.0.1:8000/api/admin/model -Method Post -Headers $headers -ContentType "application/json" -Body '{"model":"llama3.1:8b"}'
Invoke-RestMethod http://127.0.0.1:8000/api/admin/documents -Method Post -Headers $headers -ContentType "application/json" -Body '{"title":"Thermal report","category":"manufacturer","source_type":"manufacturer_evidence","source":"documents/report.txt","content":"Attributed report text"}'
```

`/api/research` accepts the documented `question` and `material` fields; persona
is optional. Answers return `answer`, `route`, attributed `sources`, tool results,
`updated_at` and warnings. `/api/route` previews daily routing without contacting
Ollama. `/api/personas` lists profiles; `/api/models` shows model state.
Financial answers request sentiment, qualitative confidence, catalysts and
risks. These are model-generated assessments, not calibrated probabilities.
Sources are traceable, but generated citations and claims require verification.
Generation uses a 16,384-token context and a 1,024-token output cap (3,072 tokens
for all research personas). A conservative
12,000-character budget for the serialized question, material and evidence
rejects oversized requests with HTTP 422 instead of silently truncating them;
shorten articles or narrow the question when this happens.

The three widget tabs provide question-driven brief, finance and research views,
with loading/error states, timestamps, source links and system dark mode.
Scheduled morning cards, watchlists, football integration, PDF/URL ingestion,
full-text indexing, research exports and history management UI remain future
milestones. Plain-text ingestion is available through the admin documents API;
the application does not automatically read private files from disk.

## Financial market intelligence

The Finance persona separates facts, evidence, interpretation, hypotheses and
conclusions. It prioritizes primary sources, labels missing or stale data, and
uses qualitative uncertainty rather than fabricated probabilities. Finance
research requests select `financial_research`, a compact 20-section intelligence
template covering thesis, macro, fundamentals, positioning, valuation, scenarios,
risks, conviction and invalidation. Other research personas keep their existing
format. On Research & Deep Work, choose **Financial market intelligence** for
questions whose subject is only apparent from the pasted article.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/research -Method Post -ContentType "application/json" -Body '{"question":"Challenge the investment thesis and trace RBA rates into company cash flow.","material":"Paste dated, attributed company evidence here.","persona":"finance"}'
```

Daily financial analysis still uses the saved daily model (Llama by default);
deep financial research uses the configured research model (Gemma by default).
Neither the persona nor article text can trigger additional tools or models.
Smart Money Score, numeric conviction and a live institutional-ownership ledger
remain unavailable: they would require continuous proprietary data feeds this
project deliberately excludes. Persistent versioned company theses, watchlists,
deterministic valuation and long-only portfolio analytics are implemented as a
separate, explicitly admin-authenticated module — see **Finance module (deep
dive)** below — not inferred by the chat persona itself.

## All persona personalities

| Persona | Reasoning focus | Evidence and boundaries |
|---|---|---|
| General | Practical decisions, priorities, dependencies, kindness and fairness | User context is not fact verification; proposed tasks are not executed tasks |
| Science | Mechanisms, study design, replication, competing hypotheses, technology readiness | Primary research over headlines; no invented consensus or experiments |
| Engineering | Systems, automation, failure modes, commissioning and acceptance criteria | Exact manuals and measurements; no safety bypasses or claimed certification |
| Building | Australian climate, assemblies, moisture, thermal and lifecycle performance | Manufacturer versus regulatory evidence; jurisdiction/edition required for compliance |
| Finance | Macro-to-company intelligence, counter-theses, valuation and invalidation | Dated sources and explicit assumptions; no invented live data or scores |
| Business | Customer evidence, unit economics, strategy, execution and decision gates | Proposed metrics are not facts; legal/tax claims need qualified review |
| Health | Evidence literacy, benefits/harms, clinician questions and urgent-care awareness | General information only; no diagnosis, prescribing or delay of emergency care |
| News | Timelines, provenance, corroboration, disputed claims and context | Headlines are not full articles; no fabricated breaking updates |
| Sports | Football tactics, F1 race strategy, season-aware performance analysis | Manchester United, Real Madrid and Ferrari context; no invented live scores or betting certainty |
| Automotive | Vehicle-specific diagnosis, ownership trade-offs and safe verification | Exact model/year service evidence; no fabricated specifications or unsafe repairs |

Choose any persona in **Morning Brief** or **Research & Deep Work**, or send its
lowercase `persona` name to `/api/chat` or `/api/research`. Financial Analysis
remains locked to Finance. Automatic mode uses the ordered deterministic rules;
ambiguous questions can use an explicit persona. General and other overrides
cannot remove shared medical/compliance/safety boundaries. Each research persona
has its own analytical sections while non-finance `route.output_format` remains
`research` for API compatibility. Daily non-finance routes retain `brief`.
All daily requests use the saved active-model preference; all research uses the
configured research model, never an additional concurrently loaded model.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/chat -Method Post -ContentType "application/json" -Body '{"question":"Help prioritize these project tasks.","persona":"general"}'
Invoke-RestMethod http://127.0.0.1:8000/api/research -Method Post -ContentType "application/json" -Body '{"question":"Assess competing fault hypotheses and propose acceptance criteria.","material":"Paste attributed measurements and equipment documentation here.","persona":"engineering"}'
```

Personas are analytical frameworks, not extra integrations. No additional feed,
device access, clinician service, vehicle diagnostic interface or autonomous
action is enabled by selecting a personality. Domain retrieval remains isolated,
with shared user context. Engineering, Building, Health and Automotive safety
guidance does not replace site procedures or qualified professional assessment.

## General knowledge and current updates

Smith can explain stable concepts using local model training knowledge with no
documents required: inflation, PLC scan cycles, heat transfer and football rules,
for example. This knowledge is fallible, not independently verified and not a
live data source. Conceptual answers skip irrelevant thesis/score checklists.
Relevant retrieved evidence is preferred when supplied; sources are never
invented to support training knowledge. Mixed questions can receive a useful
conceptual answer even when their current-data portion cannot be established.

With `MR_SMITH_EXTERNAL_FEEDS=1`, explicit current/latest questions select approved
sources deterministically. New installations remain offline by default.

| Source | Coverage |
|---|---|
| BBC general, technology, science RSS | Recent reporting and feed summaries, not full article access |
| NASA official RSS | Space/science announcements |
| RBA official media-release RSS | Published releases, not a guaranteed current cash-rate snapshot |
| Yahoo Finance RSS/chart | Finance reporting and explicit `.AX` quotes; prices can be delayed |
| Jolpica F1 standings | Published standings, not live timing or football results |

Feed items are relevance-filtered and capped at three per source. They include
source links, publisher, publication date when supplied, content scope and
freshness. `recent` means within seven days for reporting, one day for quote
observations and fourteen days for dated standings; it does not mean verified,
real-time or sufficient for every question. Missing/invalid dates are `unknown`;
older dates are `stale`. Fetch time never replaces an observation date.

SQLite caches successful snapshots for fifteen minutes (two minutes for quotes).
Cache hits preserve original timestamps; refresh errors return explicit failure
instead of silently serving expired data. Check **Refresh approved sources** in
the widget or send `"refresh_sources": true` to bypass cache. Admin and
`GET /api/sources` expose network status, catalog and last request results.
`knowledge_basis` in answers describes evidence availability, not verified
claim-level provenance.

No private documents, prompts or personal context are sent to public feeds.
Only a validated explicit quote symbol goes into the Yahoo request; other
endpoints are fixed. Selecting a persona never permits arbitrary URL fetching.
Current clinical guidelines, compliance determinations, institutional ownership,
recalls and unsupported sports scores remain outside connected coverage.
Generated history and cached reporting do not train Llama or become approved
personal memories. There is no background monitoring.

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/chat -Method Post -ContentType "application/json" -Body '{"question":"Explain inflation simply.","persona":"finance"}'
Invoke-RestMethod http://127.0.0.1:8000/api/chat -Method Post -ContentType "application/json" -Body '{"question":"Latest NASA space news","refresh_sources":true}'
```

## Finance module (deep dive)

All endpoints live under `/api/finance/*` and a `/finance` page (token kept
in-memory in a password input, never `localStorage`), reusing the **same**
admin Bearer token as `/api/admin/*` — there is no separate finance secret.
If `MR_SMITH_ADMIN_TOKEN` is unset, finance endpoints return HTTP 503 exactly
like the existing admin endpoints; set the environment variable to a private
value before relying on any finance mutation.

```powershell
$env:MR_SMITH_ADMIN_TOKEN = "choose-a-private-local-token"
$headers = @{ Authorization = "******" }
Invoke-RestMethod http://127.0.0.1:8000/api/finance/securities -Method Post -Headers $headers -ContentType "application/json" -Body '{"symbol":"BHP","name":"BHP Group","sector":"Materials"}'
```

### Data model and provenance

Companies (`fin_securities`), evidence documents (`fin_evidence` + bounded
2,000-character passages), point-in-time statement figures (`fin_statements`),
versioned investment theses (`fin_thesis_revisions`), watchlists/price
history/alerts/catalysts, immutable valuation runs, and long-only portfolios/
holdings are stored additively in the existing `mr_smith.sqlite3`. The first
time the finance tables are created against a pre-existing database, a
timestamped, size- and `PRAGMA integrity_check`-verified backup
(`mr_smith.pre-finance-migration-<UTC timestamp>.sqlite3.bak`) is written next
to it before any new table is created; personal history/settings tables are
untouched. Every statement figure either traces to a confirmed evidence
document or requires an explicit manual-entry source note — there is no silent
"no source" path. Missing figures are always `null`, never coerced to zero.

Evidence accepts pasted text or a PDF upload (`pypdf`, text layer only, 25MB
cap); scanned/encrypted/unreadable PDFs return an explicit `limitations`
string instead of guessing or silently dropping content — OCR is out of scope.
A recent evidence document for a security is also surfaced as attributed
context in `/api/chat` and `/api/research` by passing `"entity_symbol":"BHP"`
on the request.

### Watchlists, price history and alerts

Watchlists hold up to 50 deduplicated symbols. `POST
/api/finance/watchlists/{id}/refresh` fetches at most one year of daily bars
per symbol from Yahoo's public chart endpoint (bounded concurrency, a single
retry on HTTP 429, raw vs. adjusted close kept distinct), de-duplicates
already-seen `(symbol, date)` observations, and raises an in-app alert when a
configured `price_alert_threshold_pct` is crossed between the two most recent
observations. This is a best-effort, potentially delayed public source — not a
broker or paid feed, and not scheduled; refreshes are always user-initiated.

### Deterministic valuation

`POST /api/finance/securities/{symbol}/valuation/ratios` computes, using
`Decimal` only (never `float`): market cap, net debt, enterprise value, P/E,
EV/EBITDA, free cash flow, FCF yield, dividend yield and cash conversion. Any
ratio with a missing, zero or negative denominator returns an explicit
`not_meaningful` reason instead of a fabricated number, alongside the exact
formula used.

`POST /api/finance/securities/{symbol}/valuation/dcf` computes a FCFF DCF per
scenario (`discount_rate` must exceed `terminal_growth_rate` or the request is
rejected with HTTP 422), an optional probability-weighted expected value per
share (withheld with a stated reason unless every scenario has a probability
and they sum to exactly 1), and a margin-of-safety figure
`(fair_value - price) / fair_value` when a current price is supplied. Every
ratio and DCF call is saved as an **immutable** `fin_valuation_runs` row
(inputs + outputs), so past valuations are never silently recomputed in place.

### Portfolio analytics

Long-only shares/ETFs/AUD cash holdings import via CSV or JSON with an
atomic preview/confirm flow: `holdings/preview` parses and validates without
writing; `holdings/confirm` rejects the entire batch on any invalid row (no
partial import) and otherwise atomically replaces the portfolio's holdings.
Manual overseas holdings use a user-supplied `manual_aud_value` (no automatic
or fabricated FX conversion). CSV/JSON columns:
`symbol, units, cost_basis_total, sector, exposure_notes, is_cash,
manual_aud_value, valuation_date`.

`GET /api/finance/portfolios/{id}/analysis` reports coverage-aware weights and
sector concentration — holdings with no resolvable value are listed under
`missing_valuations` and excluded from weights, never silently renormalized
in. `POST /api/finance/portfolios/{id}/scenario` applies user-defined,
non-overlapping percentage shocks (first matching rule per holding wins) and
reports baseline vs. shocked totals. Correlation analysis requires a minimum
number of aligned return observations or explicitly reports itself as
unavailable; there is no Sharpe/VaR style metric, since no defined method or
sufficient history is implemented for one.

### Explicit exclusions

No paid or broker data feeds, no trade execution, no scheduling/cron, no OCR,
no tax calculations, no short positions or derivatives, and no proprietary
"scores" (e.g. no Smart Money Score). ETF look-through requires evidence you
supply; it is never inferred by the model. Private holdings are never sent to
any external provider — only the approved quote symbol is used for public
price lookups.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Tests use temporary SQLite databases and mocked HTTP transports; they do not
require model downloads or contact external services.
