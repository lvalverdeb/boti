from __future__ import annotations

import atexit
import logging
import threading

__all__ = ["LoggerRuntime"]
from logging.handlers import QueueListener
from queue import Queue
from typing import Tuple


class LoggerRuntime:
    """Global queue/listener state shared by all Logger instances."""

    _lock = threading.RLock()
    _attached_keys: set[Tuple[str, str]] = set()
    _log_queue: Queue[logging.LogRecord] = Queue(-1)
    _listener: QueueListener | None = None
    _atexit_registered: bool = False

    @classmethod
    def ensure_listener(cls) -> None:
        if cls._listener is not None:
            return
        cls._listener = QueueListener(cls._log_queue, respect_handler_level=True)
        cls._listener.start()
        if not cls._atexit_registered:
            atexit.register(cls.stop_listener)
            cls._atexit_registered = True

    @classmethod
    def add_destination(
        cls,
        key: Tuple[str, str],
        handler: logging.Handler,
        formatter: logging.Formatter,
    ) -> None:
        if key in cls._attached_keys:
            return

        if cls._listener is None:
            raise RuntimeError(
                "LoggerRuntime.ensure_listener() must be called before add_destination()."
            )

        logger_name, _ = key
        handler.addFilter(logging.Filter(name=logger_name))
        handler.setFormatter(formatter)
        cls._listener.handlers = cls._listener.handlers + (handler,)
        cls._attached_keys.add(key)

    @classmethod
    def stop_listener(cls) -> None:
        """Stop the background listener.  Safe to call multiple times."""
        with cls._lock:
            if cls._listener is None:
                return
            listener = cls._listener
            cls._listener = None
        # Stop outside the lock so handlers can flush without deadlocking.
        listener.stop()
