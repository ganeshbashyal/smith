"""Validate the human-reviewable persona knowledge collection plan."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "knowledge" / "collection_plan.json"
REGISTRY = ROOT / "knowledge" / "source_registry.json"
CANDIDATES = ROOT / "knowledge" / "source_candidates.json"
PERSONAS = {"general", "science", "engineering", "building", "finance", "business", "health", "news", "sports", "automotive"}


def validate() -> dict:
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    candidates = json.loads(CANDIDATES.read_text(encoding="utf-8"))
    candidate_ids = {item["id"] for item in candidates["candidates"]}
    registry_ids = {item["id"] for item in registry["sources"]}
    if set(plan) != {"version", "purpose", "policy", "personas"} or plan["version"] != 1:
        raise ValueError("collection plan shape or version is invalid")
    if set(plan["personas"]) != PERSONAS:
        raise ValueError("collection plan must cover every runtime persona exactly once")
    unknown = set()
    for persona, entry in plan["personas"].items():
        if not 1 <= entry["priority"] <= 3 or not entry["sources"] or not entry["first_pack"].strip():
            raise ValueError(f"invalid collection entry for {persona}")
        unknown.update(set(entry["sources"]) - candidate_ids - registry_ids)
    if unknown:
        raise ValueError("unknown source IDs: " + ", ".join(sorted(unknown)))
    return {"personas": len(plan["personas"]), "planned_sources": sum(len(item["sources"]) for item in plan["personas"].values()), "status": "valid"}


if __name__ == "__main__":
    try:
        print(json.dumps(validate(), indent=2))
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        print(f"Knowledge plan validation failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
