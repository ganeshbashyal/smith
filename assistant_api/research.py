import json
from .providers import LLMProvider


async def deep_research(provider: LLMProvider, question: str, material: str) -> str:
    prompt = [{"role": "system", "content": "You are Mr Smith's deep-research analyst. Be rigorous, practical, direct, and evidence-conscious. Return a concise structured analysis with these headings: Executive summary, Key evidence, Implications, Risks and uncertainties, Recommended next steps, Follow-up questions. Do not invent facts. Separate supplied evidence from inference. Do not provide medical, legal, or financial advice."}, {"role": "user", "content": json.dumps({"research_question": question, "source_material": material})}]
    return await provider.complete(prompt)
