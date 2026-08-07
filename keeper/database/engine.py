"""Database engine lifecycle: create engine, schema, migrations and pruning."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from keeper.core.exceptions import DatabaseError
from keeper.database.models import Base, FailureRecord, LogRecord, PromptRecord

logger = logging.getLogger(__name__)

SCHEMA_VERSION_KEY = "schema_version"
SCHEMA_VERSION = 1


class Database:
    """Owns the SQLite engine, schema initialization and session factory.

    SQLite is opened with WAL mode so the GUI, CLI, API and daemon can safely
    share the same database file.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._engine: Engine | None = None
        self._session_factory: sessionmaker[Session] | None = None
        self._schema_version = 0

    # -- lifecycle ---------------------------------------------------------

    def connect(self) -> "Database":
        """Create the engine, ensure the schema and apply migrations."""
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._engine = create_engine(
                f"sqlite:///{self._path}",
                connect_args={"timeout": 30},
                pool_pre_ping=True,
            )
            self._configure_sqlite()

            self._session_factory = sessionmaker(
                bind=self._engine, expire_on_commit=False, autoflush=False
            )
            Base.metadata.create_all(self._engine)
            self._schema_version = self._read_version()
            if self._schema_version < SCHEMA_VERSION:
                self._migrate(self._schema_version)
            logger.info("Database ready at %s (schema v%d)", self._path, SCHEMA_VERSION)
            return self
        except Exception as exc:  # noqa: BLE001
            raise DatabaseError(f"Failed to open database {self._path}: {exc}") from exc

    def _configure_sqlite(self) -> None:
        @event.listens_for(self._engine, "connect")
        def _set_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001, ARG001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

    @property
    def engine(self) -> Engine:
        if self._engine is None:
            raise DatabaseError("Database not connected. Call connect() first.")
        return self._engine

    def session(self) -> Session:
        """Open a new session (context manager)."""
        if self._session_factory is None:
            raise DatabaseError("Database not connected. Call connect() first.")
        return self._session_factory()

    @property
    def path(self) -> Path:
        return self._path

    # -- versioning --------------------------------------------------------

    def _read_version(self) -> int:
        with self.session() as session:
            row = session.execute(
                text("SELECT value FROM settings WHERE key = :key"), {"key": SCHEMA_VERSION_KEY}
            ).scalar_one_or_none()
        if row is None:
            with self.session() as session:
                session.execute(
                    text("INSERT INTO settings (key, value) VALUES (:key, :value)"),
                    {"key": SCHEMA_VERSION_KEY, "value": str(SCHEMA_VERSION)},
                )
                session.commit()
            return SCHEMA_VERSION
        return int(row)

    def _migrate(self, from_version: int) -> None:
        """Run incremental migrations from ``from_version`` to current."""
        logger.info("Migrating database schema from v%d to v%d", from_version, SCHEMA_VERSION)
        # Future migrations: apply in order and bump settings value.

    def prune(self, retention_days: int) -> dict[str, int]:
        """Delete rows older than ``retention_days`` from high-volume tables.

        Returns a mapping of table name -> deleted row count.
        """
        from datetime import timedelta

        from keeper.utils.time import now_utc

        cutoff = now_utc() - timedelta(days=retention_days)
        counts: dict[str, int] = {}
        try:
            with self.session() as session:
                for model, name, ts_col in (
                    (LogRecord, "logs", LogRecord.ts),
                    (FailureRecord, "failures", FailureRecord.created_at),
                    (PromptRecord, "prompts", PromptRecord.created_at),
                ):
                    deleted = session.query(model).filter(ts_col < cutoff).delete()
                    counts[name] = deleted
                session.commit()
            return counts
        except Exception:  # noqa: BLE001
            logger.warning("Retention prune failed", exc_info=True)
            return counts

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None
            self._session_factory = None
