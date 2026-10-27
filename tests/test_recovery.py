import sqlite3
from zipfile import ZIP_DEFLATED, ZipFile

import pytest

from assistant_api.finance.repository import FinanceStore
from assistant_api.recovery import RecoveryError, create_backup, restore_backup, verify_backup
from assistant_api.storage import Store


def test_versioned_backup_verifies_and_restores_user_data(tmp_path):
    source_dir = tmp_path / "source"
    store = Store(source_dir)
    store.set_model("llama3.2:latest")
    store.save_answer("Remember this", "Saved response", "general", "llama3.2:latest")
    FinanceStore(source_dir / "mr_smith.sqlite3")
    archive = tmp_path / "exports" / "mr-smith-backup.zip"

    created = create_backup(store.path, archive)
    verified = verify_backup(archive)

    assert created["format_version"] == 1
    assert created["application_schema_version"] == 2
    assert created["finance_schema_version"] == 2
    assert verified == created

    restored = restore_backup(archive, tmp_path / "restored")
    recovered_store = Store(tmp_path / "restored")
    assert restored["integrity"] == "ok"
    assert recovered_store.get_model("default") == "llama3.2:latest"
    assert recovered_store.get_history(1)["answer"] == "Saved response"


def test_restore_refuses_to_overwrite_existing_database(tmp_path):
    store = Store(tmp_path / "source")
    archive = tmp_path / "backup.zip"
    create_backup(store.path, archive)

    target = tmp_path / "target"
    target_store = Store(target)
    target_store.save_answer("Keep", "Do not replace", "general", "daily")
    original_bytes = (target / "mr_smith.sqlite3").read_bytes()

    with pytest.raises(RecoveryError, match="Refusing to overwrite"):
        restore_backup(archive, target)

    assert (target / "mr_smith.sqlite3").read_bytes() == original_bytes
    assert Store(target).get_history(1)["answer"] == "Do not replace"


def test_verify_rejects_changed_archive_database_bytes(tmp_path):
    store = Store(tmp_path / "source")
    archive = tmp_path / "backup.zip"
    create_backup(store.path, archive)
    damaged = tmp_path / "damaged.zip"
    with ZipFile(archive) as source, ZipFile(damaged, "w", ZIP_DEFLATED) as target:
        manifest = source.read("manifest.json")
        target.writestr("manifest.json", manifest)
        content = bytearray(source.read("mr_smith.sqlite3"))
        content[-1] ^= 1
        target.writestr("mr_smith.sqlite3", content)

    with pytest.raises(RecoveryError, match="checksum"):
        verify_backup(damaged)


def test_backup_uses_consistent_snapshot_with_committed_wal_data(tmp_path):
    store = Store(tmp_path)
    connection = sqlite3.connect(store.path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE recovery_probe (value TEXT NOT NULL)")
        connection.execute("INSERT INTO recovery_probe VALUES ('committed WAL content')")
        connection.commit()

        archive = tmp_path / "wal-backup.zip"
        create_backup(store.path, archive)
        verified = verify_backup(archive)
        assert verified["database_size_bytes"] > 0
        restored = restore_backup(archive, tmp_path / "wal-restored")
        with sqlite3.connect(restored["database"]) as recovered:
            assert recovered.execute("SELECT value FROM recovery_probe").fetchone()[0] == (
                "committed WAL content"
            )
    finally:
        connection.close()
