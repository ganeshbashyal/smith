"""Draft Year 12 (Australian senior-secondary) exam-standard study notes using
the local Ollama model Mr Smith already has selected, then register them in
the document store so the general/science/business personas can retrieve them.

This does NOT fine-tune or otherwise change any model's weights - Mr Smith has
no local training pipeline, and nothing here modifies llama3.2:latest or any
other installed model. It builds a curated, attributed study-notes corpus that
the existing retrieval personas surface alongside the standard "general model
knowledge is not independently verified" warning already built into every
answer. Treat every generated note as a draft revision aid: always check it
against your actual current-year syllabus, teacher guidance and official
marking criteria before relying on it for assessment.

Safe for unattended/overnight use:
- Idempotent generation: a topic whose output file already exists is skipped.
- Idempotent ingestion: Store.add_document() is content-digest keyed, so
  re-running ingestion over unchanged files is a safe no-op.
- Serialized, non-disruptive: before each generation call this waits for
  Ollama to be free of any *other* loaded model instead of force-unloading it,
  so a live chat/research request already in flight is never interrupted and
  Gemma is never concurrently loaded alongside the daily model.
- Local-only: generated notes live under data/year12_knowledge/, which is
  already gitignored; nothing is sent anywhere but the local Ollama instance.

Usage (from the repo root, with .venv active):
    python scripts/build_year12_knowledge.py                 # generate + ingest
    python scripts/build_year12_knowledge.py --ingest-only    # re-sync DB only
    python scripts/build_year12_knowledge.py --limit 5        # cap new topics
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from assistant_api.config import Settings
from assistant_api.storage import Store

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "year12_knowledge"
LOG_PATH = OUT_DIR / "build.log"

DISCLAIMER = (
    "Model-drafted Year 12 study notes (local Ollama model; general training "
    "knowledge, not retrieved evidence). Not verified against any specific "
    "current-year syllabus, textbook or official marking criteria - cross-check "
    "with your actual course materials and teacher before relying on this for "
    "assessment preparation."
)

SYSTEM_PROMPT = (
    "You are an exam-preparation tutor producing exemplar-standard Year 12 "
    "(Australian senior-secondary) study notes. Write accurate, well-structured "
    "notes that would earn top marks if a student understood and applied them. "
    "Structure the response with these Markdown headings: "
    "## Key concepts (precise terminology, bullet list); "
    "## Worked example (full working shown); "
    "## Exam-technique tips (2-3 concise tips); "
    "## Common mistakes (2-3 mistakes to avoid); "
    "## Practice question (one question with a model-answer outline). "
    "Describe general, stable subject knowledge only - do not claim to quote "
    "any specific textbook page, past exam paper or official syllabus wording. "
    "Keep the whole response to roughly 350-600 words."
)


@dataclass(frozen=True)
class Topic:
    subject: str
    title: str
    focus: str
    category: str  # must match an existing persona's knowledge_sources category


# Deliberately bounded: six core senior-secondary subjects, each with a
# handful of high-value, stable (non-current-events) topics. Categories map
# directly onto assistant_api.personas.PERSONAS[...].knowledge_sources so the
# existing general/science/business personas can retrieve these notes.
CURRICULUM: tuple[Topic, ...] = (
    # Mathematical Methods
    Topic("Mathematical Methods", "Differentiation rules and applications",
          "product/quotient/chain rule, rates of change, optimisation problems", "science"),
    Topic("Mathematical Methods", "Integration and area under curves",
          "antiderivatives, definite integrals, area between curves, applications", "science"),
    Topic("Mathematical Methods", "Probability distributions",
          "discrete and continuous distributions, binomial, normal distribution, expected value", "science"),
    Topic("Mathematical Methods", "Exponential and logarithmic functions",
          "laws of logs/exponents, growth and decay models, solving equations", "science"),
    Topic("Mathematical Methods", "Trigonometric functions and identities",
          "unit circle, graphing, Pythagorean/compound-angle identities, solving trig equations", "science"),
    Topic("Mathematical Methods", "Statistical inference",
          "sampling distributions, confidence intervals, hypothesis testing basics", "science"),
    # Physics
    Topic("Physics", "Newtonian mechanics",
          "forces, Newton's three laws, circular motion, momentum and impulse", "science"),
    Topic("Physics", "Electricity and circuits",
          "Ohm's law, series/parallel circuits, power, electromagnetic induction basics", "science"),
    Topic("Physics", "Waves and optics",
          "wave properties, reflection/refraction, interference, diffraction", "science"),
    Topic("Physics", "Thermodynamics",
          "heat transfer, specific heat capacity, laws of thermodynamics, entropy basics", "science"),
    Topic("Physics", "Nuclear physics and radioactivity",
          "decay types, half-life, nuclear binding energy, fission/fusion", "science"),
    Topic("Physics", "Projectile motion",
          "independence of horizontal/vertical motion, range and time-of-flight derivations", "science"),
    # Chemistry
    Topic("Chemistry", "Chemical bonding",
          "ionic, covalent, metallic bonding, intermolecular forces, structure-property links", "science"),
    Topic("Chemistry", "Stoichiometry and the mole concept",
          "molar mass, limiting reagents, percentage yield, molarity calculations", "science"),
    Topic("Chemistry", "Acids, bases and pH",
          "Bronsted-Lowry theory, pH/pOH calculations, titration curves, buffers", "science"),
    Topic("Chemistry", "Chemical equilibrium",
          "Le Chatelier's principle, equilibrium constant Kc, factors affecting equilibrium", "science"),
    Topic("Chemistry", "Organic chemistry fundamentals",
          "functional groups, IUPAC naming, substitution/addition/elimination reactions", "science"),
    Topic("Chemistry", "Redox reactions and electrochemistry",
          "oxidation states, galvanic cells, electrolysis, standard electrode potentials", "science"),
    # Biology
    Topic("Biology", "Cell structure and function",
          "organelles, membrane transport, prokaryotic vs eukaryotic cells", "science"),
    Topic("Biology", "DNA, genes and protein synthesis",
          "DNA structure, transcription, translation, gene regulation basics", "science"),
    Topic("Biology", "Genetics and inheritance",
          "Mendelian genetics, Punnett squares, pedigree analysis, mutation", "science"),
    Topic("Biology", "Evolution and natural selection",
          "evidence for evolution, mechanisms of selection, speciation", "science"),
    Topic("Biology", "Homeostasis and human biology",
          "feedback loops, thermoregulation, blood glucose regulation", "science"),
    Topic("Biology", "Ecosystems and energy flow",
          "trophic levels, energy transfer efficiency, nutrient cycles, biodiversity", "science"),
    # Economics & Business Studies
    Topic("Economics", "Demand and supply analysis",
          "determinants of demand/supply, elasticity, market equilibrium shifts", "business"),
    Topic("Economics", "Market structures",
          "perfect competition, monopoly, oligopoly: characteristics and implications", "business"),
    Topic("Economics", "Macroeconomic indicators",
          "GDP, inflation (CPI), unemployment, the business cycle", "business"),
    Topic("Economics", "Fiscal and monetary policy",
          "government budget tools, central bank interest-rate tools, policy trade-offs", "business"),
    Topic("Business Studies", "Business planning and strategy",
          "SWOT/PEST analysis, competitive strategy frameworks, strategic decision-making", "business"),
    Topic("Business Studies", "Marketing fundamentals",
          "the marketing mix (4Ps), market segmentation, branding and positioning", "business"),
    # English (stable exam-technique frameworks, not specific prescribed texts)
    Topic("English", "Persuasive and argumentative writing techniques",
          "rhetorical devices, structuring an argument, register and tone control", "general"),
    Topic("English", "Textual analysis frameworks",
          "analysing structure, language techniques, characterisation, thematic development", "general"),
    Topic("English", "Essay structure for high-stakes exams",
          "thesis-driven introductions, topic sentences, evidence integration, cohesive conclusions", "general"),
    Topic("English", "Comparative analysis technique",
          "structuring comparative essays, identifying thematic/stylistic links across two texts", "general"),
)


def slugify(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", text.lower())).strip("-")


def topic_path(topic: Topic, out_dir: Path = OUT_DIR) -> Path:
    return out_dir / slugify(topic.subject) / f"{slugify(topic.title)}.md"


async def wait_until_model_free(client: httpx.AsyncClient, model: str, *,
                                 sleep=asyncio.sleep, poll_seconds: float = 30.0,
                                 logger: logging.Logger | None = None) -> None:
    """Block until Ollama has no model loaded other than ``model``.

    Never force-unloads another model: a live chat/research request already in
    flight is left alone and this simply waits its turn.
    """
    log = logger or logging.getLogger(__name__)
    while True:
        try:
            response = await client.get("/api/ps")
            response.raise_for_status()
            loaded = [item["name"] for item in response.json().get("models", []) if isinstance(item, dict)]
        except (httpx.HTTPError, ValueError, KeyError, TypeError):
            log.warning("Could not query Ollama /api/ps; retrying shortly")
            await sleep(poll_seconds)
            continue
        others = [name for name in loaded if name != model]
        if not others:
            return
        log.info("Waiting for %s to be unloaded before drafting with %s", others, model)
        await sleep(poll_seconds)


async def generate_notes(client: httpx.AsyncClient, model: str, topic: Topic, *,
                          max_attempts: int = 3, sleep=asyncio.sleep,
                          logger: logging.Logger | None = None) -> str | None:
    """Request one topic's notes from Ollama; returns None after exhausting retries."""
    log = logger or logging.getLogger(__name__)
    prompt = (
        f"Subject: {topic.subject}\nTopic: {topic.title}\n"
        f"Focus areas: {topic.focus}\nProduce the study notes now."
    )
    for attempt in range(1, max_attempts + 1):
        try:
            response = await client.post("/api/chat", json={
                "model": model, "stream": False, "keep_alive": "5m",
                "options": {"num_ctx": 4096, "num_predict": 1024},
                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                             {"role": "user", "content": prompt}],
            })
            response.raise_for_status()
            result = response.json()
            content = result.get("message", {}).get("content")
            if isinstance(content, str) and len(content.strip()) >= 200:
                return content.strip()
            log.warning("Attempt %d for %r returned unusably short content", attempt, topic.title)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
            log.warning("Attempt %d for %r failed: %s", attempt, topic.title, error)
        if attempt < max_attempts:
            await sleep(min(60.0, 5.0 * attempt))
    log.error("Giving up on %r after %d attempts", topic.title, max_attempts)
    return None


def write_notes(topic: Topic, model: str, content: str, out_dir: Path = OUT_DIR) -> Path:
    path = topic_path(topic, out_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(timezone.utc).isoformat()
    body = (
        f"---\n"
        f"subject: {topic.subject}\n"
        f"topic: {topic.title}\n"
        f"category: {topic.category}\n"
        f"model: {model}\n"
        f"generated_at: {generated_at}\n"
        f"---\n\n"
        f"{content}\n\n---\n{DISCLAIMER}\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


def ingest_existing(store: Store, curriculum: tuple[Topic, ...] = CURRICULUM,
                     out_dir: Path = OUT_DIR, logger: logging.Logger | None = None) -> int:
    """Register every already-generated topic file in the document store.

    Idempotent: Store.add_document() is digest-keyed, so re-ingesting an
    unchanged file is a safe no-op and this can be re-run any time (including
    after only partial generation, or to resync a rebuilt database).
    """
    log = logger or logging.getLogger(__name__)
    ingested = 0
    for topic in curriculum:
        path = topic_path(topic, out_dir)
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        title = f"Year 12 {topic.subject}: {topic.title} (model-drafted study notes)"
        _, created = store.add_document(title, topic.category, "working_note", f"local-llm:{slugify(topic.title)}", content)
        ingested += 1
        if created:
            log.info("Ingested new document: %s", title)
    return ingested


def _configure_logging() -> logging.Logger:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("build_year12_knowledge")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        file_handler.setFormatter(formatter)
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        logger.addHandler(stream_handler)
    return logger


async def run(*, ingest_only: bool, limit: int | None) -> None:
    logger = _configure_logging()
    settings = Settings.from_env()
    store = Store(settings.data_dir)
    model = store.get_model(settings.daily_model)
    logger.info("Using model %s via %s", model, settings.ollama_url)

    if not ingest_only:
        generated = 0
        async with httpx.AsyncClient(base_url=settings.ollama_url, timeout=180) as client:
            for topic in CURRICULUM:
                path = topic_path(topic)
                if path.exists():
                    continue
                if limit is not None and generated >= limit:
                    logger.info("Reached --limit %d new topics this run; stopping generation", limit)
                    break
                await wait_until_model_free(client, model, logger=logger)
                logger.info("Drafting %s / %s", topic.subject, topic.title)
                content = await generate_notes(client, model, topic, logger=logger)
                if content is None:
                    continue
                write_notes(topic, model, content)
                generated += 1
        logger.info("Generated %d new topic(s) this run", generated)

    count = ingest_existing(store, logger=logger)
    logger.info("Document store now has %d Year 12 notes registered from disk", count)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ingest-only", action="store_true", help="Skip generation; only re-sync the document store from existing files")
    parser.add_argument("--limit", type=int, default=None, help="Cap the number of newly generated topics this run")
    args = parser.parse_args()
    asyncio.run(run(ingest_only=args.ingest_only, limit=args.limit))


if __name__ == "__main__":
    main()
