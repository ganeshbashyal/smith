import json
from pathlib import Path
from typing import Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from assistant_api.personas import Persona


class KnowledgeSource(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    publisher: str = Field(min_length=1)
    personas: list[str] = Field(min_length=1)
    type: Literal["feed", "official_feed", "market_feed", "api", "local_document"]
    refresh: Literal["daily", "on_demand", "event_driven", "explicit_ingest"]
    tier: int = Field(ge=1, le=5)
    enabled_by_default: bool

    @field_validator("publisher")
    @classmethod
    def publisher_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Publisher must not be blank")
        return value.strip()


class SourceRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    version: int = Field(ge=1)
    purpose: str = Field(min_length=1)
    sources: list[KnowledgeSource] = Field(min_length=1)

    @field_validator("purpose")
    @classmethod
    def purpose_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Purpose must not be blank")
        return value.strip()

    @classmethod
    def parse_supported(cls, data: object) -> "SourceRegistry":
        try:
            registry = cls.model_validate(data)
        except ValidationError as exc:
            raise ValueError(f"Invalid source registry: {exc}") from exc
        if registry.version != 1:
            raise ValueError(f"Unsupported source registry version: {registry.version}")
        source_ids = [source.id for source in registry.sources]
        duplicates = sorted({source_id for source_id in source_ids if source_ids.count(source_id) > 1})
        if duplicates:
            raise ValueError(f"Duplicate source IDs: {', '.join(duplicates)}")
        for source in registry.sources:
            if len(source.personas) != len(set(source.personas)):
                raise ValueError(f"Duplicate persona grants for source {source.id}")
        return registry


# This crosswalk is diagnostic only; routing.py and Persona remain authoritative.
SOURCE_TO_RUNTIME_TOOLS: dict[str, tuple[str, ...]] = {
    "bbc_news": ("news_feed", "technology_news", "science_news"),
    "nasa_news": ("nasa_news",),
    "rba_releases": ("rba_news",),
    "yahoo_finance": ("finance_news", "asx_quote"),
    "jolpica_f1": ("f1_standings",),
}
LOCAL_DOCUMENT_SOURCE = "user_documents"


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read valid JSON from {path.name}: {exc}") from exc


def _validate_persona_source_map(data: object, source_ids: set[str],
                                 runtime_personas: Mapping[str, Persona]) -> dict[str, list[str]]:
    if not isinstance(data, dict):
        raise ValueError("Persona source map must be a JSON object")
    unknown_personas = sorted(set(data) - set(runtime_personas))
    if unknown_personas:
        raise ValueError(f"Unknown persona names in source map: {', '.join(unknown_personas)}")

    result: dict[str, list[str]] = {}
    for persona_name, source_list in data.items():
        if not isinstance(source_list, list) or any(not isinstance(source_id, str) for source_id in source_list):
            raise ValueError(f"Source map entry for {persona_name} must be a list of source IDs")
        if len(source_list) != len(set(source_list)):
            raise ValueError(f"Duplicate source IDs in map entry for {persona_name}")
        unknown_sources = sorted(set(source_list) - source_ids)
        if unknown_sources:
            raise ValueError(f"Unknown source IDs for {persona_name}: {', '.join(unknown_sources)}")
        result[persona_name] = source_list
    return result


def _runtime_supports(persona: Persona, source_id: str) -> bool:
    if source_id == LOCAL_DOCUMENT_SOURCE:
        return bool(persona.knowledge_sources)
    tools = SOURCE_TO_RUNTIME_TOOLS.get(source_id)
    return bool(tools and set(tools).intersection(persona.tools))


def inspect_knowledge_control_plane(root: Path,
                                    runtime_personas: Mapping[str, Persona]) -> dict[str, object]:
    """Validate descriptive metadata and report drift without changing runtime policy."""
    registry_data = _read_json(root / "source_registry.json")
    registry = SourceRegistry.parse_supported(registry_data)
    source_ids = {source.id for source in registry.sources}

    source_map_data = _read_json(root / "persona_source_map.json")
    source_map = _validate_persona_source_map(source_map_data, source_ids, runtime_personas)

    for source in registry.sources:
        unknown_personas = sorted(set(source.personas) - set(runtime_personas))
        if unknown_personas:
            raise ValueError(
                f"Unknown persona names for source {source.id}: {', '.join(unknown_personas)}"
            )

    registry_grants = {
        (persona_name, source.id)
        for source in registry.sources
        for persona_name in source.personas
    }
    map_grants = {
        (persona_name, source_id)
        for persona_name, source_list in source_map.items()
        for source_id in source_list
    }
    drift: list[dict[str, object]] = []
    for persona_name, source_id in sorted(registry_grants | map_grants):
        in_registry = (persona_name, source_id) in registry_grants
        in_map = (persona_name, source_id) in map_grants
        if in_registry != in_map:
            drift.append({
                "kind": "registry_map_mismatch",
                "persona": persona_name,
                "source_id": source_id,
                "registry_granted": in_registry,
                "persona_mapped": in_map,
            })

    known_source_ids = set(SOURCE_TO_RUNTIME_TOOLS) | {LOCAL_DOCUMENT_SOURCE}
    for source in registry.sources:
        if source.id not in known_source_ids:
            drift.append({"kind": "source_without_runtime_binding", "source_id": source.id})

    runtime_summary: dict[str, dict[str, list[str]]] = {}
    for persona_name, persona in runtime_personas.items():
        mapped_sources = set(source_map.get(persona_name, []))
        supported_sources = {
            source_id for source_id in known_source_ids
            if _runtime_supports(persona, source_id)
        }
        for source_id in sorted(mapped_sources | supported_sources):
            mapped = source_id in mapped_sources
            supported = source_id in supported_sources
            if mapped != supported:
                drift.append({
                    "kind": "runtime_map_mismatch",
                    "persona": persona_name,
                    "source_id": source_id,
                    "runtime_supported": supported,
                    "persona_mapped": mapped,
                })
        if persona_name not in source_map:
            drift.append({"kind": "persona_missing_from_map", "persona": persona_name})
        runtime_summary[persona_name] = {
            "knowledge_categories": list(persona.knowledge_sources),
            "allowed_tools": list(persona.tools),
            "mapped_sources": sorted(mapped_sources),
            "runtime_sources": sorted(supported_sources),
        }

    return {
        "registry_version": registry.version,
        "sources": [source.model_dump() for source in registry.sources],
        "persona_source_map": source_map,
        "runtime_personas": runtime_summary,
        "validation": {
            "status": "drift" if drift else "ok",
            "drift": drift,
        },
    }
