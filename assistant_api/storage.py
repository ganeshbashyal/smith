from contextlib import closing, contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

SCHEMA_VERSION = 2
DOCUMENT_METADATA_COLUMNS = {
    "authority": "TEXT",
    "date_created": "TEXT",
    "date_reviewed": "TEXT",
    "jurisdiction": "TEXT",
    "confidence": "REAL",
    "supersedes": "TEXT",
    "review_status": "TEXT NOT NULL DEFAULT 'pending_human_review'",
}
DOCUMENT_METADATA_FIELDS = tuple(DOCUMENT_METADATA_COLUMNS)


class Store:
    def __init__(self, directory: Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "mr_smith.sqlite3"
        self._backup_before_schema_migration()
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents (
                    id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
                    source_type TEXT NOT NULL, source TEXT NOT NULL, content TEXT NOT NULL,
                    digest TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    revision INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(digest, category, source_type, source)
                );
                CREATE TABLE IF NOT EXISTS history (
                    id INTEGER PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL,
                    persona TEXT NOT NULL, model TEXT NOT NULL,
                    sources_json TEXT NOT NULL DEFAULT '[]',
                    is_research INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS tool_cache (
                    key TEXT PRIMARY KEY, payload TEXT NOT NULL, expires_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tool_status (
                    name TEXT PRIMARY KEY, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_schema_version (
                    version INTEGER NOT NULL,
                    applied_at TEXT NOT NULL
                );
            """)
            db.execute(
                "INSERT INTO app_schema_version(version, applied_at) "
                "SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM app_schema_version)",
                (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
            )
            self._migrate_documents(db)
            self._migrate_history(db)
            self._migrate_document_metadata(db)
            db.execute(
                "UPDATE app_schema_version SET version=?, applied_at=?",
                (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
            )

    def _backup_before_schema_migration(self) -> None:
        if not self.path.exists():
            return
        with closing(sqlite3.connect(self.path)) as db:
            tables = {
                row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            }
            columns = (
                {row[1] for row in db.execute("PRAGMA table_info(documents)")}
                if "documents" in tables else set()
            )
            history_columns = (
                {row[1] for row in db.execute("PRAGMA table_info(history)")}
                if "history" in tables else set()
            )
            fts_exists = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents_fts'"
            ).fetchone()
            revisions_exist = "document_revisions" in tables
            document_migration_needed = (
                "documents" in tables
                and (not {"revision", "updated_at"} <= columns or not fts_exists or not revisions_exist)
            )
            history_migration_needed = (
                "history" in tables and not {"sources_json", "is_research"} <= history_columns
            )
            document_revision_columns = (
                {row[1] for row in db.execute("PRAGMA table_info(document_revisions)")}
                if "document_revisions" in tables else set()
            )
            metadata_migration_needed = (
                "documents" in tables
                and not set(DOCUMENT_METADATA_COLUMNS) <= columns
            ) or (
                "document_revisions" in tables
                and not set(DOCUMENT_METADATA_COLUMNS) <= document_revision_columns
            )
            app_version_missing = "app_schema_version" not in tables or not db.execute(
                "SELECT 1 FROM app_schema_version LIMIT 1"
            ).fetchone()
        if not document_migration_needed and not history_migration_needed and not metadata_migration_needed and not app_version_missing:
            return
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = self.path.with_name(f"mr_smith.pre-storage-migration-{stamp}.sqlite3.bak")
        source = sqlite3.connect(self.path)
        backup = sqlite3.connect(backup_path)
        try:
            source.backup(backup)
            result = backup.execute("PRAGMA integrity_check").fetchone()
            if not result or result[0] != "ok" or backup_path.stat().st_size == 0:
                raise RuntimeError(f"SQLite migration backup failed integrity verification: {backup_path}")
        finally:
            backup.close()
            source.close()

    @staticmethod
    def _migrate_documents(db: sqlite3.Connection) -> None:
        columns = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
        if "revision" not in columns:
            db.execute("ALTER TABLE documents ADD COLUMN revision INTEGER NOT NULL DEFAULT 1")
        if "updated_at" not in columns:
            db.execute("ALTER TABLE documents ADD COLUMN updated_at TEXT")
            db.execute("UPDATE documents SET updated_at = created_at WHERE updated_at IS NULL")
        db.execute("""
            CREATE TABLE IF NOT EXISTS document_revisions (
                id INTEGER PRIMARY KEY,
                document_id INTEGER NOT NULL,
                revision INTEGER NOT NULL,
                action TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source TEXT NOT NULL,
                content TEXT NOT NULL,
                digest TEXT NOT NULL,
                changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                authority TEXT,
                date_created TEXT,
                date_reviewed TEXT,
                jurisdiction TEXT,
                confidence REAL,
                supersedes TEXT,
                review_status TEXT NOT NULL DEFAULT 'pending_human_review',
                UNIQUE(document_id, revision)
            )
        """)
        existing_revision_columns = {
            row[1] for row in db.execute("PRAGMA table_info(document_revisions)")
        }
        for column, definition in DOCUMENT_METADATA_COLUMNS.items():
            if column not in existing_revision_columns:
                db.execute(f"ALTER TABLE document_revisions ADD COLUMN {column} {definition}")
        fts_exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='documents_fts'"
        ).fetchone()
        db.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(
                title, content, source, category, source_type,
                tokenize='unicode61'
            )
        """)
        db.executescript("""
            CREATE TRIGGER IF NOT EXISTS documents_fts_insert AFTER INSERT ON documents BEGIN
                INSERT INTO documents_fts(rowid, title, content, source, category, source_type)
                VALUES (new.id, new.title, new.content, new.source, new.category, new.source_type);
            END;
            CREATE TRIGGER IF NOT EXISTS documents_fts_update AFTER UPDATE ON documents BEGIN
                DELETE FROM documents_fts WHERE rowid = old.id;
                INSERT INTO documents_fts(rowid, title, content, source, category, source_type)
                VALUES (new.id, new.title, new.content, new.source, new.category, new.source_type);
            END;
            CREATE TRIGGER IF NOT EXISTS documents_fts_delete AFTER DELETE ON documents BEGIN
                DELETE FROM documents_fts WHERE rowid = old.id;
            END;
        """)
        if not fts_exists:
            db.execute("""
                INSERT INTO documents_fts(rowid, title, content, source, category, source_type)
                SELECT id, title, content, source, category, source_type FROM documents
            """)

    @staticmethod
    def _migrate_history(db: sqlite3.Connection) -> None:
        columns = {row[1] for row in db.execute("PRAGMA table_info(history)")}
        if "sources_json" not in columns:
            db.execute("ALTER TABLE history ADD COLUMN sources_json TEXT NOT NULL DEFAULT '[]'")
        if "is_research" not in columns:
            db.execute("ALTER TABLE history ADD COLUMN is_research INTEGER NOT NULL DEFAULT 0")

    @staticmethod
    def _migrate_document_metadata(db: sqlite3.Connection) -> None:
        existing_columns = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
        for column, definition in DOCUMENT_METADATA_COLUMNS.items():
            if column not in existing_columns:
                db.execute(f"ALTER TABLE documents ADD COLUMN {column} {definition}")

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

    def add_document(self, title: str, category: str, source_type: str, source: str,
                     content: str, **metadata) -> tuple[int, bool]:
        return self.add_documents([{
            "title": title, "category": category, "source_type": source_type,
            "source": source, "content": content, **metadata,
        }])[0]

    def add_documents(self, documents: list[dict]) -> list[tuple[int, bool]]:
        with self.connect() as db:
            results = []
            for document in documents:
                content = document["content"].strip()
                digest = hashlib.sha256(content.encode()).hexdigest()
                metadata_values = [
                    document.get(field, "pending_human_review" if field == "review_status" else None)
                    for field in DOCUMENT_METADATA_FIELDS
                ]
                metadata_columns = ", ".join(DOCUMENT_METADATA_FIELDS)
                metadata_placeholders = ", ".join("?" for _ in DOCUMENT_METADATA_FIELDS)
                cursor = db.execute(
                    f"""INSERT OR IGNORE INTO documents
                       (title,category,source_type,source,content,digest,{metadata_columns})
                       VALUES (?,?,?,?,?, ?, {metadata_placeholders})""",
                    (document["title"], document["category"], document["source_type"],
                     document["source"], content, digest, *metadata_values),
                )
                row = db.execute(
                    """SELECT id FROM documents WHERE digest=? AND category=?
                       AND source_type=? AND source=?""",
                    (digest, document["category"], document["source_type"], document["source"]),
                ).fetchone()
                results.append((row["id"], cursor.rowcount == 1))
            return results

    def list_documents(self, *, query: str = "", category: str | None = None,
                       source_type: str | None = None, limit: int = 50,
                       offset: int = 0) -> list[dict]:
        terms = re.findall(r"[\w]+", query, flags=re.UNICODE)
        filters = []
        params: list[object] = []
        if category:
            filters.append("d.category = ?")
            params.append(category)
        if source_type:
            filters.append("d.source_type = ?")
            params.append(source_type)
        if terms:
            expression = " OR ".join(f'"{term}"*' for term in terms)
            filters.append("documents_fts MATCH ?")
            params.append(expression)
            join = "JOIN documents_fts ON documents_fts.rowid = d.id"
            order = "bm25(documents_fts), d.id DESC"
        else:
            join = ""
            order = "d.updated_at DESC, d.id DESC"
        where = " WHERE " + " AND ".join(filters) if filters else ""
        params.extend((limit, offset))
        with self.connect() as db:
            rows = db.execute(
                f"""SELECT d.id, d.title, d.category, d.source_type, d.source, d.digest,
                           d.created_at, d.updated_at, d.revision, length(d.content) AS content_length,
                           {", ".join(f"d.{field}" for field in DOCUMENT_METADATA_FIELDS)}
                    FROM documents d {join}{where} ORDER BY {order} LIMIT ? OFFSET ?""",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def get_document(self, identity: int) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT id, title, category, source_type, source, content, digest,
                          created_at, updated_at, revision,
                          authority, date_created, date_reviewed, jurisdiction,
                          confidence, supersedes, review_status
                   FROM documents WHERE id=?""",
                (identity,),
            ).fetchone()
            return dict(row) if row else None

    def update_document(self, identity: int, *, title: str, category: str,
                        source_type: str, source: str, content: str, **metadata) -> int | None:
        digest = hashlib.sha256(content.strip().encode()).hexdigest()
        with self.connect() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone()
            if not row:
                return None
            previous = dict(row)
            normalized_metadata = {
                field: metadata.get(field, previous[field])
                for field in DOCUMENT_METADATA_FIELDS
            }
            if all(previous[field] == value for field, value in {
                "title": title, "category": category, "source_type": source_type,
                "source": source, "content": content.strip(), "digest": digest,
                **normalized_metadata,
            }.items()):
                return previous["revision"]
            revision = previous["revision"]
            db.execute(
                """INSERT INTO document_revisions
                   (document_id, revision, action, title, category, source_type, source, content, digest,
                    authority, date_created, date_reviewed, jurisdiction, confidence, supersedes, review_status)
                   VALUES (?, ?, 'updated', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (identity, revision, previous["title"], previous["category"], previous["source_type"],
                 previous["source"], previous["content"], previous["digest"],
                 *(previous[field] for field in DOCUMENT_METADATA_FIELDS)),
            )
            db.execute(
                """UPDATE documents SET title=?, category=?, source_type=?, source=?, content=?,
                   digest=?, revision=?, updated_at=CURRENT_TIMESTAMP,
                   authority=?, date_created=?, date_reviewed=?, jurisdiction=?, confidence=?,
                   supersedes=?, review_status=? WHERE id=?""",
                (title, category, source_type, source, content.strip(), digest, revision + 1,
                 *(normalized_metadata[field] for field in DOCUMENT_METADATA_FIELDS), identity),
            )
            return revision + 1

    def delete_document(self, identity: int) -> bool:
        with self.connect() as db:
            exists = db.execute("SELECT 1 FROM documents WHERE id=?", (identity,)).fetchone()
            if not exists:
                return False
            db.execute("DELETE FROM document_revisions WHERE document_id=?", (identity,))
            db.execute("DELETE FROM documents WHERE id=?", (identity,))
            return True

    def list_document_revisions(self, identity: int) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT document_id, revision, action, title, category, source_type,
                          source, digest, changed_at, authority, date_created, date_reviewed,
                          jurisdiction, confidence, supersedes, review_status
                   FROM document_revisions WHERE document_id=? ORDER BY revision""",
                (identity,),
            ).fetchall()
        return [dict(row) for row in rows]

    def search(self, question: str, categories: tuple[str, ...], limit: int = 5) -> list[dict]:
        if not categories or limit <= 0:
            return []
        terms = set(re.findall(r"\b\w{3,}\b", question.lower(), flags=re.UNICODE))
        placeholders = ",".join("?" for _ in categories)
        params: list[object] = list(categories)
        category_filter = f"category IN ({placeholders})"
        if terms:
            expression = " OR ".join(f'"{term}"*' for term in sorted(terms))
            query = f"""WITH matches AS (
                            SELECT rowid FROM documents_fts WHERE documents_fts MATCH ?
                        )
                        SELECT d.id,d.title,d.category,d.source_type,d.source,d.content,
                               d.authority,d.date_created,d.date_reviewed,d.jurisdiction,
                               d.confidence,d.supersedes,d.review_status
                        FROM documents d
                        JOIN documents_fts ON documents_fts.rowid=d.id
                        LEFT JOIN matches ON matches.rowid=d.id
                        WHERE d.{category_filter}
                          AND d.review_status NOT IN ('rejected','retired')
                          AND (matches.rowid IS NOT NULL OR d.category='user_context')
                        ORDER BY bm25(documents_fts), d.id"""
            params = [expression, *params]
        else:
            query = f"""SELECT id,title,category,source_type,source,content,
                               authority,date_created,date_reviewed,jurisdiction,
                               confidence,supersedes,review_status
                        FROM documents
                        WHERE {category_filter}
                          AND category='user_context'
                          AND review_status NOT IN ('rejected','retired')
                        ORDER BY id"""
        with self.connect() as db:
            rows = db.execute(query, params).fetchall()
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

    def save_answer(self, question: str, answer: str, persona: str, model: str,
                    *, sources: list[dict] | None = None, research: bool = False):
        attribution_fields = (
            "id", "source", "source_type", "title", "publisher", "published",
            "published_at", "url", "freshness", "retrieved_at", "observed_at",
        )
        attributions = [
            {key: source[key] for key in attribution_fields if key in source}
            for source in (sources or [])
        ]
        with self.connect() as db:
            db.execute(
                """INSERT INTO history
                   (question, answer, persona, model, sources_json, is_research)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (question, answer, persona, model, json.dumps(attributions), int(research)),
            )

    def list_history(self, *, limit: int = 50, offset: int = 0) -> list[dict]:
        with self.connect() as db:
            rows = db.execute(
                """SELECT id, question, persona, model, created_at
                   FROM history ORDER BY id DESC LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_history(self, identity: int) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT id, question, answer, persona, model, sources_json, is_research, created_at
                   FROM history WHERE id=?""",
                (identity,),
            ).fetchone()
        if not row:
            return None
        entry = dict(row)
        entry["sources"] = json.loads(entry.pop("sources_json"))
        entry["is_research"] = bool(entry["is_research"])
        return entry

    def get_research_export(self, identity: int) -> dict | None:
        with self.connect() as db:
            row = db.execute(
                """SELECT id, question, answer, persona, model, sources_json, created_at
                   FROM history WHERE id=? AND is_research=1""",
                (identity,),
            ).fetchone()
        if not row:
            return None
        entry = dict(row)
        entry["sources"] = json.loads(entry.pop("sources_json"))
        return entry

    def delete_history(self, identity: int) -> bool:
        with self.connect() as db:
            cursor = db.execute("DELETE FROM history WHERE id=?", (identity,))
            return cursor.rowcount == 1

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
