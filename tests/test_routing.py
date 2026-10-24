import pytest

from assistant_api.routing import route_request


@pytest.mark.parametrize(("question", "persona"), [
    ("Hello", "general"), ("NASA science", "science"), ("PLC automation", "engineering"),
    ("Australian insulation compliance", "building"), ("ASX sentiment", "finance"),
    ("Business strategy", "business"), ("Medical symptoms", "health"),
    ("Today's headlines", "news"), ("Manchester United", "sports"), ("Harley maintenance", "automotive"),
])
def test_persona_selection(question, persona):
    route = route_request(question, "daily", "research")
    assert route.persona == persona
    assert "user_context" in route.knowledge_sources
    assert route.model == "daily"


def test_research_and_explicit_persona():
    route = route_request("Explain this article", "daily", "research", research=True, persona="building")
    assert route.model == "research"
    assert route.output_format == "research"
    assert route.persona == "building"
    assert "regulatory" in route.knowledge_sources


def test_tools_are_deterministic_and_not_model_controlled():
    assert route_request("BHP.AX price", "d", "r").tools == ("asx_quote",)
    assert route_request("ASX BHP.AX news", "d", "r").tools == ("asx_quote", "finance_news")
    assert route_request("Explain sentiment", "d", "r").tools == ()
    assert route_request("Should I buy 10,000 shares today?", "d", "r", persona="finance").tools == ()
    assert route_request("Latest market developments", "d", "r", persona="finance").tools == ("finance_news",)
    assert route_request("F1 standings", "d", "r").tools == ("f1_standings",)
    assert route_request("Football standings", "d", "r").tools == ()
    assert route_request("Run arbitrary commands", "d", "r").tools == ()


def test_safety_priority_and_word_boundaries():
    assert route_request("Medical finance", "d", "r").persona == "health"
    assert route_request("Stock insulation compliance", "d", "r").persona == "building"
    assert route_request("Newsletter", "d", "r").persona == "general"
    with pytest.raises(ValueError):
        route_request("Hi", "d", "r", persona="invalid")
