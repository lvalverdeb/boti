from __future__ import annotations

import atexit
import logging
import threading

__all__ = ["LoggerRuntime"]
from logging.handlers import QueueListener
from queue import Queue


# Must subclass QueueListener: the stdlib's _monitor thread calls
# self.handle(record) polymorphically, so dispatch can only be changed by
# overriding it here.
# spaghetti-ignore[lazy-class]: see above
class _NamedDispatchListener(QueueListener):
    """QueueListener that routes each record to the handlers registered for
    exactly ``record.name``.

    The stdlib listener offers every record to every handler, so cost grows
    with the number of loggers in the process and each handler needs its own
    name filter. Keying destinations by logger name makes dispatch a dict
    lookup and makes exact-name routing structural: a child logger's records
    cannot reach its parent's destinations because they are never looked up.
    """

    def __init__(
        self,
        queue: Queue[logging.LogRecord],
        destinations: dict[str, list[logging.Handler]],
        lock: threading.RLock,
    ) -> None:
        super().__init__(queue, respect_handler_level=True)
        self._destinations = destinations
        self._lock = lock

    def handle(self, record: logging.LogRecord) -> None:
        record = self.prepare(record)
        with self._lock:
            handlers = tuple(self._destinations.get(record.name, ()))
        for handler in handlers:
            if record.levelno >= handler.level:
                handler.handle(record)


class LoggerRuntime:
    """Global queue/listener state shared by all Logger instances.

    Destinations live here, keyed by logger name, not on the listener: the
    listener can be stopped and restarted (``stop_listener()`` /
    ``ensure_listener()``) without losing them.
    """

    lock = threading.RLock()
    _attached_keys: set[tuple[str, str]] = set()
    _destinations: dict[str, list[logging.Handler]] = {}
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
            cls._listener = _NamedDispatchListener(queue, cls._destinations, cls.lock)
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
        """Register *handler* for records from the logger named ``key[0]``.

        ``key[1]`` identifies the destination (a file path or a sentinel such
        as ``"__console__"``) so the same destination is not attached twice.
        """
        with cls.lock:
            if key in cls._attached_keys:
                return

            if cls._listener is None:
                raise RuntimeError(
                    "LoggerRuntime.ensure_listener() must be called before add_destination()."
                )

            logger_name, _ = key
            handler.setFormatter(formatter)
            cls._destinations.setdefault(logger_name, []).append(handler)
            cls._attached_keys.add(key)

    @classmethod
    def remove_destinations(cls, logger_name: str) -> int:
        """Detach and close every destination registered for *logger_name*.

        Records for that name still waiting in the queue are discarded. Returns
        the number of handlers closed.
        """
        with cls.lock:
            handlers = cls._destinations.pop(logger_name, [])
            cls._attached_keys = {k for k in cls._attached_keys if k[0] != logger_name}
        # Close outside the lock: a file handler's close() may flush to disk.
        for handler in handlers:
            handler.close()
        return len(handlers)

    @classmethod
    def stop_listener(cls) -> None:
        """Stop the background listener.  Safe to call multiple times.

        Destinations are kept, so a later ``ensure_listener()`` resumes
        delivery to the same files and streams.
        """
        with cls.lock:
            if cls._listener is None:
                return
            listener = cls._listener
            cls._listener = None
        # Stop outside the lock so handlers can flush without deadlocking.
        listener.stop()
