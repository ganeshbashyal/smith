from contextlib import asynccontextmanager
from datetime import datetime, timezone
from html import escape
import hmac
import json
import logging
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse
import httpx
from pydantic import BaseModel, Field, field_validator, model_validator

from assistant_api.config import Settings
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
        async with httpx.AsyncClient(base_url=settings.ollama_url, timeout=180, transport=ollama_transport) as ollama, httpx.AsyncClient(timeout=15, transport=feed_transport) as feeds:
            app.state.store = store
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
        app.state.store.save_answer(request.question, text, route.persona, actual_model)
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

    @app.post("/api/admin/model", dependencies=[Depends(admin)])
    async def select_model(request: ModelSelection):
        await ollama_operation(app.state.models.select(request.model))
        return {"selected_model": app.state.models.selected_model, "active_model": app.state.models.active_model}

    @app.post("/api/admin/documents", dependencies=[Depends(admin)])
    async def add_document(request: Document):
        identity, created = app.state.store.add_document(**request.model_dump())
        return {"id": f"document:{identity}", "created": created}

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

    return app


app = create_app()
