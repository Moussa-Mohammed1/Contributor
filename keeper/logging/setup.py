"""Structured logging setup.

Two sinks are always configured:
  * a structured JSON line logger on a file in the data dir,
  * an optional database handler used by the GUI and `keeper logs`.

The root logger never writes ANSI noise to stdout; console output is owned by
Rich/Typer at the CLI layer.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.engine import Engine

from keeper.logging.db_handler import DatabaseLogHandler


class JsonFormatter(logging.Formatter):
    """Format log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, datefmt="%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
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
                "taskName",
            }:
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                continue
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


class PlainFormatter(logging.Formatter):
    """Readable multi-line formatter for interactive console sessions."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record, datefmt="%Y-%m-%d %H:%M:%S")
        base = f"{timestamp} {record.levelname:<8} {record.name}: {record.getMessage()}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def setup_logging(
    *,
    level: str = "INFO",
    structured: bool = True,
    log_dir: Path | None = None,
    db_engine: Engine | None = None,
    console: bool = False,
) -> logging.Logger:
    """Configure the application logging tree.

    Args:
        level: root log level name.
        structured: use JSON file formatting when True.
        log_dir: directory for the rotating file log.
        db_engine: optional SQLAlchemy engine to mirror logs into SQLite.
        console: additionally emit to stderr (used in foreground mode).
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    handlers: list[logging.Handler] = []
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        file_path = log_dir / "contributor.log"
        try:
            from logging.handlers import RotatingFileHandler

            file_handler = RotatingFileHandler(
                file_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
            )
            formatter: logging.Formatter = (
                JsonFormatter() if structured else PlainFormatter()
            )
            file_handler.setFormatter(formatter)
            handlers.append(file_handler)
        except OSError:
            root.warning("Could not open log file %s; logging to console only.", file_path)

    if db_engine is not None:
        try:
            db_handler = DatabaseLogHandler(db_engine)
            db_handler.setFormatter(PlainFormatter())
            handlers.append(db_handler)
        except Exception:  # noqa: BLE001
            root.warning("Database logging handler unavailable", exc_info=True)

    if console or (log_dir is None and db_engine is None):
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setFormatter(PlainFormatter())
        handlers.append(console_handler)

    if not handlers:
        handlers.append(logging.NullHandler())

    root.handlers = []
    root.addHandler(logging.NullHandler())
    for handler in handlers:
        root.addHandler(handler)

    root.info(
        "Logging configured",
        extra={"structured": structured, "level": level, "handlers": [h.__class__.__name__ for h in handlers]},
    )
    return root


def get_logger(name: str) -> logging.Logger:
    """Return a child logger with the ``keeper.`` prefix."""
    return logging.getLogger(f"keeper.{name}")
