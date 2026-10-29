# Mr Smith knowledge control plane

## Architecture review and next modules

## Persona knowledge gathering

`collection_plan.json` is the operating plan for expanding all ten personas.
It identifies the first evidence pack, priority, source candidates and refresh
cadence for each persona. It is deliberately separate from runtime connector
authorization: a source must still pass terms, provenance, freshness and
bounded-failure review before it can be connected.

Validate the plan before collecting material:

```powershell
.\\.venv\\Scripts\\python.exe scripts\\validate_knowledge_plan.py
```

Collect one persona at a time. For each source, preserve the original document
or link and record publisher, dates, jurisdiction, evidence tier, limitations
and review status. Start with finance and building, then science/engineering,
health/business, news/sports and automotive. Keep current facts in dated
evidence records; keep stable reasoning principles in the persona modules.

The current request path is:

1. `routing.py` selects a code-defined persona, knowledge categories and
   allowlisted tools.
2. `storage.py` retrieves local SQLite/FTS5 documents and records answers.
3. `tools.py` runs the persona's bounded connectors only when external feeds
   are explicitly enabled.
4. `models.py` serializes Ollama selection, loading, generation and unloading
   through the single-process model manager.
5. `main.py` composes FastAPI, application lifecycle and the chat, research,
   admin and knowledge-status routes; finance is already separated under
   `finance/`.

The JSON source registry and persona map are diagnostics, not runtime
authorization. `source_candidates.json` is likewise a research backlog, not
permission to fetch. New sources must not become live merely because they are
listed.

The retrieval boundary now uses FTS5 to find matching documents and excludes
`rejected` and `retired` material before it reaches a model prompt. Candidate
and pending-review documents remain retrievable only as explicitly unverified
context; reviewed status is not a substitute for checking source freshness or
jurisdiction. The next regression gate should preserve this distinction.

### Gaps to address in order

1. **Extract API routers from `main.py`.** Move chat/research, knowledge and
   system/admin routes into small routers with injected store/provider
   dependencies. Keep `main.py` as the composition root and preserve endpoint
   contracts.
2. **Add a source-adapter contract.** Each adapter should declare its stable
   source ID, publisher, allowed personas, licensing/attribution notes,
   jurisdiction, timeout/size limits, freshness rule and provenance fields.
   Normalize evidence before retrieval; keep connector policy code-owned.
3. **Add source-level controls and health.** Enable or disable connectors
   independently, report last success/failure and cache freshness, and make
   network failure explicit without replacing evidence with model guesses.
4. **Expand semantic evaluation.** The current synthetic set is only a smoke
   gate. Build a human-reviewed, versioned set for each priority domain;
   record routing, source/citation correctness, freshness, unsupported claims,
   refusal quality and regression under the same model.
5. **Add multi-process coordination only if deployment requires it.** Model
   serialization and service ownership are process-local. Until a shared
   lease/queue is designed and tested, run one Uvicorn worker as documented.

### Proposed module boundaries

- `assistant_api/api/chat.py`: request validation, route-to-persona flow and
  response contract; no direct network or SQL implementation.
- `assistant_api/api/knowledge.py`: private document/PDF APIs and public
  non-sensitive coverage/status responses.
- `assistant_api/api/admin.py`: authenticated model and service controls.
- `assistant_api/sources/contracts.py`: typed source result, provenance,
  freshness and connector error contract.
- `assistant_api/sources/` adapters: one module per approved provider; each
  returns normalized evidence and never writes conversational answers.
- `assistant_api/retrieval.py`: candidate retrieval/ranking and evidence
  status filtering, independent of HTTP routes and provider calls.

Implement the router extraction first as a behavior-preserving refactor; then
introduce one official source adapter at a time. Prioritize Australian
curriculum evidence and building/product documentation, followed by dated
ABS/RBA data. The corresponding source candidates are catalogued but disabled
until the specific official endpoints, terms, attribution, and bounded failure
behavior have been reviewed.

`source_registry.json` and `persona_source_map.json` are validated descriptive
metadata for the knowledge control plane. `GET /api/knowledge/status` checks
their structure and reports mismatches with the code-owned persona categories
and tool allowlists, along with local document counts. The JSON files do **not**
configure or authorize runtime retrieval, persona routing, or network access;
those remain code-owned. Actual external fetches remain deterministic and
disabled unless `MR_SMITH_EXTERNAL_FEEDS=1`.

`GET /api/coverage/sports` describes the actual current sports coverage. The
Jolpica source is limited to published Formula 1 driver standings. No approved
football fixture, result or live-score provider is connected, so those facts
remain unavailable rather than being inferred from general headlines.

Private knowledge documents can be searched, viewed, edited and deleted from
the authenticated `/admin` page. Edits create immutable prior revisions;
deletion removes both the current document and its revisions. Text-layer PDF
imports are bounded and stored by page with page-number citations. No OCR,
arbitrary URL fetching or automatic local-file discovery is supported. The
document search index is local SQLite FTS5, and schema changes are preceded by
a verified database backup.

Conversation history is private local data. The authenticated admin page
provides paginated listing, detail and permanent deletion; research answers
can be exported by user action as Markdown with captured evidence attributions
and a verification caveat. History diagnostics and public knowledge status do
not expose conversation content.

Ingested evidence should preserve its original source, publisher, publication
date, retrieval date, content hash, source tier and limitations. Model-generated
summaries are derived material, never the primary evidence. Knowledge status
reports counts and configuration health only; it never exposes document text.
