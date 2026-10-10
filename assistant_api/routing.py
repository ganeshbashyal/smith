from dataclasses import asdict, dataclass
import re

from assistant_api.personas import PERSONAS


# Ordered rules resolve ties predictably; safety-sensitive subjects take priority.
RULES = (
    ("health", r"\b(health|medical|symptoms?|diagnosis|medicine|illness|wellbeing|chest pain|breathing difficulty|stroke signs)\b"),
    ("building", r"\b(building|construction|insulation|ncc|compliance|building code|condensation|thermal bridging|building science)\b"),
    ("finance", r"\b(finance|financial|asx|stock|stocks|shares|investment|sentiment|portfolio|valuation|equities|earnings|dividend|macro|inflation|liquidity|rba|dcf|bond yields|interest rates|cash flow|short interest|institutional ownership)\b|\b[A-Za-z0-9]{2,6}\.AX\b"),
    ("engineering", r"\b(engineering|automation|plc|industrial|control systems?|commissioning|interlocks?|scada)\b"),
    ("science", r"\b(science|scientific|nasa|physics|technology|space|peer review|preprint|experiment|reproducibility)\b"),
    ("business", r"\b(business|strategy|strategic|project delivery|market entry|unit economics|customer segmentation)\b"),
    ("automotive", r"\b(car|cars|motorcycles?|harley(?:-davidson)?|automotive|vehicles?|brakes?|roadworthiness)\b"),
    ("sports", r"\b(soccer|football|manchester united|real madrid|f1|formula 1|ferrari|la liga|premier league)\b"),
    ("news", r"\b(news|headlines|current events)\b"),
)

def conceptual_question(question: str) -> bool:
    return bool(re.search(r"\b(explain|what is|what are|how does|how do|why does|principles|concept|rules|definition)\b", question, re.IGNORECASE)) and not bool(
        re.search(r"\b(latest|current|today|standings|certify|compliant|diagnose|prescribe|price now)\b|\b[A-Za-z0-9]{2,6}\.AX\b", question, re.IGNORECASE)
    )


@dataclass(frozen=True)
class Route:
    persona: str
    knowledge_sources: tuple[str, ...]
    tools: tuple[str, ...]
    model: str
    output_format: str
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def route_request(question: str, selected_model: str, research_model: str,
                  *, research: bool = False, persona: str | None = None) -> Route:
    if persona is not None and persona not in PERSONAS:
        raise ValueError("Unknown persona")
    reason = "Explicit persona" if persona else "General fallback"
    if persona is None:
        persona = "general"
        for name, pattern in RULES:
            if re.search(pattern, question, re.IGNORECASE):
                persona, reason = name, f"Matched {name} intent"
                break
    profile = PERSONAS[persona]
    tools = []
    # Tool execution requires a matching intent, not just a persona assignment.
    explicit_freshness = bool(re.search(r"\b(latest|current|today|updates|standings|price now)\b", question, re.IGNORECASE))
    conceptual = bool(re.search(r"\b(explain|what is|what are|how does|how do|why does|principles|concept|rules|definition)\b", question, re.IGNORECASE))
    current = explicit_freshness or (not conceptual and bool(re.search(r"\b(news|headlines)\b", question, re.IGNORECASE)))
    if persona == "finance":
        if re.search(r"\b[A-Za-z0-9]{2,6}\.AX\b", question, re.IGNORECASE):
            tools.append("asx_quote")
        if current and re.search(r"\b(news|headlines)\b", question, re.IGNORECASE):
            tools.append("finance_news")
    if persona == "news" and current:
        tools.append("news_feed")
    if current:
        candidates = {
            "general": ("news_feed",), "science": ("science_news", "technology_news"),
            "engineering": ("technology_news",), "building": ("news_feed",),
            "business": ("finance_news",), "automotive": ("technology_news",),
        }.get(persona, ())
        tools.extend(candidates)
        if persona == "science" and re.search(r"\b(nasa|space)\b", question, re.IGNORECASE):
            tools = ["nasa_news"] if re.search(r"\bnasa\b", question, re.IGNORECASE) else [*tools, "nasa_news"]
        if persona in ("finance", "business") and re.search(r"\b(rba|interest rates|monetary)\b", question, re.IGNORECASE):
            tools.append("rba_news")
        elif persona == "finance" and "finance_news" not in tools and not tools:
            tools.append("finance_news")
    if persona == "sports" and re.search(r"\b(f1|formula 1)\b.*\b(standings|ranking)\b", question, re.IGNORECASE):
        tools.append("f1_standings")
    return Route(
        persona, ("user_context", *profile.knowledge_sources)
        if "user_context" not in profile.knowledge_sources else profile.knowledge_sources,
        tuple(tools), research_model if research else selected_model,
        ("financial_research" if persona == "finance" else "research") if research
        else "financial_analysis" if persona == "finance" else "brief",
        reason,
    )
