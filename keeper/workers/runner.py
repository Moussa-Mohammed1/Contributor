"""Daemon runner: the always-on background process.

Entry point: ``python -m keeper.workers.runner [--config PATH] [--foreground]``

Startup sequence:
  1. load configuration (watch for hot reload),
  2. open the database and mirror logs into it,
  3. recover interrupted slots (crash recovery),
  4. refresh repository health,
  5. run today's due work immediately (catch-up after downtime),
  6. start APScheduler (daily tick + due tick),
  7. optionally start the local HTTP API,
  8. wait for a stop signal (SIGTERM / SIGINT / stop flag) then shut down
     gracefully.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="keeper-runner", description="contributor background daemon")
    parser.add_argument("--config", type=Path, default=None, help="Path to config.yaml")
    parser.add_argument("--foreground", action="store_true", help="Keep attached to the terminal")
    return parser.parse_args(argv)


class Runner:
    """Lifecycle of the background daemon process."""

    def __init__(self, config_path: Path | None, foreground: bool) -> None:
        from keeper.config.manager import ConfigManager

        self._config_manager = ConfigManager(config_path, watch=True)
        self._config_manager.load(config_path)
        self._foreground = foreground
        self._app = None
        self._stop_event = threading.Event()
        self._api_thread: threading.Thread | None = None
        self._installed_signals = False

    # ------------------------------------------------------------------

    def run(self) -> int:
        from keeper.app import build_application
        from keeper.logging.setup import setup_logging

        try:
            self._app = build_application(self._config_manager, start_scheduler=True)
        except Exception as exc:  # noqa: BLE001
            print(f"contributor failed to initialize: {exc}", file=sys.stderr)
            return 1

        app = self._app
        setup_logging(
            level=app.config.logging.level,
            structured=app.config.logging.structured,
            log_dir=app.config.log_dir,
            db_engine=app.database.engine if app.database else None,
            console=self._foreground,
        )
        logger.info("contributor daemon starting (foreground=%s)", self._foreground)

        # 1. Crash recovery: reset interrupted slots, recover landed commits.
        with app.session() as session:
            app.tracker.recover_interrupted(session)

        # 2. Retention prune for high-volume audit tables.
        try:
            pruned = app.database.prune(app.config.logging.retention_days)
            logger.info("Retention prune: %s", pruned)
        except Exception:  # noqa: BLE001
            logger.warning("Retention prune failed", exc_info=True)

        # 3. Health refresh.
        with app.session() as session:
            app.repo_manager.check_health(session, app.config)

        # 4. Catch-up run (covers restarts while the PC was off).
        result = app.orchestrator.run_now()
        logger.info("Startup run result: %s", result)

        # 5. Scheduler.
        if app.scheduler is not None:
            app.scheduler.start()
        else:
            logger.warning("Scheduler not available; daemon will only run the catch-up step")

        # 6. Optional local API.
        if app.config.api.enabled:
            self._start_api(app)

        # 7. PID file + signal/flag handling.
        self._write_pid()
        self._install_signal_handlers()
        self._watch_stop_flag(app.config.stop_flag_path)

        logger.info("Daemon ready. PID=%d. Stop with `keeper stop`.", os.getpid())
        self._stop_event.wait()
        self._shutdown()
        return 0

    # ------------------------------------------------------------------

    def _start_api(self, app) -> None:
        try:
            from keeper.api.server import start_api_thread

            self._api_thread = start_api_thread(app, host=app.config.api.host, port=app.config.api.port)
        except Exception:  # noqa: BLE001
            logger.warning("Local API failed to start", exc_info=True)

    def _write_pid(self) -> None:
        try:
            self._config_manager.config.pid_path.write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            logger.warning("Could not write PID file")

    def _install_signal_handlers(self) -> None:
        if self._installed_signals:
            return
        self._installed_signals = True

        def handle(signum, frame) -> None:  # noqa: ARG001
            logger.info("Received signal %s; shutting down", signum)
            self._stop_event.set()

        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                signal.signal(sig, handle)
            except (ValueError, OSError):
                pass

    def _watch_stop_flag(self, path: Path) -> None:
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass

        def poll() -> None:
            while not self._stop_event.is_set():
                if path.exists():
                    logger.info("Stop flag detected; shutting down")
                    self._stop_event.set()
                    return
                self._stop_event.wait(2.0)

        thread = threading.Thread(target=poll, name="stop-flag-watcher", daemon=True)
        thread.start()

    def _shutdown(self) -> None:
        logger.info("Shutting down daemon")
        try:
            self._config_manager.config.stop_flag_path.unlink(missing_ok=True)
            self._config_manager.config.pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        if self._app is not None:
            try:
                self._app.close()
            except Exception:  # noqa: BLE001
                logger.exception("Error during application shutdown")
        logger.info("Daemon stopped cleanly")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    runner = Runner(args.config, args.foreground)
    return runner.run()


if __name__ == "__main__":
    raise SystemExit(main())