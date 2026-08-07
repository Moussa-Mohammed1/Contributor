"""Logging handler that mirrors structured log records into the state database.

This enables the GUI Logs page and `keeper logs` without parsing files.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime

from sqlalchemy.engine import Engine


class DatabaseLogHandler(logging.Handler):
    """A logging.Handler that stores records as JSON rows in SQLite.

    Batching: records are appended to an in-memory buffer and flushed on a
    timer or when the buffer reaches its size limit, keeping database
    round-trips low.
    """

    def __init__(self, engine: Engine, batch_size: int = 50, flush_interval: float = 2.0) -> None:
        super().__init__()
        self._engine = engine
        self._batch_size = batch_size
        self._buffer: list[tuple] = []
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._flush_interval = flush_interval
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record: logging.LogRecord) -> None:  # noqa: PLR6301
        try:
            ts = datetime.fromtimestamp(record.created, tz=UTC)
            extra = {}
            for key, value in record.__dict__.items():
                if key.startswith("_") or key in {
                    "name",
                    "msg",
                    "args",
                    "levelname",
                    "levelno",
                    "pathname",
                    "filename",
                    "module",
                    "exc_info",
                    "exc_text",
                    "stack_info",
                    "lineno",
                    "funcName",
                    "created",
                    "msecs",
                    "relativeCreated",
                    "thread",
                    "threadName",
                    "processName",
                    "process",
                    "message",
                }:
                    continue
                try:
                    json.dumps(value)
                except (TypeError, ValueError):
                    extra[key] = str(value)
                else:
                    extra[key] = value
            message = self.format(record)
            with self._lock:
                self._buffer.append((ts, record.levelname, record.name, message, json.dumps(extra)))
                if len(self._buffer) >= self._batch_size:
                    self._flush_locked()
                elif self._timer is None or not self._timer.is_alive():
                    self._timer = threading.Timer(self._flush_interval, self.flush)
                    self._timer.daemon = True
                    self._timer.start()
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        rows, self._buffer = self._buffer, []
        try:
            with self._engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO log_records (ts, level, logger, message, extra) VALUES (?, ?, ?, ?, ?)",
                    rows,
                )
        except Exception:  # noqa: BLE001
            # Database might be unavailable (during shutdown); drop silently.
            return

    def close(self) -> None:
        self.flush()
        super().close()
