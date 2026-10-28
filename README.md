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
$env:MR_SMITH_ADMIN_TOKEN = (& python -c "import secrets; print(secrets.token_urlsafe(32))")
.\.venv\Scripts\python.exe -m uvicorn assistant_api.main:app --reload
```

Or use the idempotent launcher, which reuses an existing `.venv`/install and
never hard-codes a token:

```powershell
.\scripts\start-mr-smith.ps1                                    # foreground dev mode, no admin features
.\scripts\start-mr-smith.ps1 -ExternalFeeds -Detached
```

Run `Get-Help .\scripts\start-mr-smith.ps1 -Full` for all parameters.

To start only the required services, wait for Ollama/Mr Smith readiness, verify
the selected model is installed, and open the combined chat/Morning Brief page:

```powershell
.\.venv\Scripts\python.exe .\scripts\start_mr_smith.py
```

The Python launcher reuses healthy local services and leaves unrelated
processes untouched. It does not download models, send a chat prompt, fetch
headlines, or set an admin token. Use `--no-browser` for service-only startup,
`--port 8001` for an alternate local app port, or `--help` for timeout options.
Chat and the user-triggered Morning Brief are modes on the same `/morning` page.

To configure admin access and start a server that receives the token in its
environment, use:

```powershell
.\.venv\Scripts\python.exe .\scripts\start_mr_smith_admin.py
```

This creates a random token, saves it in the current Windows user's environment
registry, passes it directly to the server, verifies it against the local admin
API, and opens `/admin`. The token is printed once for pasting into the page;
keep it private. If port 8000 is occupied, the script leaves that service alone
and starts Mr Smith on the next available local port. It does not stop or
replace existing processes.

Ollama must be running at `http://127.0.0.1:11434`. Open
[the widget](http://127.0.0.1:8000/morning),
[model admin](http://127.0.0.1:8000/admin), or
[API docs](http://127.0.0.1:8000/docs).
Do not expose the server to a network: chat, model status and source evidence
are intended for trusted local use. Admin mutations require the environment
token; use a unique, randomly generated token and do not commit or share it.
No token or secret is embedded in the application or browser storage. Responses
include browser security headers, and private admin/finance API responses are
marked `no-store`.

### Test and smoke checks (Windows PowerShell)

The project test wrapper creates a unique pytest base under the system temp
directory, not inside the checkout. Override its writable parent with
`-TempDirectory` or `MR_SMITH_TEST_TEMP`:

```powershell
.\scripts\test-mr-smith.ps1 -q
.\scripts\test-mr-smith.ps1 -TempDirectory "$env:LOCALAPPDATA\MrSmith\tests" -q
.\.venv\Scripts\python.exe scripts\smoke_mr_smith.py --temp-dir "$env:LOCALAPPDATA\MrSmith\smoke"
```

The smoke script uses a disposable SQLite database and mocked Ollama transport;
it does not contact a live model, alter `data\mr_smith.sqlite3`, or start a
network server. It uses the system temporary directory by default and accepts
`--temp-dir` or `MR_SMITH_SMOKE_TEMP` overrides. Its unique temporary files are
removed automatically.

Admin document metadata includes authority, issue/review dates, jurisdiction,
human-assessed confidence, supersession and review status. Existing documents
migrate additively with `pending_human_review`; setting `reviewed` requires a
source authority and human review date. Pending/candidate evidence remains
explicitly unverified in persona prompts.

### Governed knowledge and semantic evaluation

The admin document workflow is the curated corpus entry point: ingest only
attributed material, keep personal context separate from authoritative sources,
set jurisdiction and dates when relevant, and leave new material
`pending_human_review` until a person verifies it. Do not upload personal
background or business information unless it is intended to be stored in the
local SQLite database. Product/SKU mappings and compliance conclusions remain
human-reviewed; absence of a reliable match is not permission to infer one.
Fine-tuning is deferred until retrieval and measured answer quality demonstrate
a specific need.

`tests/semantic_eval_cases.jsonl` is an initial set of synthetic, human-labelled
behavioral expectations spanning all ten personas. It is not a complete 160
case release set, and it does not claim model-quality results. Review real
answers locally against each case and save one JSONL annotation per case with
these fields:

```json
{"case_id":"building-r-value-rw","corpus_version":"corpus-1","model":"llama3.2:latest","reviewer":"reviewer-id","ratings":{"routing_correct":true,"source_accuracy":true,"citation_completeness":true,"unsupported_claim_count":0,"refusal_quality":2,"answer_usefulness":2}}
```

Add `stale_detection` for freshness cases and `retrieval_relevance` for cases
with evidence. Boolean ratings use `true`/`false`; quality ratings use 0
(miss), 1 (partial), or 2 (meets expectation). Compare two manually reviewed
corpus/model runs without sending data to an external evaluator:

```powershell
.\.venv\Scripts\python.exe scripts\score_semantic_eval.py --annotations .\evaluation\candidate.jsonl --baseline .\evaluation\baseline.jsonl
```

The scorer reports routing/source/citation/freshness accuracy, unsupported
claim rate, retrieval relevance, answer quality, and paired-case regression.
Annotation JSONL may contain private prompts or responses; keep it local and
out of version control. Keep the model fixed between corpus versions when
measuring corpus regression; the scorer rejects a mixed-model run and requires
matching models for baseline comparisons.

The user-provided public-source list is cataloged in
[`knowledge/source_candidates.json`](knowledge/source_candidates.json) as
reference-only candidates. The catalog does not enable fetching. Verify each
source's official API/feed, current terms, licensing, attribution and rate
limits before creating a bounded runtime connector.

### Persona knowledge foundations

The v1 foundation pack under [`knowledge/modules`](knowledge/modules/README.md)
provides a reviewed-in-progress knowledge module for each persona. Validate or
re-import it into the active local database with:

```powershell
.\.venv\Scripts\python.exe scripts\ingest_knowledge_modules.py --check-only
.\.venv\Scripts\python.exe scripts\ingest_knowledge_modules.py
```

These are local working notes, not an exhaustive domain corpus or independent
authority. Every imported module remains `pending_human_review`; review and
update sources before treating it as verified. Re-imports are idempotent and
changed module content creates a revision. The module guide records current
coverage, limits, and the next domain-evidence steps.

### Offline database backup and recovery

Create a consistent, versioned archive from SQLite's online backup API, verify
it without changing the database, or restore it into a directory where
`mr_smith.sqlite3` does not already exist:

```powershell
.\.venv\Scripts\python.exe scripts\recover_mr_smith.py export --output .\backups\mr-smith-backup.zip
.\.venv\Scripts\python.exe scripts\recover_mr_smith.py verify --backup .\backups\mr-smith-backup.zip
.\.venv\Scripts\python.exe scripts\recover_mr_smith.py restore --backup .\backups\mr-smith-backup.zip --data-dir .\recovered-data
```

Exports include the application/finance schema versions, a SHA-256 checksum
and an SQLite integrity check. Restore refuses to replace an existing
database; restore to a separate directory first, verify the recovered data,
then follow a deliberate application cutover procedure. Treat backup archives
as private user data.
The database records an application schema version separately from the
finance migration version; future persistent schema changes must advance the
appropriate version and include an additive migration and recovery regression
test.

### Opt-in live acceptance (Windows PowerShell)

With Mr Smith and Ollama already running locally, use the read-only checks:

```powershell
.\scripts\accept-mr-smith.ps1
.\scripts\accept-mr-smith.ps1 -OpenBrowser
```

The script never starts or stops services and does not load a model. Its
explicit `-RunInference` option sends one local chat request, which can load
the selected model and save a history entry. If the admin token is present in
the current PowerShell process, the script also reads local service/resource
status; it does not print or persist the token.

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
health, last generation latency and a snapshot of in-flight/queued model
operations. First requests can be slow on CPU. Missing models or Ollama failures
return an explicit HTTP 502, never a fabricated answer.

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

The morning page has quick links to Chat & Brief, Finance and System/Admin, plus
question-driven Morning Brief, Financial Analysis and Research & Deep Work mode
tabs. Views include loading/error states, timestamps, source links and system
dark mode. The
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
management. Its current-process table reports each Mr Smith/Ollama task, PID,
memory usage and cumulative CPU time, and refreshes while the admin token is
entered. From the page you can start a local
Ollama server, gracefully unload models while keeping the service running, and
gracefully stop only a server started by that page; stopping waits for active
model work and unloads loaded models first. It falls back to process termination
only if the graceful signal cannot be delivered or shutdown times out. An
Ollama process that was already running independently is visible but is not
stopped by Smith.
Service controls require the admin token and a loopback connection.
The UI does not terminate arbitrary PIDs; stop/restart Mr Smith from the
PowerShell window or process manager that launched it.

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
timestamped SQLite online backup (including committed WAL data) with a passing
`PRAGMA integrity_check`
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
and rejects non-finite/negative bar values instead of recording them. It also
caches timestamped OHLC bars for technical structure. `/watchlists/import`
accepts a user-selected CSV with a `Code` column (including the FOMO export
format), registers company names/sectors where present without overwriting
existing company records, and reports skipped rows. Each imported list can be
reviewed with `GET /watchlists/{id}/analysis`; `GET
/securities/{symbol}/analysis` returns the cached price, technical scenarios,
latest confirmed statement, evidence, thesis, catalysts, alerts and valuation
runs. The Finance page's **Deep dive with Smith** sends that packet to the
local research model and asks it to distinguish facts from interpretation.
Missing or stale inputs stay unavailable; the model may use only the supplied
calculated technical levels for conditional entry/target scenarios.

Daily pivots are calculated from the last completed daily OHLC bar: pivot,
R1/S1/R2/S2 and ATR-based conditional pullback/breakout/bear-case references.
They are technique-based scenarios, not fair value, forecasts or personalized
trade advice. `GET /xauusd/levels` exposes them for gold; the Finance page also
shows cached session range and the macro context. `GET /market/snapshot`
summarizes cached ASX 200, S&P 500, NASDAQ, VIX, USD-index proxy and real-yield
observations. Its risk-on/risk-off/mixed label follows an explicit index/VIX
rule and is not a sentiment score or prediction.

### Instrument research and batch runs

The Finance page supports manual one-company jobs, sequential batches, and a
separate Manual analysis tab. Each batch retrieves up to ten years of daily
price history where the public quote source has data, calculates price-only
context locally, writes a dated research report, and asks the selected local
Ollama model to digest that report. Batch status records every company's result;
source/model failures are recorded per ticker and do not stop the remaining
queue. Batches support up to 200 unique instruments. Completed instruments are
skipped by the resume operation. The default
batch model is `llama3.2:latest`, checked against the locally installed models
before the run starts. No external AI service is called.

The authenticated one-instrument API is available at
`POST /api/finance/research-jobs` with
`{"instrument":"ASX:NCK","asset_type":"auto"}`, then
`GET /api/finance/research-jobs/{job_id}` and
`GET /api/finance/research-jobs/{job_id}/report`. A bare ticker is provisionally
treated as an ASX equity; explicit forms such as `ASX:CSL`, `XAUUSD`, or
`EUR/USD` select a method without forcing every instrument through ASX rules.
Start a batch through `POST /api/finance/research-batches` with a list of
`instruments` and optional installed local `model`. Poll
`GET /api/finance/research-batches/{batch_id}`; retry unfinished/failed entries
with `POST /api/finance/research-batches/{batch_id}/resume`. Batch reports,
LLM analyses and status are saved beneath
`<data_dir>/research_batches/`; they do not modify SQLite or automatically
become trusted knowledge. Only one finance research job or batch runs at a time.
Live Yahoo/RBA retrieval is gated by `MR_SMITH_EXTERNAL_FEEDS=1`.

For the current ASX 200, first create a dated constituent snapshot and batch
list, then start the independent local Python/Ollama worker from PowerShell.
The preparation step does not fetch prices or call a model:

```powershell
$run = "asx200-10y-" + (Get-Date -Format "yyyyMMdd-HHmm")
.\.venv\Scripts\python.exe .\scripts\download_asx200_dataset.py `
  --prepare-only --output-dir ".\data\asx200_ingest\$run"
.\.venv\Scripts\python.exe scripts\research_instrument.py `
  --batch-file ".\data\asx200_ingest\$run\instruments.json" `
  --batch-dir ".\data\research_jobs\$run" `
  --data-dir .\data `
  --model llama3.2:latest --allow-network
```

The research worker runs sequentially in that PowerShell process, retrieves up
to ten years of Yahoo daily history per ticker, and sends each company report
to the local Ollama model. Copilot is not part of execution. The constituent
snapshot is sourced from Wikipedia and is current membership, not a
historical/survivorship-free ASX 200 roster. Yahoo's public chart endpoint is
unofficial and may be delayed; check source terms before redistribution.
Ten-year price history does not mean ten years of issuer financial statements:
those remain unavailable unless a company source manifest and reports exist.
Expect a long run; status and per-company outputs are persisted under the batch
directory, so use the same list path and exact same batch directory with
`--resume` after interruption. Do not rerun preparation into that same
non-empty output directory; keep the generated `instruments.json`.

For a standalone price-data CSV instead of model reports, run
`.\.venv\Scripts\python.exe .\scripts\download_asx200_dataset.py --output-dir .\data\asx200_ingest\asx200-price-snapshot`;
it saves `daily_prices_10y.csv` and a run manifest. The script refuses to
overwrite files in an existing non-empty output directory.

For other resumable command-line batches, prepare a JSON list or CSV with a
`Code` or `symbol` column and run:

```powershell
.\.venv\Scripts\python.exe scripts\research_instrument.py `
  --batch-file .\data\research_lists\watchlist_and_holdings.json `
  --batch-dir .\data\research_jobs\tonight `
  --model llama3.2:latest --allow-network
```

If interrupted, rerun with the same paths plus `--resume`; already completed
companies are not repeated. To regenerate local-model writeups from already
saved evidence without re-downloading, add `--resume --reanalyze-completed`.
The CLI reports a preflight error if Ollama or the requested model is
unavailable. It never imports holding quantities, changes the finance
database, or makes trade decisions.

#### Make completed ASX research retrievable by Smith

After a batch completes, import its deterministic per-company reports into
Smith's local finance evidence store. The import first validates that every
constituent has one completed report, offers a no-write dry run by default,
and creates an integrity-checked SQLite backup when `--apply` is used. It is
idempotent and preserves existing security records. It does not import Llama
analysis drafts or turn reports into reviewed/authoritative knowledge.

```powershell
.\.venv\Scripts\python.exe .\scripts\ingest_asx200_research.py `
  --batch-dir .\data\research_jobs\asx200-10y-20261027-2356 `
  --constituents .\data\asx200_ingest\asx200-10y-20261027-2356\constituents_snapshot.csv `
  --data-dir .\data

.\.venv\Scripts\python.exe .\scripts\ingest_asx200_research.py `
  --batch-dir .\data\research_jobs\asx200-10y-20261027-2356 `
  --constituents .\data\asx200_ingest\asx200-10y-20261027-2356\constituents_snapshot.csv `
  --data-dir .\data --apply
```

Ticker-linked finance deep dives retrieve up to three relevant evidence passages
from the imported report. For direct `/api/chat` or `/api/research` requests,
include `entity_symbol` such as `CSL.AX`; the Finance page's company deep dive
sets this automatically. Price reports do not replace issuer filings or
fundamental statements; that missing coverage remains explicit.

This supports growing a 300–500-company research set in bounded batches rather
than launching an uncontrolled scrape. The worker currently uses a curated
`<data_dir>/company_sources/<ASX_CODE>.json` manifest for issuer history,
annual-report URLs, and verified corporate-action notes. If no manifest exists,
it says the financial history and complete split/merger record are unavailable;
it does not guess. Consequently, unmanifested small caps receive a price and
market-structure review—not a fundamental company deep dive. The manifest-backed
report parser and Yahoo chart are an initial workflow, not an automated
discovery service for every small cap.
Thin trading, stale quotes, incomplete announcements, and coverage gaps must
remain visible; no ASX200 membership, market-cap tier, or liquidity class is
assumed. Model output is a research draft, not verified evidence or advice.

The dated broker "Share Holdings" CSV can be uploaded to the existing
portfolio `holdings/preview` flow. The parser normalizes symbols and units,
calculates total cost basis from units times the export's purchase price, and
uses the dated snapshot's market value only as a dated manual valuation. The
preview is non-mutating; importing requires the separate `holdings/confirm`
action. Account identifiers, subtotal rows and report preamble are not saved.
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

### XAUUSD macro context and trading workflow

The `/finance` page separates the ASX investor workflow from the XAUUSD
trading workflow. Gold prices and the allowlisted Yahoo proxies (U.S. Dollar
Index, EUR/USD, USD/JPY, and VIX) are timestamped, source-labeled and cached;
all indicators and the macro context summary are calculated locally. The
Yahoo `XAUUSD=X` series is an unofficial proxy, not an executable spot quote.
The VIX label is only an equity-volatility/risk-mood proxy; Smith does not
claim a universal fear/greed score or infer gold direction from it.

`POST /api/finance/xauusd/macro/refresh` manually requests the official U.S.
Treasury daily real-yield curve, Federal Reserve press-release RSS, GDELT
headlines, and the allowlisted Yahoo macro series. Network access still
requires `MR_SMITH_EXTERNAL_FEEDS=1`; each source reports its own failure,
and headlines are stored as unverified, attributed links rather than copied
article text. `GET /api/finance/xauusd/macro/context` reports dated cached
observations, VIX risk-mood bands, and conditional real-yield/USD context.
The UI's risk calculator withholds position size when contract inputs are
missing, and the journal stores manual TradingView notes without scraping.
These outputs describe evidence and scenarios; they are not trade
recommendations.

FXCM is a promising source candidate: its official ForexConnect SDK advertises
live and historical price access, including XAU/USD in its symbol reference.
However, FXCM's published Python package page lists support only through
Python 3.7 and pins old dependencies; its official SDK also requires an FXCM
account and signed EULA. It is catalogued but not connected or enabled until a
Python 3.12-compatible official SDK/runtime and account-specific API access are
verified. See [FXCM ForexConnect API](https://github.com/fxcm/ForexConnectAPI),
[FXCM ForexConnect on PyPI](https://pypi.org/project/forexconnect/), and
[FXCM's available-symbol list](https://github.com/fxcm/ForexConnectAPI/blob/master/AvailableSymbols.md).

Market Index's published terms prohibit automated/script access to its data.
There is deliberately no Market Index scraper or announcement connector.
Use manual evidence entry/PDF upload or an authorized, licensed ASX/company
data feed for announcements. The macro source registry reports which
connectors exist, but does not enable them or authorize background refresh.

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
