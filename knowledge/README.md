# Mr Smith knowledge control plane

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
