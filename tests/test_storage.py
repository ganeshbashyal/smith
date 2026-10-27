import sqlite3

from assistant_api.storage import Store


def test_document_migration_makes_verified_backup_and_rebuilds_search(tmp_path):
    database = tmp_path / "mr_smith.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("""
            CREATE TABLE documents (
                id INTEGER PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
                source_type TEXT NOT NULL, source TEXT NOT NULL, content TEXT NOT NULL,
                digest TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(digest, category, source_type, source)
            )
        """)
        connection.execute("""
            CREATE TABLE history (
                id INTEGER PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL,
                persona TEXT NOT NULL, model TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute(
            """INSERT INTO documents(title, category, source_type, source, content, digest)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("Existing procedure", "engineering", "source_document", "archive/procedure.txt",
             "Legacy resolver acceptance checklist", "legacy-digest"),
        )
        connection.execute(
            "INSERT INTO history(question, answer, persona, model) VALUES (?, ?, ?, ?)",
            ("Old question", "Old answer", "general", "daily"),
        )

    store = Store(tmp_path)

    backups = list(tmp_path.glob("mr_smith.pre-storage-migration-*.sqlite3.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT content FROM documents WHERE id=1").fetchone()[0] == (
            "Legacy resolver acceptance checklist"
        )
        assert backup.execute("SELECT answer FROM history WHERE id=1").fetchone()[0] == "Old answer"
    assert store.list_documents(query="resolver")[0]["id"] == 1
    assert store.get_document(1)["revision"] == 1
    assert store.get_document(1)["review_status"] == "pending_human_review"
    old_history = store.get_history(1)
    assert old_history["answer"] == "Old answer"
    assert old_history["sources"] == [] and not old_history["is_research"]
    with store.connect() as connection:
        assert connection.execute("SELECT MAX(version) FROM app_schema_version").fetchone()[0] == 2


def test_document_updates_are_versioned_and_search_index_tracks_changes(tmp_path):
    store = Store(tmp_path)
    identity, created = store.add_document(
        "Procedure", "engineering", "source_document", "procedure.txt", "Initial servo test",
    )
    assert created
    revision = store.update_document(
        identity, title="Procedure", category="engineering", source_type="source_document",
        source="procedure.txt", content="Final encoder calibration test",
    )
    assert revision == 2
    assert store.list_documents(query="servo") == []
    assert store.list_documents(query="encoder")[0]["revision"] == 2
    assert store.list_document_revisions(identity)[0]["action"] == "updated"

    assert store.delete_document(identity)
    assert store.list_documents(query="encoder") == []
    assert store.get_document(identity) is None
    assert store.list_document_revisions(identity) == []


def test_retrieval_uses_fts_and_excludes_rejected_or_retired_documents(tmp_path):
    store = Store(tmp_path)
    store.add_document(
        "Current motor specification", "engineering", "source_document",
        "current-spec.txt", "Approved motor torque range is 12 to 15 Nm.",
        review_status="reviewed",
    )
    store.add_document(
        "Rejected motor specification", "engineering", "source_document",
        "rejected-spec.txt", "Rejected motor torque range is 900 Nm.",
        review_status="rejected",
    )
    store.add_document(
        "Retired motor specification", "engineering", "source_document",
        "retired-spec.txt", "Retired motor torque range is 800 Nm.",
        review_status="retired",
    )
    store.add_document(
        "Candidate motor specification", "engineering", "working_note",
        "candidate-spec.txt", "Candidate motor torque range requires verification.",
        review_status="pending_human_review",
    )
    store.add_document(
        "Personal motor preference", "user_context", "user_context",
        "profile", "Prefers lightweight motor housings.",
    )

    evidence = store.search("motor torque range", ("engineering", "user_context"))

    titles = [item["title"] for item in evidence]
    assert "Current motor specification" in titles
    assert "Candidate motor specification" in titles
    assert "Personal motor preference" in titles
    assert "Rejected motor specification" not in titles
    assert "Retired motor specification" not in titles
    assert all(item["review_status"] not in ("rejected", "retired") for item in evidence)
    assert store.search("motor", (), limit=5) == []
    assert store.search("motor", ("engineering",), limit=0) == []


def test_history_only_schema_migration_is_backed_up(tmp_path):
    database = tmp_path / "mr_smith.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("""
            CREATE TABLE history (
                id INTEGER PRIMARY KEY, question TEXT NOT NULL, answer TEXT NOT NULL,
                persona TEXT NOT NULL, model TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        connection.execute(
            "INSERT INTO history(question, answer, persona, model) VALUES (?, ?, ?, ?)",
            ("Preserve me", "Private answer", "general", "daily"),
        )

    store = Store(tmp_path)
    backups = list(tmp_path.glob("mr_smith.pre-storage-migration-*.sqlite3.bak"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert backup.execute("SELECT answer FROM history WHERE id=1").fetchone()[0] == "Private answer"
    assert store.get_history(1)["answer"] == "Private answer"
