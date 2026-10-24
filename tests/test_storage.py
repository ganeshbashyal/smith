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
    old_history = store.get_history(1)
    assert old_history["answer"] == "Old answer"
    assert old_history["sources"] == [] and not old_history["is_research"]


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
