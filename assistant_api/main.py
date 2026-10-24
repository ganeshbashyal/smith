from contextlib import asynccontextmanager
from datetime import datetime, timezone
from html import escape
import hmac
import json
import logging
from pathlib import Path
import sqlite3
from typing import Literal
from urllib.parse import quote, urlsplit

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse
import httpx
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from assistant_api.config import Settings
from assistant_api.finance.api import build_router as build_finance_router
from assistant_api.finance.evidence import extract_pdf_text
from assistant_api.finance.repository import FinanceStore
from assistant_api.knowledge import inspect_knowledge_control_plane
from assistant_api.models import ModelManager
from assistant_api.personas import BASE_INSTRUCTION, PERSONAS
from assistant_api.routing import conceptual_question, route_request
from assistant_api.storage import Store
from assistant_api.tools import execute_tools
from assistant_api.tools import SOURCES, PUBLISHERS

logger = logging.getLogger(__name__)
PersonaName = Literal["general", "science", "engineering", "building", "finance", "business", "health", "news", "sports", "automotive"]
Category = Literal["user_context", "general", "science", "engineering", "automation", "building", "manufacturer", "regulatory", "finance", "business", "project_delivery", "health", "news", "sports", "automotive"]


class Question(BaseModel):
    question: str = Field(min_length=1, max_length=10000)
    persona: PersonaName | None = None
    refresh_sources: bool = False
    entity_symbol: str | None = Field(default=None, max_length=12, description="Optional linked company for finance evidence/context")

    @field_validator("question")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question must not be blank")
        return value.strip()


class Research(Question):
    material: str = Field(default="", max_length=60000)


class ModelSelection(BaseModel):
    model: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")


class Document(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    category: Category
    source_type: Literal["user_context", "source_document", "manufacturer_evidence", "regulatory_source", "working_note"]
    source: str = Field(min_length=1, max_length=2000)
    content: str = Field(min_length=1, max_length=200000)

    @field_validator("title", "source", "content")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Document fields must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def consistent_attribution(self) -> "Document":
        categories = {
            "user_context": "user_context",
            "manufacturer_evidence": "manufacturer",
            "regulatory_source": "regulatory",
        }
        if self.source_type in categories and self.category != categories[self.source_type]:
            raise ValueError("Source type requires its dedicated category")
        if self.category in categories.values() and categories.get(self.source_type) != self.category:
            raise ValueError("Dedicated evidence category requires a matching source type")
        return self


def create_app(settings: Settings | None = None, *, ollama_transport=None, feed_transport=None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = Store(settings.data_dir)
        finance_store = FinanceStore(settings.data_dir / "mr_smith.sqlite3")
        async with httpx.AsyncClient(base_url=settings.ollama_url, timeout=180, transport=ollama_transport) as ollama, httpx.AsyncClient(timeout=15, transport=feed_transport) as feeds:
            app.state.store = store
            app.state.finance_store = finance_store
            app.state.models = ModelManager(ollama, store, settings.daily_model)
            app.state.feeds = feeds
            yield

    app = FastAPI(title="Mr Smith", lifespan=lifespan)

    def admin(authorization: str | None = Header(default=None)):
        if not settings.admin_token:
            raise HTTPException(503, "Set MR_SMITH_ADMIN_TOKEN to enable admin operations")
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(401, "Bearer admin token required")
        supplied = authorization.removeprefix("Bearer ")
        if not hmac.compare_digest(supplied.encode(), settings.admin_token.encode()):
            raise HTTPException(401, "Invalid admin token")

    async def ollama_operation(operation):
        try:
            return await operation
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Ollama operation failed: %s", type(exc).__name__)
            raise HTTPException(502, "Ollama unavailable, model not installed, or invalid response. Check Ollama and installed models.") from exc

    @app.get("/api/personas")
    async def personas():
        return {"personas": [{"name": p.name, "label": p.label, "instruction": p.instruction,
                              "brief_format": p.brief_format, "research_format": p.research_format,
                              "knowledge_sources": p.knowledge_sources, "allowed_tools": p.tools}
                             for p in PERSONAS.values()]}

    @app.post("/api/route")
    async def preview(request: Question):
        return route_request(request.question, app.state.models.selected_model,
                             settings.research_model, persona=request.persona).to_dict()

    async def answer(request: Question, research: bool):
        route = route_request(request.question, app.state.models.selected_model,
                              settings.research_model, research=research, persona=request.persona)
        evidence = app.state.store.search(request.question, route.knowledge_sources)
        if request.entity_symbol:
            evidence.extend(app.state.finance_store.evidence_as_context(request.entity_symbol.upper()))
        tools = await execute_tools(route.tools, request.question, app.state.feeds, settings.external_feeds,
                                    store=app.state.store, refresh=request.refresh_sources)
        material = request.material if isinstance(request, Research) else ""
        if material.strip():
            evidence.append({"id": "request:material", "source": "User-supplied material",
                             "source_type": "working_note", "content": material})
        profile = PERSONAS[route.persona]
        format_instruction = profile.research_format if research else profile.brief_format
        if conceptual_question(request.question):
            format_instruction = """
REQUEST FORMAT OVERRIDE: This is a conceptual explanation, not a case assessment.
Answer directly in plain language with a short example if useful.
Do not use the persona's case-analysis headings, company metadata, sentiment,
confidence scores, catalysts, counter-thesis or empty Sources section.
Do not demand documents or equipment details to explain a general principle.
If no supplied evidence is used, end with "Basis: general model knowledge."
Respect the user's requested brevity. Safety boundaries remain in force."""
        if tools:
            format_instruction = """
For current information, identify actual reported developments with source IDs,
publication dates and source links supplied in tool data. Summarize the evidence,
not the mechanics of the tools. State missing or stale coverage explicitly.
Use a concise list of relevant dated developments followed by limitations.
Do not use conceptual headings such as Mechanism for a news roundup.
Do not present a stale item as today's news or infer unreported details.
For mixed questions, also explain the stable concept from general knowledge."""
        system = BASE_INSTRUCTION + "\n" + profile.instruction + "\n" + format_instruction
        prompt = json.dumps({"question": request.question, "evidence": evidence, "tool_results": tools})
        if len(prompt) > 12000:
            raise HTTPException(422, "Combined question, material and evidence exceed the local context budget. Shorten the material or narrow the question.")
        text, actual_model = await ollama_operation(app.state.models.generate(
            system, prompt, settings.research_model if research else None,
            output_tokens=3072 if research else 1024,
        ))
        # The model may have been changed by an admin while retrieval was running.
        route_data = {**route.to_dict(), "model": actual_model}
        captured_sources = list(evidence)
        for tool in tools:
            data = tool.get("data")
            if isinstance(data, list):
                captured_sources.extend(data)
            elif isinstance(data, dict):
                captured_sources.append({**tool, **data})
        app.state.store.save_answer(
            request.question, text, route.persona, actual_model,
            sources=captured_sources, research=research,
        )
        return {"answer": text, "route": route_data, "sources": evidence, "tools": tools,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "knowledge_basis": {"general_model_knowledge_allowed": True,
                                    "retrieved_evidence_available": bool(
                                        any(e.get("source_type") not in ("working_note", "user_context") for e in evidence)
                                        or any(t["status"] == "ok" for t in tools)),
                                    "user_material_available": bool(material.strip())},
                "warnings": ["General model knowledge is not independently verified. Generated answers and citations require verification."] +
                (["Sentiment is not financial advice."] if route.persona == "finance" else [])}

    @app.post("/api/chat")
    async def chat(request: Question):
        return await answer(request, False)

    @app.post("/api/research")
    async def research(request: Research):
        return await answer(request, True)

    @app.get("/api/models")
    async def models():
        return await ollama_operation(app.state.models.status())

    @app.get("/api/sources")
    async def sources():
        status = app.state.store.tool_status()
        return {"enabled": settings.external_feeds, "coverage": "Approved feeds only; no unrestricted browsing or universal live knowledge",
                "sources": [{"tool": name, "source": url, "publisher": PUBLISHERS[name],
                             "last_result": status.get(name)} for name, url in SOURCES.items()]}

    @app.get("/api/coverage/sports")
    async def sports_coverage():
        status = app.state.store.tool_status()
        return {
            "football": {
                "status": "unavailable",
                "coverage": ["Stable rules, tactics and dated supplied evidence"],
                "not_supported": ["Current fixtures", "live scores", "results", "standings", "injuries"],
                "freshness": "unknown",
                "source": None,
                "reason": "No approved football fixture or results provider is configured; general news feeds are not authoritative score sources.",
            },
            "formula_1": {
                "status": "limited",
                "coverage": ["Published current-season driver standings"],
                "not_supported": ["Live timing", "live race telemetry"],
                "source": SOURCES["f1_standings"],
                "publisher": PUBLISHERS["f1_standings"],
                "last_result": status.get("f1_standings"),
            },
        }

    @app.get("/api/knowledge/status")
    async def knowledge_status():
        root = Path(__file__).parents[1] / "knowledge"
        try:
            control_plane = inspect_knowledge_control_plane(root, PERSONAS)
        except ValueError as exc:
            raise HTTPException(503, f"Knowledge control-plane metadata is invalid: {exc}") from exc
        with app.state.store.connect() as db:
            document_count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            by_category = {
                row["category"]: row["count"]
                for row in db.execute("SELECT category, COUNT(*) AS count FROM documents GROUP BY category")
            }
            by_source_type = {
                row["source_type"]: row["count"]
                for row in db.execute("SELECT source_type, COUNT(*) AS count FROM documents GROUP BY source_type")
            }
        return {
            **control_plane,
            "local_document_count": document_count,
            "local_document_counts": {
                "by_category": by_category,
                "by_source_type": by_source_type,
            },
            "external_feeds_enabled": settings.external_feeds,
        }

    @app.get("/api/admin/history", dependencies=[Depends(admin)])
    async def list_history(
        limit: int = Query(default=25, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        return {"history": app.state.store.list_history(limit=limit, offset=offset)}

    @app.get("/api/admin/history/{history_id}", dependencies=[Depends(admin)])
    async def get_history(history_id: int):
        entry = app.state.store.get_history(history_id)
        if not entry:
            raise HTTPException(404, "Unknown history entry")
        return entry

    @app.delete("/api/admin/history/{history_id}", dependencies=[Depends(admin)])
    async def delete_history(history_id: int):
        if not app.state.store.delete_history(history_id):
            raise HTTPException(404, "Unknown history entry")
        return {"deleted": True, "id": history_id}

    @app.get("/api/admin/history/{history_id}/export", dependencies=[Depends(admin)])
    async def export_research_history(history_id: int):
        entry = app.state.store.get_research_export(history_id)
        if not entry:
            raise HTTPException(404, "Unknown research entry")

        def markdown_escape(value: str) -> str:
            for character in ("\\", "`", "*", "_", "{", "}", "[", "]", "(", ")", "#", "+", "-", ".", "!", "|", ">"):
                value = value.replace(character, "\\" + character)
            return value

        lines = [
            f"# Research answer {entry['id']}",
            "",
            f"- **Created:** {markdown_escape(entry['created_at'])}",
            f"- **Persona:** {markdown_escape(entry['persona'])}",
            f"- **Model:** {markdown_escape(entry['model'])}",
            "",
            "## Question",
            "",
            entry["question"],
            "",
            "## Answer",
            "",
            entry["answer"],
            "",
            "## Captured evidence",
            "",
        ]
        if entry["sources"]:
            for source in entry["sources"]:
                label = str(source.get("title") or source.get("source") or source.get("id") or "Evidence")
                href = str(source.get("url") or source.get("source") or "")
                parsed = urlsplit(href)
                if parsed.scheme in ("http", "https") and parsed.netloc:
                    safe_href = quote(href, safe=":/?#[]@!$&'*,;=%")
                    rendered = f"[{markdown_escape(label)}]({safe_href})"
                else:
                    rendered = markdown_escape(label)
                details = [
                    str(source[key]) for key in ("source_type", "publisher")
                    if source.get(key)
                ]
                publication_date = source.get("published_at") or source.get("published")
                if publication_date:
                    details.append(str(publication_date))
                if source.get("freshness"):
                    details.append(str(source["freshness"]))
                lines.append("- " + rendered + (f" — {markdown_escape('; '.join(details))}" if details else ""))
        else:
            lines.append("No source attribution was captured for this answer.")
        lines.extend([
            "",
            "> This answer is model-generated and has not been independently verified. "
            "Check the linked evidence and confirm that it supports each claim; citations do not guarantee claim-level support.",
            "",
        ])
        return PlainTextResponse(
            "\n".join(lines),
            media_type="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="mr-smith-research-{history_id}.md"'},
        )

    @app.post("/api/admin/model", dependencies=[Depends(admin)])
    async def select_model(request: ModelSelection):
        await ollama_operation(app.state.models.select(request.model))
        return {"selected_model": app.state.models.selected_model, "active_model": app.state.models.active_model}

    @app.post("/api/admin/documents", dependencies=[Depends(admin)])
    async def add_document(request: Document):
        identity, created = app.state.store.add_document(**request.model_dump())
        return {"id": f"document:{identity}", "created": created}

    @app.get("/api/admin/documents", dependencies=[Depends(admin)])
    async def list_documents(
        q: str = Query(default="", max_length=200),
        category: Category | None = None,
        source_type: Literal["user_context", "source_document", "manufacturer_evidence", "regulatory_source", "working_note"] | None = None,
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ):
        return {"documents": app.state.store.list_documents(
            query=q, category=category, source_type=source_type, limit=limit, offset=offset,
        )}

    @app.get("/api/admin/documents/{document_id}", dependencies=[Depends(admin)])
    async def get_document(document_id: int):
        document = app.state.store.get_document(document_id)
        if not document:
            raise HTTPException(404, "Unknown document")
        return document

    @app.put("/api/admin/documents/{document_id}", dependencies=[Depends(admin)])
    async def update_document(document_id: int, request: Document):
        try:
            revision = app.state.store.update_document(document_id, **request.model_dump())
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "An identical document with the same attribution already exists") from exc
        if revision is None:
            raise HTTPException(404, "Unknown document")
        return {"id": f"document:{document_id}", "revision": revision}

    @app.delete("/api/admin/documents/{document_id}", dependencies=[Depends(admin)])
    async def delete_document(document_id: int):
        if not app.state.store.delete_document(document_id):
            raise HTTPException(404, "Unknown document")
        return {"deleted": True, "id": f"document:{document_id}"}

    @app.get("/api/admin/documents/{document_id}/revisions", dependencies=[Depends(admin)])
    async def document_revisions(document_id: int):
        if not app.state.store.get_document(document_id) and not app.state.store.list_document_revisions(document_id):
            raise HTTPException(404, "Unknown document")
        return {"revisions": app.state.store.list_document_revisions(document_id)}

    @app.post("/api/admin/documents/pdf", dependencies=[Depends(admin)])
    async def add_document_pdf(
        file: UploadFile = File(...),
        title: str = Form(..., min_length=1, max_length=300),
        category: str = Form(...),
        source_type: str = Form(...),
        source: str = Form(..., min_length=1, max_length=1900),
    ):
        raw = await file.read(25_000_001)
        if len(raw) > 25_000_000:
            raise HTTPException(422, "PDF exceeds the 25MB import budget")
        pages, limitations = extract_pdf_text(raw)
        if not pages or not any(page.strip() for page in pages):
            raise HTTPException(422, limitations or "PDF has no extractable text; scanned-image OCR is not supported")
        if len(pages) > 500:
            raise HTTPException(422, "PDF exceeds the 500-page import budget")
        if sum(len(page) for page in pages) > 1_000_000:
            raise HTTPException(422, "Extracted PDF text exceeds the 1MB import budget")

        page_documents = []
        for page_number, page_text in enumerate(pages, start=1):
            if not page_text.strip():
                continue
            page_source = f"{source}#page={page_number}"
            page_title = f"{title} (page {page_number})"
            if len(page_title) > 300 or len(page_source) > 2000:
                raise HTTPException(422, "PDF title or source is too long after adding page provenance")
            try:
                document = Document.model_validate({
                    "title": page_title,
                    "category": category,
                    "source_type": source_type,
                    "source": page_source,
                    "content": page_text,
                })
            except ValidationError as exc:
                raise HTTPException(422, "Invalid document metadata") from exc
            page_documents.append((page_number, document.model_dump()))
        try:
            stored = app.state.store.add_documents([document for _, document in page_documents])
        except sqlite3.IntegrityError as exc:
            raise HTTPException(409, "PDF pages conflict with existing document attribution") from exc
        imported = [
            {"id": f"document:{identity}", "page": page_documents[index][0], "created": created}
            for index, (identity, created) in enumerate(stored)
        ]
        return {
            "documents": imported,
            "pages": len(pages),
            "imported_pages": len(imported),
            "limitations": limitations,
        }

    @app.get("/morning", response_class=HTMLResponse)
    async def morning():
        page = (Path(__file__).parent / "pages" / "morning.html").read_text(encoding="utf-8")
        options = "\n".join(
            f'<option value="{escape(p.name, quote=True)}">{escape(p.label)}</option>'
            for p in PERSONAS.values()
        )
        return page.replace("<!-- PERSONA_OPTIONS -->", options)

    @app.get("/admin", response_class=HTMLResponse)
    async def admin_page():
        return (Path(__file__).parent / "pages" / "admin.html").read_text(encoding="utf-8")

    @app.get("/finance", response_class=HTMLResponse)
    async def finance_page():
        return (Path(__file__).parent / "pages" / "finance.html").read_text(encoding="utf-8")

    app.include_router(build_finance_router(
        get_store=lambda: app.state.finance_store,
        admin_dependency=admin,
        feeds_client_getter=lambda: app.state.feeds,
        external_feeds_enabled=lambda: settings.external_feeds,
    ))

    return app


app = create_app()
