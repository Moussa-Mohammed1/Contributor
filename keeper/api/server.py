"""Local read/control HTTP API for the daemon.

Implemented with stdlib asyncio so no additional dependencies are required.
Endpoints are JSON-only and bound to 127.0.0.1 by default. GUI, CLI and
external tooling can query live state through this API.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any
from urllib.parse import parse_qs, urlparse

from keeper.app import Application

logger = logging.getLogger(__name__)


def _json(body: Any, status: int = 200) -> bytes:
    data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
    headers = (
        f"HTTP/1.1 {status} {'OK' if status == 200 else 'Error'}\r\n"
        "Content-Type: application/json; charset=utf-8\r\n"
        f"Content-Length: {len(data)}\r\n"
        "Access-Control-Allow-Origin: *\r\n"
        "Connection: close\r\n\r\n"
    )
    return headers.encode("utf-8") + data


class ApiServer:
    """Async JSON server bound to localhost."""

    def __init__(self, app: Application, host: str = "127.0.0.1", port: int = 8971) -> None:
        self._app = app
        self._host = host
        self._port = port
        self._server: asyncio.AbstractServer | None = None
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------

    async def serve(self) -> None:
        server = await asyncio.start_server(self._handle, self._host, self._port)
        self._server = server
        logger.info("Local API listening on http://%s:%d", self._host, self._port)
        async with server:
            await self._stop.wait()

    def stop(self) -> None:
        if self._server is not None:
            self._server.close()

    # ------------------------------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            request_line = await reader.readline()
            if not request_line:
                writer.close()
                return
            parts = request_line.decode("utf-8", "replace").strip().split()
            if len(parts) < 3:
                writer.close()
                return
            method, path, _ = parts
            while True:
                line = await reader.readline()
                if line in (b"\r\n", b"\n", b""):
                    break
            body = await self._route(method, path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("API request failed: %s", exc)
            body = _json({"error": str(exc)}, 500)
        try:
            writer.write(body)
            await writer.drain()
        except (ConnectionError, OSError):
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass

    async def _route(self, method: str, path: str) -> bytes:
        parsed = urlparse(path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if method == "GET" and route == "/api/health":
            return _json({"ok": True, "service": "contributor"})
        if method == "GET" and route == "/api/status":
            return _json(await asyncio.to_thread(self._status))
        if method == "GET" and route == "/api/dashboard":
            return _json(await asyncio.to_thread(self._dashboard))
        if method == "GET" and route == "/api/repositories":
            return _json(await asyncio.to_thread(self._repositories))
        if method == "GET" and route == "/api/schedules":
            return _json(await asyncio.to_thread(self._schedules))
        if method == "GET" and route == "/api/stats":
            days = int((query.get("days") or ["30"])[0])
            return _json(await asyncio.to_thread(self._stats, days))
        if method == "GET" and route == "/api/logs":
            limit = int((query.get("limit") or ["200"])[0])
            level = (query.get("level") or [None])[0]
            return _json(await asyncio.to_thread(self._logs, limit, level))
        if method == "GET" and route == "/api/notifications":
            return _json(await asyncio.to_thread(self._notifications))
        if method == "GET" and route == "/api/config":
            return _json(await asyncio.to_thread(self._config))
        if method == "GET" and route == "/api/providers":
            return _json(await asyncio.to_thread(self._providers))
        if method == "GET" and route == "/api/executions":
            return _json(await asyncio.to_thread(self._executions))
        if method == "POST" and route == "/api/control/tick":
            result = await asyncio.to_thread(self._app.orchestrator.run_now)
            return _json(result)
        if method == "POST" and route == "/api/control/stop":
            try:
                self._app.config.stop_flag_path.write_text("1", encoding="utf-8")
            except OSError as exc:
                return _json({"error": str(exc)}, 500)
            return _json({"stopping": True})
        return _json({"error": f"no such route {method} {route}"}, 404)

    # ------------------------------------------------------------------
    # endpoint implementations (sync, run in worker threads)
    # ------------------------------------------------------------------

    def _status(self) -> dict:
        from keeper.workers import daemon

        return daemon.daemon_status()

    def _dashboard(self) -> dict:
        app = self._app
        with app.session() as session:
            today_stats = app.stats.today(session)
            repo_rows = app.repo_manager.snapshots(session, app.config)
            commits = dataset_last_commits(session)
            notifications = dataset_notifications(session)
        next_run = None
        if app.scheduler is not None:
            jobs = app.scheduler.running_jobs()
            next_run = jobs[0]["next_run"] if jobs else None
        return {
            "stats": today_stats,
            "repositories": [r.as_dict() for r in repo_rows],
            "last_commits": commits,
            "next_scheduler_run": next_run,
            "notifications": notifications[:10],
        }

    def _repositories(self) -> dict:
        with self._app.session() as session:
            rows = self._app.repo_manager.snapshots(session, self._app.config)
        return {"repositories": [r.as_dict() for r in rows]}

    def _schedules(self) -> dict:
        with self._app.session() as session:
            schedules = dataset_schedules(session)
        return {"schedules": schedules}

    def _stats(self, days: int) -> dict:
        with self._app.session() as session:
            return {
                "daily": self._app.stats.daily_series(session, days),
                "weekly": self._app.stats.weekly_series(session, 12),
                "monthly": self._app.stats.monthly_series(session, 12),
                "repos": self._app.stats.repo_activity(session, days),
                "ai": self._app.stats.ai_success_rate(session, days),
                "failures": self._app.stats.failure_rate(session, days),
                "execution_time": self._app.stats.execution_time(session, 7),
            }

    def _logs(self, limit: int, level: str | None) -> dict:
        from keeper.database import repo as dataset

        with self._app.session() as session:
            rows = dataset.list_logs(session, limit=min(limit, 2000), level=level)
        return {"logs": [{"ts": r.ts.isoformat(), "level": r.level, "logger": r.logger, "message": r.message}
                         for r in rows]}

    def _notifications(self) -> dict:
        with self._app.session() as session:
            rows = dataset_notifications(session)
        return {"notifications": rows}

    def _config(self) -> dict:
        cfg = self._app.config
        return {
            "path": str(self._app.config_manager.config_path),
            "data_dir": str(cfg.resolved_data_dir()),
            "schedule": cfg.schedule.model_dump(mode="json"),
            "repositories": [r.model_dump(mode="json") for r in cfg.repositories],
            "safety": cfg.safety.model_dump(mode="json"),
            "notifications": cfg.notifications.model_dump(mode="json"),
        }

    def _providers(self) -> dict:
        return self._app.providers.status()

    def _executions(self) -> dict:
        with self._app.session() as session:
            rows = dataset_executions(session)
        return {"executions": rows}


# -- tiny DAO shims used by the API (keeps the module self-contained) ------

def dataset_list(session) -> list:
    from keeper.database import repo as dataset

    return dataset.list_slots(session)


def dataset_schedules(session) -> list[dict]:
    from keeper.database import repo as dataset

    result = []
    for schedule in dataset.list_schedules(session, limit=30):
        result.append(
            {
                "id": schedule.id,
                "date": schedule.plan_date,
                "mode": schedule.mode,
                "target": schedule.target_commits,
                "status": schedule.status,
                "dry_run": schedule.dry_run,
            }
        )
    return result


def dataset_last_commits(session) -> list[dict]:
    from keeper.database import repo as dataset

    rows = []
    for record in dataset.list_commits(session, limit=20):
        rows.append(
            {
                "repo": record.repo_path,
                "hash": record.hash[:10],
                "message": record.message,
                "category": record.category,
                "pushed": record.pushed,
                "when": record.created_at.isoformat() if record.created_at else None,
            }
        )
    return rows


def dataset_notifications(session) -> list[dict]:
    from keeper.database import repo as dataset

    rows = []
    for record in dataset.list_notifications(session, limit=50):
        rows.append(
            {
                "title": record.title,
                "message": record.message,
                "level": record.level,
                "when": record.created_at.isoformat() if record.created_at else None,
            }
        )
    return rows


def dataset_executions(session) -> list[dict]:
    from keeper.database import repo as dataset

    rows = []
    for record in dataset.list_executions(session, limit=20):
        rows.append(
            {
                "kind": record.kind,
                "status": record.status,
                "started": record.started_at.isoformat() if record.started_at else None,
                "ended": record.ended_at.isoformat() if record.ended_at else None,
                "summary": record.summary,
            }
        )
    return rows


def start_api_thread(app: Application, host: str, port: int) -> threading.Thread:
    """Start the API in a daemon thread and return the thread object."""
    server = ApiServer(app, host=host, port=port)

    def run_loop() -> None:
        asyncio.run(server.serve())

    thread = threading.Thread(target=run_loop, name="contributor-api", daemon=True)
    thread.start()
    return thread


def api_serve_blocking(app: Application, host: str, port: int) -> None:
    """Blocking entry used by `keeper api`."""
    server = ApiServer(app, host=host, port=port)
    asyncio.run(server.serve())