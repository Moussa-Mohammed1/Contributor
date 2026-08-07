"""ConfigManager: lifecycle of the configuration (load, reload, save, watch).

Watchdog is used to hot-reload the configuration file while the daemon runs.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Callable

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

from keeper.config.loader import discover_config_path, load_config
from keeper.config.models import AppConfig
from keeper.core.exceptions import ConfigError

logger = logging.getLogger(__name__)

ConfigChangeCallback = Callable[[AppConfig], None]


class _ConfigFileHandler(FileSystemEventHandler):
    def __init__(self, manager: "ConfigManager") -> None:
        self._manager = manager
        self._debounce_timer: threading.Timer | None = None

    def on_modified(self, event: object) -> None:  # noqa: ARG002
        self._schedule_reload()

    def on_created(self, event: object) -> None:  # noqa: ARG002
        self._schedule_reload()

    def _schedule_reload(self) -> None:
        if self._debounce_timer and self._debounce_timer.is_alive():
            return
        self._debounce_timer = threading.Timer(1.0, self._manager.reload)
        self._debounce_timer.daemon = True
        self._debounce_timer.start()


class ConfigManager:
    """Loads, validates, persists and watches the configuration file."""

    def __init__(
        self,
        config_path: Path | None = None,
        *,
        watch: bool = True,
        on_change: ConfigChangeCallback | None = None,
    ) -> None:
        self._config_path: Path | None = None
        self._config: AppConfig | None = None
        self._watch = watch
        self._observer: Observer | None = None
        self._handler: _ConfigFileHandler | None = None
        self._callbacks: list[ConfigChangeCallback] = []
        if on_change is not None:
            self._callbacks.append(on_change)
        self._lock = threading.RLock()

    # -- lifecycle ---------------------------------------------------------

    def resolve_init_path(self, config_path: Path | None = None) -> Path:
        """Target config path for ``keeper init`` (the file may not exist yet)."""
        with self._lock:
            if config_path is not None:
                path = Path(config_path).expanduser().resolve()
            else:
                data_dir = self._default_data_dir()
                found = discover_config_path(Path.cwd(), data_dir)
                path = found if found is not None else data_dir / "config.yaml"
            self._config_path = path
            return path

    def load(self, config_path: Path | None = None) -> AppConfig:
        """Load configuration from an explicit path or via discovery."""
        with self._lock:
            if config_path is not None:
                path = Path(config_path).expanduser().resolve()
                self._config = load_config(path)
                self._config_path = path
            else:
                data_dir = self._default_data_dir()
                found = discover_config_path(Path.cwd(), data_dir)
                if found is None:
                    raise ConfigError(
                        "No configuration file found. Run `keeper init` first."
                    )
                self._config = load_config(found)
                self._config_path = found
            self._start_watching()
            return self._config

    def _default_data_dir(self) -> Path:
        if self._config is not None:
            return self._config.resolved_data_dir()
        return Path("~/.contributor").expanduser().resolve()

    @property
    def config(self) -> AppConfig:
        if self._config is None:
            raise ConfigError("Configuration not loaded. Call ConfigManager.load() first.")
        return self._config

    @property
    def config_path(self) -> Path:
        if self._config_path is None:
            raise ConfigError("Configuration path is unknown.")
        return self._config_path

    # -- reloading ---------------------------------------------------------

    def reload(self) -> AppConfig:
        """Reload configuration from disk and notify subscribers on success."""
        with self._lock:
            if self._config_path is None:
                raise ConfigError("Cannot reload: no configuration file known.")
            new_config = load_config(self._config_path)
            self._config = new_config
            logger.info("Configuration reloaded from %s", self._config_path)
        callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback(new_config)
            except Exception:  # noqa: BLE001 - one bad subscriber must not kill reload
                logger.exception("Config change callback failed")
        return new_config

    def save_text(self, text: str) -> Path:
        """Persist raw YAML text to the config file (atomic write)."""
        with self._lock:
            if self._config_path is None:
                raise ConfigError("Cannot save: no configuration file known.")
            path = self._config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".yaml.tmp")
        temp.write_text(text, encoding="utf-8")
        temp.replace(path)
        logger.info("Configuration written to %s", path)
        return path

    def subscribe(self, callback: ConfigChangeCallback) -> None:
        with self._lock:
            self._callbacks.append(callback)

    # -- watching ----------------------------------------------------------

    def _start_watching(self) -> None:
        if not self._watch or self._config_path is None or self._observer is not None:
            return
        directory = self._config_path.parent
        if not directory.exists():
            return
        try:
            self._handler = _ConfigFileHandler(self)
            self._observer = Observer()
            self._observer.daemon = True
            self._observer.schedule(self._handler, str(directory), recursive=False)
            self._observer.start()
            logger.debug("Watching config directory %s", directory)
        except Exception:  # noqa: BLE001
            logger.exception("Failed to start config watcher; hot reload disabled")
            self._observer = None

    def stop_watching(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=2)
            self._observer = None
