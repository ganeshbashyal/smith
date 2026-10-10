import pytest

from assistant_api.finance import FINANCE_INSTRUCTION
from assistant_api.personas import PERSONAS
from assistant_api.routing import route_request


@pytest.mark.parametrize("question", [
    "Map RBA interest rates into cash flow",
    "What explains the inflation regime?",
    "Compare equity valuation and earnings",
    "Review institutional ownership and short interest",
    "Build a DCF sensitivity analysis",
])
def test_market_intelligence_routes_to_finance_without_extra_tools(question):
    route = route_request(question, "daily", "research", research=True)
    assert route.persona == "finance"
    assert route.output_format == "financial_research"
    assert route.model == "research"
    assert route.tools == ()


def test_finance_prompt_keeps_evidence_and_capability_boundaries():
    assert PERSONAS["finance"].instruction == FINANCE_INSTRUCTION
    for instruction in (
        "Fact -> Evidence -> Interpretation -> Hypothesis -> Conclusion",
        "World -> Economy -> Industry -> Company",
        "Tier 1", "Tier 2", "Tier 3", "We don't know yet.",
        "Disabled or failed", "counter-thesis", "invalidation",
        "not automatically verified primary evidence",
        "Never invent probabilities", "Smart Money Score",
        "Do not claim monitoring, alerts or persistent thesis tracking",
        "Sentiment is not financial advice",
    ):
        assert instruction in FINANCE_INSTRUCTION


def test_finance_does_not_override_safety_or_general_research():
    assert route_request("Medical investment risks", "d", "r").persona == "health"
    assert route_request("Building compliance valuation", "d", "r").persona == "building"
    assert route_request("Engineering material", "d", "r", research=True).output_format == "research"
    assert route_request("Macro news", "d", "r").output_format == "financial_analysis"
    assert route_request("Newsletter", "d", "r").persona == "general"
