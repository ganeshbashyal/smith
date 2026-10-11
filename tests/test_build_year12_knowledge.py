import asyncio
import logging
from pathlib import Path

import httpx
import pytest

from assistant_api.personas import PERSONAS
from assistant_api.storage import Store
from scripts.build_year12_knowledge import (
    CURRICULUM,
    Topic,
    generate_notes,
    ingest_existing,
    slugify,
    topic_path,
    wait_until_model_free,
    write_notes,
)


def test_curriculum_categories_match_existing_persona_knowledge_sources():
    all_categories = {category for persona in PERSONAS.values() for category in persona.knowledge_sources}
    assert CURRICULUM, "curriculum must not be empty"
    for topic in CURRICULUM:
        assert topic.category in all_categories, f"{topic.category!r} is not a known persona knowledge source"
        assert topic.subject and topic.title and topic.focus


def test_curriculum_has_no_duplicate_topic_paths():
    paths = {topic_path(topic, Path("ignored")) for topic in CURRICULUM}
    assert len(paths) == len(CURRICULUM)


def test_slugify_normalizes_punctuation_and_case():
    assert slugify("Acids, Bases and pH") == "acids-bases-and-ph"
    assert slugify("  Multiple   Spaces--Here ") == "multiple-spaces-here"


def test_generate_notes_retries_then_succeeds():
    attempts = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"message": {"content": "x" * 250}})

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    async def run():
        topic = Topic("Physics", "Newtonian mechanics", "forces", "science")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ollama.local") as client:
            result = await generate_notes(client, "llama3.2:latest", topic, sleep=fake_sleep)
        assert result == "x" * 250
        assert attempts["count"] == 3
        assert len(sleeps) == 2  # backoff before attempt 2 and 3, none after success

    asyncio.run(run())


def test_generate_notes_gives_up_after_max_attempts():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    async def fake_sleep(seconds):
        return None

    async def run():
        topic = Topic("Chemistry", "Acids, bases and pH", "pH calculations", "science")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ollama.local") as client:
            result = await generate_notes(client, "llama3.2:latest", topic, max_attempts=2, sleep=fake_sleep)
        assert result is None

    asyncio.run(run())


def test_generate_notes_rejects_too_short_content():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "too short"}})

    async def fake_sleep(seconds):
        return None

    async def run():
        topic = Topic("Biology", "Cell structure and function", "organelles", "science")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ollama.local") as client:
            result = await generate_notes(client, "llama3.2:latest", topic, max_attempts=1, sleep=fake_sleep)
        assert result is None

    asyncio.run(run())


def test_wait_until_model_free_returns_immediately_when_clear():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"models": []})

    calls = {"sleep": 0}

    async def fake_sleep(seconds):
        calls["sleep"] += 1

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ollama.local") as client:
            await wait_until_model_free(client, "llama3.2:latest", sleep=fake_sleep)
        assert calls["sleep"] == 0

    asyncio.run(run())


def test_wait_until_model_free_waits_for_other_model_without_forcing_unload():
    responses = [
        httpx.Response(200, json={"models": [{"name": "gemma4:26b"}]}),
        httpx.Response(200, json={"models": [{"name": "gemma4:26b"}]}),
        httpx.Response(200, json={"models": []}),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return responses.pop(0)

    calls = {"sleep": 0}

    async def fake_sleep(seconds):
        calls["sleep"] += 1

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ollama.local") as client:
            await wait_until_model_free(client, "llama3.2:latest", sleep=fake_sleep)
        assert calls["sleep"] == 2
        assert responses == []

    asyncio.run(run())


def test_write_notes_embeds_disclaimer_and_frontmatter(tmp_path):
    topic = Topic("Physics", "Newtonian mechanics", "forces", "science")
    path = write_notes(topic, "llama3.2:latest", "Some generated body text.", out_dir=tmp_path)
    text = path.read_text(encoding="utf-8")
    assert "subject: Physics" in text
    assert "Some generated body text." in text
    assert "Not verified against any specific" in text


def test_ingest_existing_is_idempotent_and_digest_keyed(tmp_path):
    topics = (
        Topic("Physics", "Newtonian mechanics", "forces", "science"),
        Topic("Economics", "Demand and supply analysis", "elasticity", "business"),
    )
    for topic in topics:
        write_notes(topic, "llama3.2:latest", "Body text for " + topic.title, out_dir=tmp_path)

    store = Store(tmp_path / "db")
    first_count = ingest_existing(store, topics, out_dir=tmp_path)
    second_count = ingest_existing(store, topics, out_dir=tmp_path)
    assert first_count == 2
    assert second_count == 2  # re-ingestion attempted for both, but...

    with store.connect() as db:
        rows = db.execute("SELECT category, source_type FROM documents").fetchall()
    assert len(rows) == 2  # ...no duplicate rows were created (digest-keyed)
    categories = {row["category"] for row in rows}
    assert categories == {"science", "business"}
    assert all(row["source_type"] == "working_note" for row in rows)


def test_ingest_existing_skips_topics_without_a_file(tmp_path):
    store = Store(tmp_path / "db")
    count = ingest_existing(store, CURRICULUM[:2], out_dir=tmp_path)
    assert count == 0

