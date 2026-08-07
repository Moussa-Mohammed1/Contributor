"""Database engine: lifecycle, schema versioning and retention pruning."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from keeper.database.engine import Database
from keeper.database.models import LogRecord
from keeper.utils.time import now_utc


def test_connect_creates_db(tmp_path: Path) -> None:
    path = tmp_path / "sub" / "contributor.db"
    db = Database(path).connect()
    assert path.exists()
    assert db.path == path
    assert db.engine is not None
    db.close()


def test_schema_version_set(tmp_path: Path) -> None:
    db = Database(tmp_path / "contributor.db").connect()
    with db.session() as session:
        from sqlalchemy import text

        version = session.execute(
            text("SELECT value FROM settings WHERE key = 'schema_version'")
        ).scalar_one()
    assert int(version) >= 1
    db.close()


def test_engine_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "contributor.db"
    Database(path).connect().close()
    second = Database(path).connect()  # must not raise
    second.close()


def test_session_requires_connect(tmp_path: Path) -> None:
    from keeper.core.exceptions import DatabaseError

    db = Database(tmp_path / "x.db")
    try:
        try:
            db.session()
            raise AssertionError("expected DatabaseError")
        except DatabaseError:
            pass
    finally:
        db.close()


def test_prune_deletes_old_rows(tmp_path: Path) -> None:
    db = Database(tmp_path / "contributor.db").connect()
    with db.session() as session:
        old = LogRecord(ts=now_utc() - timedelta(days=90), level="INFO",
                        logger="test.prune", message="old")
        fresh = LogRecord(ts=now_utc(), level="INFO",
                          logger="test.prune", message="fresh")
        session.add_all([old, fresh])
        session.commit()
    counts = db.prune(retention_days=30)
    assert counts["logs"] == 1
    with db.session() as session:
        messages = session.query(LogRecord.message).all()
    assert [m[0] for m in messages] == ["fresh"]
    db.close()


def test_wal_mode_enabled(tmp_path: Path) -> None:
    db = Database(tmp_path / "contributor.db").connect()
    with db.session() as session:
        journal = session.execute(__import__("sqlalchemy").text("PRAGMA journal_mode")).scalar_one()
    assert journal == "wal"
    db.close()
