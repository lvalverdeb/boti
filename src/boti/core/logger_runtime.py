from __future__ import annotations

import atexit
import logging
import threading

__all__ = ["LoggerRuntime"]
from logging.handlers import QueueListener
from queue import Queue


# Must subclass logging.Filter: Handler.addFilter accepts callables too, but
# mypy can't type a lambda here and a named class documents the intent.
# spaghetti-ignore[lazy-class]: see above
class _ExactNameFilter(logging.Filter):
    """Pass only records from the logger named exactly *name* — not its children."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.name == self.name


class LoggerRuntime:
    """Global queue/listener state shared by all Logger instances."""

    lock = threading.RLock()
    _attached_keys: set[tuple[str, str]] = set()
    _log_queue: Queue[logging.LogRecord] | None = None
    _listener: QueueListener | None = None
    _atexit_registered: bool = False

    @classmethod
    def ensure_queue(cls) -> Queue[logging.LogRecord]:
        if cls._log_queue is None:
            cls._log_queue = Queue(-1)
        return cls._log_queue

    @classmethod
    def ensure_listener(cls) -> None:
        with cls.lock:
            if cls._listener is not None:
                return
            queue = cls.ensure_queue()
            cls._listener = QueueListener(queue, respect_handler_level=True)
            cls._listener.start()
            if not cls._atexit_registered:
                atexit.register(cls.stop_listener)
                cls._atexit_registered = True

    @classmethod
    def add_destination(
        cls,
        key: tuple[str, str],
        handler: logging.Handler,
        formatter: logging.Formatter,
    ) -> None:
        with cls.lock:
            if key in cls._attached_keys:
                return

            if cls._listener is None:
                raise RuntimeError(
                    "LoggerRuntime.ensure_listener() must be called before add_destination()."
                )

            logger_name, _ = key
            # Exact-name match only. A plain ``logging.Filter(name=...)`` also
            # accepts child loggers (``app`` matches ``app.child``), which would
            # send a child's records to its parent's file and console as well
            # as its own — a duplicate console line per ancestor.
            handler.addFilter(_ExactNameFilter(logger_name))
            handler.setFormatter(formatter)
            cls._listener.handlers = cls._listener.handlers + (handler,)
            cls._attached_keys.add(key)

    @classmethod
    def stop_listener(cls) -> None:
        """Stop the background listener.  Safe to call multiple times."""
        with cls.lock:
            if cls._listener is None:
                return
            listener = cls._listener
            cls._listener = None
        # Stop outside the lock so handlers can flush without deadlocking.
        listener.stop()
