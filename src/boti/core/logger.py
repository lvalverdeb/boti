"""
Standardized logging engine for Boti Tools.

Provides a thread-safe, non-blocking Logger with structured formatting,
PII redaction, and optional asynchronous queue-based handlers.
"""

from __future__ import annotations
import logging
import os
import sys
import threading
import warnings
from collections import OrderedDict
from logging.handlers import QueueHandler
from pathlib import Path
from typing import Any, Optional, Union


from boti.core.logger_filters import PIISecretFilter
from boti.core.logger_handlers import SafeRotatingFileHandler
from boti.core.logger_runtime import LoggerRuntime
from boti.core.models import LoggerConfig
from boti.core.project import ProjectService


class Logger:
    """
    Thread-safe, non-blocking Logger designed for high-performance toolkits.
    """

    DEFAULT_LOGGER_NAME = "boti"
    # Bounded LRU cache: key = (log_dir, logger_name, log_file, log_level).
    # Including log_level prevents a cached instance silently ignoring level changes.
    # Capped at 256 entries to avoid unbounded file-descriptor growth in long-running apps.
    _MAX_CACHE_SIZE = 256
    _default_logger_cache: OrderedDict[tuple[Path, str, str, int], "Logger"] = OrderedDict()
    _default_logger_lock = threading.RLock()

    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL

    def __init__(self, config: LoggerConfig) -> None:
        """
        Initialize the Logger instance.

        Args:
            config: LoggerConfig model containing initialization parameters.
        """
        self.config = config
        self.log_dir = config.log_dir
        self.logger_name = config.logger_name
        self.log_file = config.log_file or config.logger_name
        self.log_level = config.log_level

        self._core = logging.getLogger(self.logger_name)
        self._core.setLevel(self.log_level)
        self._core.propagate = False

        self._setup_handlers()

    @classmethod
    def default_logger(
        cls,
        log_dir: Union[str, Path] = "logs",
        logger_name: Optional[str] = None,
        log_file: Optional[str] = None,
        log_level: int = logging.INFO,
        base_dir: Optional[Union[str, Path]] = None,
    ) -> Logger:
        """
        Factory method for quick instantiation with sensible defaults.
        """
        if logger_name is None:
            try:
                # Use caller's module name
                caller_frame = sys._getframe(1)
                logger_name = caller_frame.f_globals.get("__name__", cls.DEFAULT_LOGGER_NAME)
            except (ValueError, AttributeError):
                logger_name = cls.DEFAULT_LOGGER_NAME

        resolved_log_dir = cls._resolve_log_dir(log_dir, base_dir=base_dir)
        effective_log_file = log_file or logger_name
        # log_level is part of the key so two callers requesting different levels
        # get distinct instances rather than the second silently overriding the first.
        cache_key = (resolved_log_dir, logger_name, effective_log_file, log_level)

        with cls._default_logger_lock:
            logger = cls._default_logger_cache.get(cache_key)
            if logger is None:
                if len(cls._default_logger_cache) >= cls._MAX_CACHE_SIZE:
                    # Evict the least-recently-used entry (first in insertion order).
                    cls._default_logger_cache.popitem(last=False)
                config = LoggerConfig(
                    log_dir=resolved_log_dir,
                    logger_name=logger_name,
                    log_file=log_file,
                    log_level=log_level
                )
                logger = cls(config)
                cls._default_logger_cache[cache_key] = logger
            else:
                # Move to end to mark as most-recently-used.
                cls._default_logger_cache.move_to_end(cache_key)
            return logger

    @staticmethod
    def _resolve_log_dir(
        log_dir: Union[str, Path],
        *,
        base_dir: Optional[Union[str, Path]] = None,
    ) -> Path:
        path = Path(log_dir).expanduser()
        if path.is_absolute():
            return path.resolve()

        anchor = Path(base_dir).resolve() if base_dir is not None else ProjectService.detect_project_root()
        return (anchor / path).resolve()

    def set_level(self, level: int) -> None:
        """Update the logging level."""
        self._core.setLevel(level)

    def debug(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._core.log(logging.DEBUG, msg, *args, **kwargs)

    def info(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._core.log(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._core.log(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._core.log(logging.ERROR, msg, *args, **kwargs)

    def critical(self, msg: str, *args: Any, **kwargs: Any) -> None:
        self._core.log(logging.CRITICAL, msg, *args, **kwargs)

    def _setup_handlers(self) -> None:
        """
        Configures non-blocking log handling via a QueueListener.

        Symlink-targeted log directories are rejected as a hard security error.
        If the directory cannot be created for any other reason (e.g. permission
        denied) the logger falls back to stderr-only output and emits a warning,
        so the application can still start rather than failing at logger init.
        """
        if self.log_dir.is_symlink():
            raise ValueError(f"log_dir must not be a symlink: {self.log_dir}")

        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            warnings.warn(
                f"Logger '{self.logger_name}' could not create log directory "
                f"'{self.log_dir}': {exc}. Falling back to stderr only.",
                RuntimeWarning,
                stacklevel=3,
            )
            self._setup_stderr_only_handler()
            return

        self._restrict_permissions(self.log_dir, 0o700)

        log_file_path = self.log_dir / f"{self.log_file}.log"
        self._ensure_secure_log_file(log_file_path)
        file_key = (self.logger_name, str(log_file_path.resolve()))
        console_key = (self.logger_name, "__console__")

        fmt = logging.Formatter(
            "[%(asctime)s][%(levelname)s][%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        with LoggerRuntime._lock:
            LoggerRuntime.ensure_listener()

            # Attach QueueHandler to this logger
            if not any(isinstance(h, QueueHandler) for h in self._core.handlers):
                qh = QueueHandler(LoggerRuntime._log_queue)
                qh.addFilter(PIISecretFilter())
                self._core.addHandler(qh)

            # Add destinations to the global listener
            LoggerRuntime.add_destination(file_key, SafeRotatingFileHandler(
                log_file_path, maxBytes=5 * 1024 * 1024, backupCount=5, delay=True
            ), fmt)

            LoggerRuntime.add_destination(console_key, logging.StreamHandler(sys.stdout), fmt)

    def _setup_stderr_only_handler(self) -> None:
        """Attach a simple stderr handler when file logging is unavailable."""
        fmt = logging.Formatter(
            "[%(asctime)s][%(levelname)s][%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        with LoggerRuntime._lock:
            LoggerRuntime.ensure_listener()
            if not any(isinstance(h, QueueHandler) for h in self._core.handlers):
                qh = QueueHandler(LoggerRuntime._log_queue)
                qh.addFilter(PIISecretFilter())
                self._core.addHandler(qh)
            console_key = (self.logger_name, "__console__")
            LoggerRuntime.add_destination(console_key, logging.StreamHandler(sys.stderr), fmt)

    @staticmethod
    def _restrict_permissions(path: Path, mode: int) -> None:
        if os.name == "posix":
            path.chmod(mode)

    @classmethod
    def _ensure_secure_log_file(cls, path: Path) -> None:
        try:
            # Atomically attempt to create the file exclusively without following symlinks
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600)
            os.close(fd)
        except FileExistsError:
            # File exists, check if it's securely structured and not a planted symlink.
            if path.is_symlink():
                raise ValueError(f"log_file must not be a symlink: {path}")
            cls._restrict_permissions(path, 0o600)
