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

Or use the idempotent launcher, which reuses an existing `.venv`/install and
never hard-codes a token:

```powershell
.\scripts\start-mr-smith.ps1                                    # foreground dev mode, no admin features
.\scripts\start-mr-smith.ps1 -AdminToken "choose-a-private-local-token" -ExternalFeeds -Detached
```

Run `Get-Help .\scripts\start-mr-smith.ps1 -Full` for all parameters.

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
with loading/error states, timestamps, source links and system dark mode. The
Morning Brief tab also has a user-triggered source-aware card that checks the
approved general-news feed and shows publication dates, freshness and
disabled/error states. There is no background schedule; external feeds remain
off unless explicitly enabled. `/api/coverage/sports` reports that no approved
football fixtures/results source is connected; it does not substitute general
headlines for scores. The existing Jolpica feed is limited to published F1
driver standings, not live timing.
Private document and history management are available from `/admin`; the
application does not automatically read private files from disk or fetch
arbitrary URLs.

## Private knowledge documents

The `/admin` page supports authenticated local-service monitoring and document
management. It reports the Mr Smith process and local Ollama processes with
memory usage and cumulative CPU time. From the page you can start a local
Ollama server, gracefully unload models while keeping the service running, and
gracefully stop only a server started by that page; stopping waits for active
model work and unloads loaded models first. An Ollama process that was already
running independently is visible but is not stopped by Smith.
Service controls require the admin token and a loopback connection.

The page also supports authenticated private document search, detail,
revision-preserving edits and deletion. `/api/admin/documents` also supports
bounded listing and full-text search; list/search results omit document text.
Deleting a document also deletes its revision history. Updating a document
retains the previous version as an immutable revision. Admin routes require
`MR_SMITH_ADMIN_TOKEN`; the page holds the token only in its password input.

The admin page can import text-layer PDFs (maximum 25 MB, 500 pages and 1 MB
extracted text). Each non-empty page is stored separately with a page-number
citation. Scanned-image OCR and arbitrary URL or filesystem ingestion are not
supported. Before adding document revision or search schema to an existing
database, Smith writes a timestamped SQLite backup and checks its integrity.
Private data remains local and no document content is included in knowledge
status diagnostics.

The same page provides paginated private conversation history. Open an entry
to view its answer; delete removes it locally. A user-triggered **Download
Markdown** action is available for newly saved research answers and includes
captured source links, attribution metadata and an explicit verification
caveat. Older answers have no retroactively reconstructed evidence. History
schema migrations are preceded by a verified SQLite backup.

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
| Health | Evidence applicability, privacy, clinician questions and urgent-care-first handling | No diagnosis or prescribing; emergencies go to urgent local assistance (000 in Australia); no connected live clinical guidance |
| News | Timelines, provenance, corroboration, disputed claims and context | Headlines are not full articles; no fabricated breaking updates |
| Sports | Football tactics, F1 race strategy, season-aware performance analysis | Manchester United, Real Madrid and Ferrari context; no connected football scores/results or invented live facts |
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

Persona regression scenarios live in `tests/persona_eval_cases.py` and run via
`python -m pytest tests/test_persona_evaluations.py`. They exercise all ten
personas through the real API and routing with a mocked Ollama transport,
checking selected persona, allowed tools, output format, key domain boundaries,
shared safety instructions, current-source limitations, and separation of
untrusted research material. Engineering-specific manuals, Australian building
compliance, vehicle recalls, health guidance, football coverage and other
unsupported current claims are deliberately not routed to generic feeds as if
those feeds were authoritative. These deterministic checks verify the
application contract, not the factual quality of a model's generated answer;
they require no model download or live Ollama call.

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

### Year 12 study-notes corpus

`scripts/build_year12_knowledge.py` drafts Markdown study notes for a fixed set
of senior-secondary topics (Maths Methods, Physics, Chemistry, Biology,
Economics, Business Studies, English) using Mr Smith's **currently selected**
local Ollama model, then registers them in the document store via
`Store.add_document` so the `general`/`science`/`business` personas can
retrieve them like any other evidence.

This is **not model fine-tuning** - no local training pipeline exists, and no
model weights are changed. It is a curated, attributed retrieval corpus built
from the model's own general training knowledge, carrying the same
"not independently verified" caveat as any other conceptual answer, plus an
explicit per-note disclaimer to cross-check current syllabus/teacher guidance
before relying on it for assessment.

Safe to run unattended/overnight:
- Idempotent generation (skips topics whose file already exists) and
  idempotent ingestion (content-digest keyed; safe to re-run anytime).
- Never force-unloads another model - waits for Ollama to be free before each
  draft, so an in-flight chat/research request is never interrupted.
- Fully local: notes are written under `data/year12_knowledge/` (already
  excluded from git) and nothing leaves the local Ollama instance.

```powershell
python scripts/build_year12_knowledge.py                 # generate + ingest
python scripts/build_year12_knowledge.py --limit 5        # cap new topics this run
python scripts/build_year12_knowledge.py --ingest-only    # re-sync DB only, no generation
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
2,000-character passages keyed to real PDF page numbers, not chunk IDs),
versioned point-in-time statement figures (`fin_statements` +
`fin_statement_revisions` — re-saving a period never overwrites; every save
creates a new immutable revision and prior revisions remain queryable),
versioned investment theses (`fin_thesis_revisions`), watchlists/price
history/alerts/catalysts, immutable valuation runs, and long-only portfolios/
holdings are stored additively in the existing `mr_smith.sqlite3`. The first
time the finance tables are created against a pre-existing database, a
timestamped, size- and `PRAGMA integrity_check`-verified backup
(`mr_smith.pre-finance-migration-<UTC timestamp>.sqlite3.bak`) is written next
to it before any new table is created; personal history/settings tables are
untouched. Every statement figure (via single-entry or CSV/JSON
preview/confirm import) either traces to a confirmed evidence document you
own for that security or requires an explicit manual-entry source note — there
is no silent "no source" path, and linking someone else's evidence to your
security is rejected with HTTP 422. Missing figures are always `null`, never
coerced to zero.

Evidence accepts pasted text or a PDF upload (`pypdf`, text layer only, 25MB
cap, typed upload metadata validated before any parsing); scanned/encrypted/
unreadable PDFs return HTTP 422 instead of silently saving blank evidence.
Multi-chunk pages are preserved as a single page each — `page_count` always
reflects actual PDF pages, never internal text chunks. A recent evidence
document for a security is also surfaced as attributed context in
`/api/chat` and `/api/research` by passing `"entity_symbol":"BHP"` on the
request.

### Watchlists, price history and alerts

Watchlists hold up to 50 deduplicated **`.AX`-suffixed ASX symbols only**
(validated before any network call). `POST
/api/finance/watchlists/{id}/refresh` returns HTTP 503 unless the
`MR_SMITH_EXTERNAL_FEEDS=1` environment variable is set — refreshes never
silently attempt a network call. When enabled, it fetches at most one year of
daily bars per symbol from Yahoo's public chart endpoint (bounded
concurrency, a single retry on HTTP 429, raw vs. adjusted close kept
distinct), records each bar's actual Sydney exchange date (via
`exchangeTimezoneName`/`zoneinfo`, not naive UTC, so a late-session bar is
never misdated), de-duplicates already-seen `(symbol, date)` observations,
and rejects non-finite/negative bar values instead of recording them.
`POST /api/finance/alerts/evaluate` is a separate, user-initiated, idempotent
check (re-running it twice creates no duplicate alerts) that raises material
alerts for near-term catalysts and thesis-review alerts when evidence is
newer than the latest thesis revision — there is no background scheduler.

### Deterministic valuation

`POST /api/finance/securities/{symbol}/valuation/ratios` requires a
**confirmed statement revision** (`statement_period` +
`statement_fiscal_period_end`, not an arbitrary inline dict) and an explicit
`price_currency` on the request; a mismatch against the confirmed statement's
own currency is rejected with HTTP 422 (no automatic FX conversion — the
caller must supply a price already denominated in the statement's currency).
It computes, using `Decimal` only (never `float`) after converting the
statement's own monetary scale (units/thousands/millions) and share count to
the supplied price's date basis: market cap, net debt, enterprise value, P/E,
EV/EBITDA, free cash flow, FCF yield, dividend yield and cash conversion. Any
ratio with a missing, zero or negative denominator returns an explicit
`not_meaningful` reason instead of a fabricated number, alongside the exact
formula and the statement revision it was computed from.

`POST /api/finance/securities/{symbol}/valuation/dcf` computes a FCFF DCF per
scenario (`discount_rate` must exceed `terminal_growth_rate` or the request is
rejected with HTTP 422; scenario probabilities must each be in `[0, 1]`), an
optional probability-weighted expected value per share (withheld with a
stated reason unless every scenario has a probability and they sum to exactly
1), and a margin-of-safety figure `(fair_value - price) / fair_value` —
computed only when fair value is strictly positive and a dated current price
was explicitly supplied, otherwise withheld with a reason. `POST
.../dcf/sensitivity` re-runs a base scenario across discount-rate and
terminal-growth deltas; `GET .../dcf/{run_id}/export` produces a Markdown
export. Every ratio and DCF call is saved as an **immutable**
`fin_valuation_runs` row (inputs + outputs), so past valuations are never
silently recomputed in place.

### Portfolio analytics

Long-only shares/ETFs/AUD cash holdings import via CSV or JSON with an
atomic preview/confirm flow: `holdings/preview` parses and validates without
writing; `holdings/confirm` rejects the entire batch on any invalid row (no
partial import, 500-row file budget) and otherwise atomically replaces the
portfolio's holdings. Units must be positive, cash balances non-negative, all
monetary figures finite (no `NaN`/`Infinity`), symbols canonical and unique
within a file. Manual overseas holdings require a user-supplied, dated
`manual_aud_value` (no automatic or fabricated FX conversion); zero-valued
holdings are preserved, not dropped. Individual holdings can also be read,
added, edited and deleted one at a time via `GET/POST
/api/finance/portfolios/{id}/holdings` and `PUT/DELETE
.../holdings/{holding_id}`. A holding is only auto-valued from a watchlist
quote when the recorded bar currency is exactly `AUD`; a non-AUD or unknown
bar currency, or any future-dated manual valuation, is left unavailable
rather than silently priced.

`GET /api/finance/portfolios/{id}/analysis` reports a `covered_value_aud`
subtotal plus always-present `covered_weights`/`covered_sector_concentration`/
`covered_unrealized_gain_aud` computed **only** over holdings with a
resolvable value — holdings with no resolvable value are listed under
`missing_valuations`. The true, whole-portfolio `total_value_aud`/`weights`/
`sector_concentration`/`total_unrealized_gain_aud` are `null` (never silently
renormalized or substituted with the covered-only figures) whenever coverage
is incomplete, and only populated once every holding resolves. A
`holdings_detail` list reports market value, valuation source, currency and
staleness for **every** holding, not only the top-10 `largest_positions`.
`POST /api/finance/portfolios/{id}/scenario` applies user-defined,
non-overlapping percentage shocks (first matching rule per
holding wins, `pct` must be `>= -100` and finite) and reports
`covered_baseline_total_aud`/`covered_shocked_total_aud` always, with the true
`baseline_total_aud`/`shocked_total_aud` similarly withheld unless
`coverage_complete`. `GET /api/finance/portfolios/{id}/correlation` (bounded
`window` query, `>20` and `<=365` trading days) computes a Pearson
correlation between two symbols' returns derived purely from
**adjusted_close** (split/dividend-adjusted). If either symbol has any
recorded bar missing `adjusted_close`, the whole series is treated as
inconsistent and the endpoint reports `available: false` with a reason —
there is no per-bar fallback to raw `close`, which would otherwise mix
adjusted and unadjusted bases and introduce artificial jumps. Returns are
aligned by their exact `(start_date, end_date)` interval, not merely by a
shared end date, so a multi-day return spanning a data gap in one symbol is
never compared against a different-length interval in the other. The
endpoint explicitly reports `available: false` with a reason (not a
fabricated `0`) whenever aligned history is insufficient.

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
