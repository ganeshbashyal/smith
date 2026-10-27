"""Versioned, offline SQLite backup and non-overwriting restore operations."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, BadZipFile

from assistant_api.finance.repository import SCHEMA_VERSION as FINANCE_SCHEMA_VERSION
from assistant_api.storage import SCHEMA_VERSION

FORMAT_VERSION = 1
DATABASE_MEMBER = "mr_smith.sqlite3"
MANIFEST_MEMBER = "manifest.json"
MAX_DATABASE_BYTES = 2_000_000_000
MAX_MANIFEST_BYTES = 64_000


class RecoveryError(ValueError):
    """A user-safe invalid backup, database, or destination condition."""


def _integrity_check(database: Path) -> None:
    try:
        with closing(sqlite3.connect(database)) as connection:
            result = connection.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.DatabaseError as exc:
        raise RecoveryError("SQLite database could not be opened or checked") from exc
    if not result or result[0] != "ok":
        raise RecoveryError("SQLite integrity check failed")


def _database_versions(database: Path) -> tuple[int, int | None]:
    try:
        with closing(sqlite3.connect(database)) as connection:
            app_version = connection.execute(
                "SELECT MAX(version) FROM app_schema_version"
            ).fetchone()[0]
            if app_version is None:
                raise RecoveryError("Database does not contain Mr Smith schema version metadata")
            finance_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='finance_schema_version'"
            ).fetchone()
            finance_version = None
            if finance_table:
                finance_version = connection.execute(
                    "SELECT MAX(version) FROM finance_schema_version"
                ).fetchone()[0]
    except sqlite3.DatabaseError as exc:
        raise RecoveryError("Database schema metadata could not be read") from exc
    return int(app_version), int(finance_version) if finance_version is not None else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(database: Path, output: Path) -> dict:
    database = Path(database)
    output = Path(output)
    if not database.is_file():
        raise RecoveryError(f"Source database does not exist: {database}")
    if output.exists():
        raise RecoveryError(f"Refusing to overwrite existing backup: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    temporary_database = None
    temporary_archive = None
    try:
        with tempfile.NamedTemporaryFile(prefix="mr-smith-snapshot-", suffix=".sqlite3", delete=False,
                                         dir=output.parent) as temporary:
            temporary_database = Path(temporary.name)
        source = sqlite3.connect(database)
        snapshot = sqlite3.connect(temporary_database)
        try:
            source.backup(snapshot)
            integrity = snapshot.execute("PRAGMA integrity_check").fetchone()
        finally:
            snapshot.close()
            source.close()
        if not integrity or integrity[0] != "ok":
            raise RecoveryError("SQLite snapshot failed its integrity check")

        app_version, finance_version = _database_versions(temporary_database)
        digest = _sha256(temporary_database)
        manifest = {
            "format": "mr-smith-sqlite-backup",
            "format_version": FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "database": DATABASE_MEMBER,
            "database_sha256": digest,
            "database_size_bytes": temporary_database.stat().st_size,
            "application_schema_version": app_version,
            "finance_schema_version": finance_version,
        }
        with tempfile.NamedTemporaryFile(prefix="mr-smith-backup-", suffix=".zip", delete=False,
                                         dir=output.parent) as temporary:
            temporary_archive = Path(temporary.name)
        with ZipFile(temporary_archive, "w", compression=ZIP_DEFLATED) as archive:
            archive.write(temporary_database, DATABASE_MEMBER)
            archive.writestr(MANIFEST_MEMBER, json.dumps(manifest, indent=2) + "\n")
        os.link(temporary_archive, output)
        temporary_archive.unlink()
        temporary_archive = None
        return manifest
    except sqlite3.DatabaseError as exc:
        raise RecoveryError("Could not create a consistent SQLite backup") from exc
    finally:
        if temporary_database and temporary_database.exists():
            temporary_database.unlink()
        if temporary_archive and temporary_archive.exists():
            temporary_archive.unlink()


def _read_archive(backup: Path, extracted_database: Path) -> dict:
    try:
        with ZipFile(backup, "r") as archive:
            members = archive.namelist()
            if len(members) != 2 or set(members) != {DATABASE_MEMBER, MANIFEST_MEMBER}:
                raise RecoveryError("Backup must contain exactly the database and manifest")
            if archive.getinfo(MANIFEST_MEMBER).file_size > MAX_MANIFEST_BYTES:
                raise RecoveryError("Backup manifest exceeds the 64 KB limit")
            if archive.getinfo(DATABASE_MEMBER).file_size > MAX_DATABASE_BYTES:
                raise RecoveryError("Backup database exceeds the 2 GB restore limit")
            manifest = json.loads(archive.read(MANIFEST_MEMBER))
            if not isinstance(manifest, dict):
                raise RecoveryError("Backup manifest must be a JSON object")
            with archive.open(DATABASE_MEMBER) as source, extracted_database.open("xb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
    except (BadZipFile, KeyError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        raise RecoveryError("Backup archive is invalid or unreadable") from exc
    if (
        manifest.get("format") != "mr-smith-sqlite-backup"
        or manifest.get("format_version") != FORMAT_VERSION
        or manifest.get("database") != DATABASE_MEMBER
    ):
        raise RecoveryError("Backup format or version is unsupported")
    if manifest.get("database_size_bytes") != extracted_database.stat().st_size:
        raise RecoveryError("Backup database size does not match its manifest")
    if manifest.get("database_sha256") != _sha256(extracted_database):
        raise RecoveryError("Backup database checksum does not match its manifest")
    _integrity_check(extracted_database)
    app_version, finance_version = _database_versions(extracted_database)
    if manifest.get("application_schema_version") != app_version:
        raise RecoveryError("Backup application schema version does not match its manifest")
    if manifest.get("finance_schema_version") != finance_version:
        raise RecoveryError("Backup finance schema version does not match its manifest")
    if app_version > SCHEMA_VERSION:
        raise RecoveryError("Backup was created by a newer application schema")
    if finance_version is not None and finance_version > FINANCE_SCHEMA_VERSION:
        raise RecoveryError("Backup was created by a newer finance schema")
    return manifest


def verify_backup(backup: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="mr-smith-verify-") as scratch:
        database = Path(scratch) / DATABASE_MEMBER
        return _read_archive(Path(backup), database)


def restore_backup(backup: Path, data_dir: Path) -> dict:
    data_dir = Path(data_dir)
    database = data_dir / DATABASE_MEMBER
    if database.exists():
        raise RecoveryError(f"Refusing to overwrite existing database: {database}")
    data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mr-smith-restore-") as scratch:
        extracted = Path(scratch) / DATABASE_MEMBER
        manifest = _read_archive(Path(backup), extracted)
        try:
            with database.open("xb"):
                pass
        except FileExistsError as exc:
            raise RecoveryError(f"Refusing to overwrite existing database: {database}") from exc
        destination = source = None
        try:
            destination = sqlite3.connect(database)
            source = sqlite3.connect(extracted)
            source.backup(destination)
            integrity = destination.execute("PRAGMA integrity_check").fetchone()
            if not integrity or integrity[0] != "ok":
                raise RecoveryError("Restored database failed its integrity check")
        except Exception as exc:
            if destination is not None:
                destination.close()
                destination = None
            if source is not None:
                source.close()
                source = None
            database.unlink(missing_ok=True)
            if isinstance(exc, RecoveryError):
                raise
            if isinstance(exc, sqlite3.DatabaseError):
                raise RecoveryError("Could not restore SQLite database") from exc
            raise
        finally:
            if destination is not None:
                destination.close()
            if source is not None:
                source.close()
    return {"database": str(database), "manifest": manifest, "integrity": "ok"}
