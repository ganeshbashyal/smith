"""Additive SQLite schema and repository for the finance module.

All tables are created with CREATE TABLE IF NOT EXISTS against the existing
mr_smith.sqlite3 database so the pre-existing settings/documents/history/
tool_cache/tool_status tables, selected model and user history are never
touched. Before the finance tables exist for the first time against a
pre-existing database, a timestamped verified backup copy is taken.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import shutil
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS finance_schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS fin_securities (
    id INTEGER PRIMARY KEY, symbol TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
    sector TEXT, notes TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fin_evidence (
    id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, title TEXT NOT NULL,
    publisher TEXT NOT NULL, source_type TEXT NOT NULL, document_type TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '', published_on TEXT, period TEXT,
    content_hash TEXT NOT NULL, page_count INTEGER NOT NULL DEFAULT 1,
    limitations TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, content_hash)
);

CREATE TABLE IF NOT EXISTS fin_evidence_passages (
    id INTEGER PRIMARY KEY, evidence_id INTEGER NOT NULL REFERENCES fin_evidence(id),
    page_number INTEGER NOT NULL, chunk_index INTEGER NOT NULL DEFAULT 1, text TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fin_statement_revisions (
    id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, period TEXT NOT NULL,
    fiscal_period_end TEXT NOT NULL, revision INTEGER NOT NULL,
    currency TEXT NOT NULL, scale TEXT NOT NULL, consolidated INTEGER NOT NULL DEFAULT 1,
    figures_json TEXT NOT NULL, evidence_id INTEGER, source_note TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, period, fiscal_period_end, revision)
);

CREATE TABLE IF NOT EXISTS fin_statements (
    id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, period TEXT NOT NULL,
    fiscal_period_end TEXT NOT NULL, currency TEXT NOT NULL, scale TEXT NOT NULL,
    consolidated INTEGER NOT NULL DEFAULT 1, figures_json TEXT NOT NULL,
    evidence_id INTEGER, source_note TEXT NOT NULL DEFAULT '',
    confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, period, fiscal_period_end)
);

CREATE TABLE IF NOT EXISTS fin_thesis_revisions (
    id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, revision INTEGER NOT NULL,
    thesis TEXT NOT NULL, counter_thesis TEXT NOT NULL DEFAULT '',
    assumptions TEXT NOT NULL DEFAULT '', sensitivities TEXT NOT NULL DEFAULT '',
    catalysts TEXT NOT NULL DEFAULT '', risks TEXT NOT NULL DEFAULT '',
    invalidation_criteria TEXT NOT NULL DEFAULT '', evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, revision)
);

CREATE TABLE IF NOT EXISTS fin_watchlists (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL, symbols_json TEXT NOT NULL,
    price_alert_threshold_pct TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fin_price_bars (
    id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, observed_on TEXT NOT NULL,
    currency TEXT NOT NULL, close TEXT, adjusted_close TEXT, volume INTEGER,
    retrieved_at TEXT NOT NULL, publisher TEXT NOT NULL DEFAULT 'Yahoo Finance',
    UNIQUE(symbol, observed_on)
);

CREATE TABLE IF NOT EXISTS fin_catalysts (
    id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, title TEXT NOT NULL,
    event_date TEXT NOT NULL, timezone TEXT NOT NULL, source_url TEXT NOT NULL DEFAULT '',
    is_manual INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, title, event_date)
);

CREATE TABLE IF NOT EXISTS fin_alerts (
    id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, kind TEXT NOT NULL,
    message TEXT NOT NULL, trigger_evidence TEXT NOT NULL DEFAULT '',
    observed_on TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(symbol, kind, message, observed_on)
);

CREATE TABLE IF NOT EXISTS fin_valuation_runs (
    id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, kind TEXT NOT NULL,
    inputs_json TEXT NOT NULL, result_json TEXT NOT NULL,
    statement_id INTEGER, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fin_portfolios (
    id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fin_holdings (
    id INTEGER PRIMARY KEY, portfolio_id INTEGER NOT NULL REFERENCES fin_portfolios(id),
    symbol TEXT NOT NULL, units TEXT NOT NULL, cost_basis_total TEXT,
    sector TEXT, exposure_notes TEXT NOT NULL DEFAULT '', is_cash INTEGER NOT NULL DEFAULT 0,
    manual_aud_value TEXT, valuation_date TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def backup_path(db_path: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return db_path.with_name(f"{db_path.stem}.pre-finance-migration-{stamp}.sqlite3.bak")


class FinanceStore:
    """Repository for all finance entities, sharing the main app's sqlite file."""

    def __init__(self, path: Path):
        self.path = path
        self._migrate()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            db.row_factory = sqlite3.Row
            with db:
                yield db
        finally:
            db.close()

    def _migrate(self):
        pre_existing = self.path.exists() and self.path.stat().st_size > 0
        with self.connect() as db:
            already_migrated = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='finance_schema_version'"
            ).fetchone()
        if not already_migrated and pre_existing:
            # Verified backup: copy then confirm the copy opens and matches size.
            dest = backup_path(self.path)
            shutil.copy2(self.path, dest)
            if dest.stat().st_size != self.path.stat().st_size:
                raise RuntimeError("Pre-migration backup verification failed: size mismatch")
            with sqlite3.connect(dest) as verify:
                verify.execute("PRAGMA integrity_check").fetchone()
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute(
                "INSERT OR IGNORE INTO finance_schema_version VALUES (?, ?)",
                (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
            )
            # Additive column migrations for tables created before these columns existed.
            self._ensure_column(db, "fin_price_bars", "observed_at_utc", "TEXT")
            self._ensure_column(db, "fin_price_bars", "exchange_timezone", "TEXT")

    @staticmethod
    def _ensure_column(db: sqlite3.Connection, table: str, column: str, coldef: str) -> None:
        existing = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")

    # -- Securities -----------------------------------------------------
    def upsert_security(self, symbol: str, name: str, sector: str | None, notes: str) -> int:
        with self.connect() as db:
            db.execute(
                """INSERT INTO fin_securities(symbol, name, sector, notes) VALUES (?,?,?,?)
                   ON CONFLICT(symbol) DO UPDATE SET name=excluded.name, sector=excluded.sector, notes=excluded.notes""",
                (symbol, name, sector, notes),
            )
            return db.execute("SELECT id FROM fin_securities WHERE symbol=?", (symbol,)).fetchone()["id"]

    def get_security(self, symbol: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM fin_securities WHERE symbol=?", (symbol,)).fetchone()
            return dict(row) if row else None

    def list_securities(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM fin_securities ORDER BY symbol")]

    # -- Evidence ---------------------------------------------------------
    def add_evidence(self, symbol: str, title: str, publisher: str, source_type: str,
                     document_type: str, source_url: str, published_on, period: str | None,
                     content_hash: str, passages: list[dict], page_count: int,
                     limitations: str = "") -> tuple[int, bool]:
        """``passages`` is a list of {"page_number", "chunk_index", "text"} dicts
        (see evidence.passages_with_page_numbers / plain_text_passages) so a page
        split into several chunks is never miscounted as several distinct pages.
        ``page_count`` is the actual document page count, independent of how
        many text chunks were produced.
        """
        with self.connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO fin_evidence
                   (symbol, title, publisher, source_type, document_type, source_url, published_on,
                    period, content_hash, page_count, limitations)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (symbol, title, publisher, source_type, document_type, source_url,
                 published_on.isoformat() if published_on else None, period,
                 content_hash, page_count, limitations),
            )
            row = db.execute(
                "SELECT id FROM fin_evidence WHERE symbol=? AND content_hash=?", (symbol, content_hash)
            ).fetchone()
            created = cursor.rowcount == 1
            if created:
                for passage in passages:
                    db.execute(
                        "INSERT INTO fin_evidence_passages(evidence_id, page_number, chunk_index, text) VALUES (?,?,?,?)",
                        (row["id"], passage["page_number"], passage["chunk_index"], passage["text"]),
                    )
            return row["id"], created

    def search_evidence(self, symbol: str, query: str, limit: int = 5) -> list[dict]:
        import re
        terms = set(re.findall(r"\b\w{3,}\b", query.lower()))
        with self.connect() as db:
            docs = {row["id"]: dict(row) for row in db.execute(
                "SELECT * FROM fin_evidence WHERE symbol=?", (symbol,))}
            passages = db.execute(
                "SELECT * FROM fin_evidence_passages WHERE evidence_id IN (%s)" % ",".join("?" for _ in docs) or "SELECT 0",
                tuple(docs),
            ).fetchall() if docs else []
        ranked = []
        for passage in passages:
            tokens = set(re.findall(r"\b\w{3,}\b", passage["text"].lower()))
            score = len(terms & tokens)
            if score:
                doc = docs[passage["evidence_id"]]
                ranked.append((score, doc, passage))
        ranked.sort(key=lambda item: -item[0])
        return [
            {"id": f"finance_evidence:{doc['id']}:{passage['page_number']}:{passage['chunk_index']}",
             "title": doc["title"], "publisher": doc["publisher"],
             "source": doc["source_url"] or doc["title"],
             "source_type": doc["source_type"], "published_on": doc["published_on"],
             "page": passage["page_number"], "content": passage["text"][:4000]}
            for score, doc, passage in ranked[:limit]
        ]

    def list_evidence(self, symbol: str) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM fin_evidence WHERE symbol=? ORDER BY id", (symbol,))]

    def get_evidence(self, evidence_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM fin_evidence WHERE id=?", (evidence_id,)).fetchone()
            return dict(row) if row else None

    def evidence_as_context(self, symbol: str, limit: int = 5) -> list[dict]:
        """Most recent evidence documents for a symbol, shaped as chat evidence items.

        Used to link a company's uploaded research into /api/chat and
        /api/research answers when a request names an entity_symbol.
        """
        with self.connect() as db:
            docs = [dict(row) for row in db.execute(
                "SELECT * FROM fin_evidence WHERE symbol=? ORDER BY published_on DESC, id DESC LIMIT ?",
                (symbol, limit),
            )]
        return [
            {"id": f"finance_evidence:{doc['id']}", "title": doc["title"],
             "publisher": doc["publisher"], "source": doc["source_url"] or doc["title"],
             "source_type": doc["source_type"], "published_on": doc["published_on"],
             "content": f"{doc['document_type']} document ({doc['period'] or 'undated period'}). "
                        f"{doc['limitations'] or ''}".strip()}
            for doc in docs
        ]

    # -- Statements (immutable, versioned revisions) -------------------------
    def save_statement(self, symbol: str, period: str, fiscal_period_end, currency: str,
                       scale: str, consolidated: bool, figures: dict,
                       evidence_id: int | None, source_note: str) -> tuple[int, int]:
        """Always inserts a new revision; never updates a prior one in place.

        Returns (row_id, revision_number).
        """
        end_iso = fiscal_period_end.isoformat() if hasattr(fiscal_period_end, "isoformat") else fiscal_period_end
        with self.connect() as db:
            next_revision = db.execute(
                """SELECT COALESCE(MAX(revision), 0) + 1 AS n FROM fin_statement_revisions
                   WHERE symbol=? AND period=? AND fiscal_period_end=?""",
                (symbol, period, end_iso),
            ).fetchone()["n"]
            cursor = db.execute(
                """INSERT INTO fin_statement_revisions
                   (symbol, period, fiscal_period_end, revision, currency, scale, consolidated,
                    figures_json, evidence_id, source_note)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (symbol, period, end_iso, next_revision, currency, scale, int(consolidated),
                 json.dumps(figures), evidence_id, source_note),
            )
            return cursor.lastrowid, next_revision

    def list_statements(self, symbol: str) -> list[dict]:
        """Latest revision per (period, fiscal_period_end), newest period first."""
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                """SELECT s1.* FROM fin_statement_revisions s1
                   WHERE s1.symbol=? AND s1.revision = (
                       SELECT MAX(s2.revision) FROM fin_statement_revisions s2
                       WHERE s2.symbol=s1.symbol AND s2.period=s1.period
                         AND s2.fiscal_period_end=s1.fiscal_period_end
                   )
                   ORDER BY s1.fiscal_period_end DESC""",
                (symbol,),
            )]
        for row in rows:
            row["figures"] = json.loads(row.pop("figures_json"))
        return rows

    def list_statement_revisions(self, symbol: str, period: str, fiscal_period_end: str) -> list[dict]:
        """Full immutable revision history for one (symbol, period, fiscal_period_end)."""
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                """SELECT * FROM fin_statement_revisions
                   WHERE symbol=? AND period=? AND fiscal_period_end=? ORDER BY revision""",
                (symbol, period, fiscal_period_end),
            )]
        for row in rows:
            row["figures"] = json.loads(row.pop("figures_json"))
        return rows

    def get_statement(self, symbol: str, period: str, fiscal_period_end: str) -> dict | None:
        statements = [s for s in self.list_statements(symbol)
                     if s["period"] == period and s["fiscal_period_end"] == fiscal_period_end]
        return statements[0] if statements else None

    def latest_statement(self, symbol: str) -> dict | None:
        statements = self.list_statements(symbol)
        return statements[0] if statements else None

    # -- Thesis revisions ---------------------------------------------------
    def add_thesis_revision(self, symbol: str, **fields) -> int:
        evidence_ids = fields.pop("evidence_ids", [])
        with self.connect() as db:
            next_revision = (db.execute(
                "SELECT COALESCE(MAX(revision), 0) + 1 AS n FROM fin_thesis_revisions WHERE symbol=?", (symbol,)
            ).fetchone()["n"])
            db.execute(
                """INSERT INTO fin_thesis_revisions
                   (symbol, revision, thesis, counter_thesis, assumptions, sensitivities, catalysts, risks,
                    invalidation_criteria, evidence_ids_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (symbol, next_revision, fields.get("thesis", ""), fields.get("counter_thesis", ""),
                 fields.get("assumptions", ""), fields.get("sensitivities", ""), fields.get("catalysts", ""),
                 fields.get("risks", ""), fields.get("invalidation_criteria", ""), json.dumps(evidence_ids)),
            )
            return next_revision

    def list_thesis_revisions(self, symbol: str) -> list[dict]:
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM fin_thesis_revisions WHERE symbol=? ORDER BY revision", (symbol,))]
        for row in rows:
            row["evidence_ids"] = json.loads(row.pop("evidence_ids_json"))
        return rows

    # -- Watchlists / price history / alerts ---------------------------------
    def create_watchlist(self, name: str, symbols: list[str], threshold_pct: str | None) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO fin_watchlists(name, symbols_json, price_alert_threshold_pct) VALUES (?,?,?)",
                (name, json.dumps(symbols), threshold_pct),
            )
            return cursor.lastrowid

    def get_watchlist(self, watchlist_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM fin_watchlists WHERE id=?", (watchlist_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["symbols"] = json.loads(data.pop("symbols_json"))
        return data

    def list_watchlists(self) -> list[dict]:
        with self.connect() as db:
            rows = [dict(row) for row in db.execute("SELECT * FROM fin_watchlists ORDER BY id")]
        for row in rows:
            row["symbols"] = json.loads(row.pop("symbols_json"))
        return rows

    def save_price_bar(self, symbol: str, observed_on: str, currency: str, close: str | None,
                       adjusted_close: str | None, volume: int | None, retrieved_at: str,
                       publisher: str = "Yahoo Finance", observed_at_utc: str | None = None,
                       exchange_timezone: str | None = None) -> bool:
        """Returns True if this observation is newly stored (not previously seen)."""
        with self.connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO fin_price_bars
                   (symbol, observed_on, currency, close, adjusted_close, volume, retrieved_at, publisher,
                    observed_at_utc, exchange_timezone)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (symbol, observed_on, currency, close, adjusted_close, volume, retrieved_at, publisher,
                 observed_at_utc, exchange_timezone),
            )
            return cursor.rowcount == 1

    def price_history(self, symbol: str, limit_days: int = 366) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM fin_price_bars WHERE symbol=? ORDER BY observed_on DESC LIMIT ?",
                (symbol, limit_days),
            )]

    def add_alert(self, symbol: str, kind: str, message: str, trigger_evidence: str, observed_on: str) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO fin_alerts(symbol, kind, message, trigger_evidence, observed_on)
                   VALUES (?,?,?,?,?)""",
                (symbol, kind, message, trigger_evidence, observed_on),
            )
            return cursor.rowcount == 1

    def list_alerts(self, symbol: str | None = None) -> list[dict]:
        with self.connect() as db:
            if symbol:
                rows = db.execute("SELECT * FROM fin_alerts WHERE symbol=? ORDER BY id DESC", (symbol,))
            else:
                rows = db.execute("SELECT * FROM fin_alerts ORDER BY id DESC")
            return [dict(row) for row in rows]

    def add_catalyst(self, symbol: str, title: str, event_date, timezone_name: str,
                     source_url: str, is_manual: bool) -> tuple[int, bool]:
        with self.connect() as db:
            cursor = db.execute(
                """INSERT OR IGNORE INTO fin_catalysts(symbol, title, event_date, timezone, source_url, is_manual)
                   VALUES (?,?,?,?,?,?)""",
                (symbol, title, event_date.isoformat(), timezone_name, source_url, int(is_manual)),
            )
            row = db.execute(
                "SELECT id FROM fin_catalysts WHERE symbol=? AND title=? AND event_date=?",
                (symbol, title, event_date.isoformat()),
            ).fetchone()
            return row["id"], cursor.rowcount == 1

    def list_catalysts(self, symbol: str) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM fin_catalysts WHERE symbol=? ORDER BY event_date", (symbol,))]

    # -- Valuation runs -------------------------------------------------------
    def save_valuation_run(self, symbol: str, kind: str, inputs: dict, result: dict,
                           statement_id: int | None) -> int:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT INTO fin_valuation_runs(symbol, kind, inputs_json, result_json, statement_id) VALUES (?,?,?,?,?)",
                (symbol, kind, json.dumps(inputs), json.dumps(result), statement_id),
            )
            return cursor.lastrowid

    def list_valuation_runs(self, symbol: str) -> list[dict]:
        with self.connect() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM fin_valuation_runs WHERE symbol=? ORDER BY id DESC", (symbol,))]
        for row in rows:
            row["inputs"] = json.loads(row.pop("inputs_json"))
            row["result"] = json.loads(row.pop("result_json"))
        return rows

    # -- Portfolios / holdings -------------------------------------------------
    def create_portfolio(self, name: str) -> int:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO fin_portfolios(name) VALUES (?)", (name,))
            return db.execute("SELECT id FROM fin_portfolios WHERE name=?", (name,)).fetchone()["id"]

    def get_portfolio(self, portfolio_id: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM fin_portfolios WHERE id=?", (portfolio_id,)).fetchone()
            return dict(row) if row else None

    def replace_holdings(self, portfolio_id: int, rows: list[dict]) -> int:
        """Atomic replace: all rows commit together or none do."""
        with self.connect() as db:
            db.execute("DELETE FROM fin_holdings WHERE portfolio_id=?", (portfolio_id,))
            for row in rows:
                db.execute(
                    """INSERT INTO fin_holdings
                       (portfolio_id, symbol, units, cost_basis_total, sector, exposure_notes,
                        is_cash, manual_aud_value, valuation_date)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (portfolio_id, row["symbol"], row["units"], row.get("cost_basis_total"),
                     row.get("sector"), row.get("exposure_notes", ""), int(row.get("is_cash", False)),
                     row.get("manual_aud_value"),
                     row["valuation_date"].isoformat() if row.get("valuation_date") else None),
                )
            return len(rows)

    def list_holdings(self, portfolio_id: int) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM fin_holdings WHERE portfolio_id=? ORDER BY id", (portfolio_id,))]

    def list_portfolios(self) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM fin_portfolios ORDER BY name")]

    def add_holding(self, portfolio_id: int, row: dict) -> int:
        """Single-row holding add (defect-10 CRUD beyond the atomic CSV replace)."""
        with self.connect() as db:
            cursor = db.execute(
                """INSERT INTO fin_holdings
                   (portfolio_id, symbol, units, cost_basis_total, sector, exposure_notes,
                    is_cash, manual_aud_value, valuation_date)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (portfolio_id, row["symbol"], row["units"], row.get("cost_basis_total"),
                 row.get("sector"), row.get("exposure_notes", ""), int(row.get("is_cash", False)),
                 row.get("manual_aud_value"),
                 row["valuation_date"].isoformat() if row.get("valuation_date") else None),
            )
            return cursor.lastrowid

    def update_holding(self, portfolio_id: int, holding_id: int, row: dict) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                """UPDATE fin_holdings SET symbol=?, units=?, cost_basis_total=?, sector=?,
                   exposure_notes=?, is_cash=?, manual_aud_value=?, valuation_date=?
                   WHERE id=? AND portfolio_id=?""",
                (row["symbol"], row["units"], row.get("cost_basis_total"), row.get("sector"),
                 row.get("exposure_notes", ""), int(row.get("is_cash", False)),
                 row.get("manual_aud_value"),
                 row["valuation_date"].isoformat() if row.get("valuation_date") else None,
                 holding_id, portfolio_id),
            )
            return cursor.rowcount == 1

    def delete_holding(self, portfolio_id: int, holding_id: int) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                "DELETE FROM fin_holdings WHERE id=? AND portfolio_id=?", (holding_id, portfolio_id)
            )
            return cursor.rowcount == 1
