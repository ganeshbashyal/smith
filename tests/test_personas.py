import pytest

from assistant_api.main import PersonaName
from assistant_api.personas import BASE_INSTRUCTION, PERSONAS
from assistant_api.routing import route_request
from typing import get_args


DOMAIN_REQUIREMENTS = {
    "general": ("kind", "dependencies", "not execution", "professional review",
                "Lead with the most useful direct answer", "one focused question at a time"),
    "science": ("reproducibility", "confounders", "preprint", "treatment advice",
                "Correlation alone does not establish causation"),
    "engineering": ("acceptance criteria", "lockout", "firmware", "not commissioned",
                    "hazard-control", "non-invasive observations"),
    "building": ("assembly", "jurisdiction", "Manufacturer evidence", "compliance is undetermined",
                 "whole-assembly", "ask only for the detail needed"),
    "finance": ("counter-thesis", "invalidation", "Smart Money Score", "not financial advice"),
    "business": ("unit economics", "decision gates", "customer evidence", "qualified review"),
    "health": ("Never diagnose", "000", "contraindications", "urgent care",
               "Do not ask follow-up questions", "no connected live clinical source"),
    "news": ("publication time", "corroboration", "headlines", "disabled/error"),
    "sports": ("Manchester United", "Real Madrid", "Ferrari", "season", "betting"),
    "automotive": ("model", "torque", "not driving", "cannot certify",
                   "lead with a clear stop-use recommendation"),
}


def test_registry_matches_validated_api_personas():
    assert set(PERSONAS) == set(get_args(PersonaName)) == set(DOMAIN_REQUIREMENTS)
    with pytest.raises(TypeError):
        PERSONAS["extra"] = PERSONAS["general"]


@pytest.mark.parametrize("name", DOMAIN_REQUIREMENTS)
def test_every_persona_has_developed_unique_formats_and_boundaries(name):
    profile = PERSONAS[name]
    normalized = " ".join(profile.instruction.lower().split())
    for phrase in DOMAIN_REQUIREMENTS[name]:
        assert phrase.lower() in normalized
    assert profile.name == name
    assert profile.label
    assert profile.knowledge_sources
    assert profile.brief_format
    assert profile.research_format != profile.brief_format
    assert "Sources" in profile.research_format
    assert len(profile.instruction.split()) >= 100
    assert len({p.instruction for p in PERSONAS.values()}) == 10
    assert len({p.research_format for p in PERSONAS.values()}) == 10


@pytest.mark.parametrize(("question", "name"), [
    ("Possible stroke signs", "health"),
    ("Condensation and thermal bridging", "building"),
    ("SCADA commissioning", "engineering"),
    ("Preprint reproducibility", "science"),
    ("Customer segmentation and unit economics", "business"),
    ("Vehicle brakes and roadworthiness", "automotive"),
    ("Premier League standings", "sports"),
    ("Current events", "news"),
    ("Help organize my day", "general"),
])
def test_new_domain_terms_route_deterministically(question, name):
    route = route_request(question, "daily", "research")
    assert route.persona == name
    assert set(route.tools) <= set(PERSONAS[name].tools)


def test_emergency_and_safety_boundaries_apply_to_explicit_overrides():
    assert route_request("chest pain", "d", "r").persona == "health"
    for persona in PERSONAS:
        assert route_request("chest pain", "d", "r", persona=persona).persona == persona
    assert "Possible medical emergencies take priority" in BASE_INSTRUCTION
    assert "Australia tell the user to call 000" in BASE_INSTRUCTION
    assert "Do not ask follow-up questions" in BASE_INSTRUCTION
    assert "Across all personas" in BASE_INSTRUCTION
