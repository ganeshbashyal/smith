# Persona knowledge modules — v1 foundation

This pack provides a practical foundation for all ten personas. It is not a
complete domain encyclopedia, a professional standard, a current-data feed, or
a substitute for qualified review. The modules are authored as reusable
working notes. Until reviewed, every imported module remains
`pending_human_review` and must not be presented as authoritative evidence.

## What is included

| Persona | Foundation | Important unresolved evidence |
| --- | --- | --- |
| General | Decisions, assumptions, priorities, and action planning | User-specific preferences must come from explicit local context |
| Science | Study quality, uncertainty, causality, and reproducibility | Current consensus and individual papers need dated primary/review sources |
| Engineering | Requirements, systems, hazards, failure analysis, verification | Equipment-, revision-, standards-, and site-specific details |
| Building | Heat/moisture/acoustics and product/assembly distinctions | NCC edition, jurisdiction, approvals, exact assemblies and product evidence |
| Finance | Statements, returns, valuation, risk, and scenario discipline | Dated filings, prices, currency, portfolio completeness and personal suitability |
| Business | Customer evidence, unit economics, strategy, delivery | Jurisdiction-specific obligations and validated customer/market data |
| Health | Evidence literacy, benefits/harms, and safe boundaries | Current clinical guidance and personal assessment by a qualified clinician |
| News | Attribution, corroboration, chronology, and uncertainty | Current events require accessible dated reporting and primary records |
| Sports | Measurement-aware football/F1 analysis | Current fixtures/results/data; no football live-data connector is assumed |
| Automotive | Diagnostic reasoning, ownership, and safety | Exact vehicle/year/market, recalls, and manufacturer service information |

## Review and use

1. Review each module for accuracy, local applicability, source licensing, and
   the user's intended depth. Correct or split content before marking it
   reviewed in the Admin knowledge workflow.
2. Keep changing facts in dated evidence records, not in these stable foundation
   notes. A source URL listed in a module is a place to verify information, not
   proof that Mr Smith fetched or checked it.
3. Use the same module title and source when re-importing. Re-import updates
   that module as a new document revision; it does not mark the revision
   reviewed.
4. Do not mark model-generated or unverified material as reviewed merely
   because it is well-written.

Run a no-write validation first, then import into the active local database:

```powershell
.\.venv\Scripts\python.exe scripts\ingest_knowledge_modules.py --check-only
.\.venv\Scripts\python.exe scripts\ingest_knowledge_modules.py
```

The importer validates all modules before changing the database, uses stable
source identifiers, is safe to repeat, and preserves prior content as document
revisions when a module changes.
## Criteria for calling a domain module ready

- A named human reviewer has checked factual claims against identified,
  appropriate sources.
- Edition, jurisdiction, date, product/model scope, and evidence limitations
  are explicit wherever they affect the answer.
- Persona routing and retrieval tests show the right module is surfaced and
  excluded from unrelated personas.
- Representative answers have human annotations for unsupported claims,
  citation/source accuracy, usefulness, and refusal or escalation quality.
- Known gaps and the next evidence collection task remain recorded here.

The v1 pack makes every persona usable for foundational reasoning and helps
Mr Smith request the right evidence. It does **not** make every persona
authoritative for every real-world question. The next expansion should add
reviewed evidence slices for Australian building/insulation and finance first,
then persona-specific corpora for engineering, health, business, news, sports,
and automotive.
