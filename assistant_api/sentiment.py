import hashlib
import json
import re
from .news import news_digest
from .providers import LLMProvider
from .storage import SQLiteStore

POSITIVE = {"growth", "gain", "gains", "profit", "profits", "strong", "record", "surge", "success", "bullish", "recovery", "innovation"}
NEGATIVE = {"loss", "losses", "fall", "falls", "weak", "risk", "crisis", "decline", "drop", "cuts", "lawsuit", "bearish", "warning"}

def heuristic(title: str, summary: str) -> tuple[str, float, str, str]:
    words = set(re.findall(r"[a-z]+", f"{title} {summary}".lower()))
    score = len(words & POSITIVE) - len(words & NEGATIVE)
    sentiment = "positive" if score > 0 else "negative" if score < 0 else "neutral"
    confidence = min(0.9, 0.5 + abs(score) * 0.1)
    catalyst = "Positive development or growth signal" if score > 0 else "No clear positive catalyst identified"
    risk = "Negative development or uncertainty signal" if score < 0 else "Monitor follow-up reporting and market reaction"
    return sentiment, confidence, catalyst, risk

async def analyse_news(store: SQLiteStore, provider: LLMProvider) -> list[dict]:
    output = []
    for feed in await news_digest():
        for item in feed.get("items", [])[:4]:
            article = {**item, "source": feed["source"], "article_key": hashlib.sha256(item["link"].encode()).hexdigest()}
            sentiment, confidence, catalyst, risk = heuristic(item["title"], item.get("summary", ""))
            prompt = [{"role": "system", "content": "Analyse news conservatively. Return JSON only with sentiment (positive, neutral, or negative), confidence (0 to 1), catalyst, and risk. Do not give financial advice."}, {"role": "user", "content": json.dumps({"title": item["title"], "summary": item.get("summary", "")})}]
            try:
                raw = await provider.complete(prompt)
                parsed = json.loads(raw[raw.find("{"):raw.rfind("}") + 1])
                if parsed.get("sentiment") in {"positive", "neutral", "negative"}:
                    sentiment, confidence, catalyst, risk = parsed["sentiment"], float(parsed.get("confidence", confidence)), str(parsed.get("catalyst", catalyst)), str(parsed.get("risk", risk))
            except Exception:
                pass
            article.update(sentiment=sentiment, confidence=round(max(0, min(1, confidence)), 2), catalyst=catalyst[:180], risk=risk[:180])
            store.save_analysis(article)
            output.append(article)
    return output
