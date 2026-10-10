from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "mr_smith.sqlite3"
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
                    source_type TEXT NOT NULL, source TEXT NOT NULL, content TEXT NOT NULL,
                    digest TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(digest, category, source_type, source)
                );
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL,
                    persona TEXT NOT NULL, model TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS tool_cache (
                    key TEXT PRIMARY KEY, payload TEXT NOT NULL, expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tool_status (
                    name TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            with db:
                yield db
        finally:
            db.close()

    def get_model(self, default: str) -> str:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key='selected_model'").fetchone()
            return row["value"] if row else default

    def set_model(self, model: str):
        with self.connect() as db:
            db.execute("INSERT INTO settings VALUES ('selected_model', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (model,))

    def add_document(self, title: str, category: str, source_type: str, source: str, content: str) -> tuple[int, bool]:
        digest = hashlib.sha256(content.strip().encode()).hexdigest()
        with self.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO documents(title,category,source_type,source,content,digest) VALUES (?,?,?,?,?,?)",
                (title, category, source_type, source, content.strip(), digest),
            )
            row = db.execute(
                "SELECT id FROM documents WHERE digest=? AND category=? AND source_type=? AND source=?",
                (digest, category, source_type, source),
            ).fetchone()
            return row["id"], cursor.rowcount == 1

    def search(self, question: str, categories: tuple[str, ...], limit: int = 5) -> list[dict]:
        terms = set(re.findall(r"\b\w{3,}\b", question.lower()))
        placeholders = ",".join("?" for _ in categories)
        with self.connect() as db:
            rows = db.execute(
                f"SELECT id,title,category,source_type,source,content FROM documents WHERE category IN ({placeholders}) ORDER BY id",
                categories,
            ).fetchall()
        ranked = []
        for row in rows:
            tokens = set(re.findall(r"\b\w{3,}\b", (row["title"] + " " + row["content"]).lower()))
            score = len(terms & tokens)
            if score or row["category"] == "user_context":
                ranked.append((score, row["id"], dict(row)))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [
            {**row, "id": f"document:{row['id']}", "content": row["content"][:6000],
             "relevance": score, "evidence_status": row["source_type"]}
            for score, _, row in ranked[:limit]
        ]

    def save_answer(self, question: str, answer: str, persona: str, model: str):
        with self.connect() as db:
            db.execute("INSERT INTO history(question,answer,persona,model) VALUES (?,?,?,?)", (question, answer, persona, model))

    def cached_tool(self, key: str, now: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT payload FROM tool_cache WHERE key=? AND expires_at>?", (key, now)).fetchone()
            return json.loads(row["payload"]) if row else None

    def save_tool(self, key: str, result: dict, expires_at: str):
        with self.connect() as db:
            db.execute("INSERT INTO tool_cache VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload, expires_at=excluded.expires_at",
                       (key, json.dumps(result), expires_at))

    def record_tool_status(self, name: str, result: dict):
        with self.connect() as db:
            db.execute("INSERT INTO tool_status VALUES (?,?) ON CONFLICT(name) DO UPDATE SET payload=excluded.payload",
                       (name, json.dumps({key: result.get(key) for key in ("status", "error", "retrieved_at", "freshness", "cached")})))

    def tool_status(self) -> dict:
        with self.connect() as db:
            return {row["name"]: json.loads(row["payload"]) for row in db.execute("SELECT name,payload FROM tool_status")}
