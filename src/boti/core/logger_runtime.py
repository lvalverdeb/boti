from __future__ import annotations

import atexit
import logging
import threading

__all__ = ["LoggerRuntime"]
from logging.handlers import QueueListener
from queue import Queue

# A sink identifies one destination set: (logger_name, discriminator). boti
# Loggers use their log directory as the discriminator and stamp every record
# with its sink (see Logger._log), so two Loggers sharing a stdlib logger name
# but writing to different directories never receive each other's records.
# Records from raw stdlib loggers carry no stamp and fall back to
# (record.name, "") — the sink add_destination() registers when none is given.
Sink = tuple[str, str]
SINK_ATTR = "_boti_sink"


# Must subclass QueueListener: the stdlib's _monitor thread calls
# self.handle(record) polymorphically, so dispatch can only be changed by
# overriding it here.
# spaghetti-ignore[lazy-class]: see above
class _NamedDispatchListener(QueueListener):
    """QueueListener that routes each record to the handlers registered for
    exactly its sink.

    The stdlib listener offers every record to every handler, so cost grows
    with the number of loggers in the process and each handler needs its own
    name filter. Keying destinations by sink makes dispatch a dict lookup and
    makes exact routing structural: a child logger's records cannot reach its
    parent's destinations, and two Loggers with one name but different log
    directories cannot reach each other's files, because neither is ever
    looked up.
    """

    def __init__(
        self,
        queue: Queue[logging.LogRecord],
        destinations: dict[Sink, list[logging.Handler]],
        lock: threading.RLock,
    ) -> None:
        super().__init__(queue, respect_handler_level=True)
        self._destinations = destinations
        self._lock = lock

    def handle(self, record: logging.LogRecord) -> None:
        record = self.prepare(record)
        sink: Sink = record.__dict__.get(SINK_ATTR, (record.name, ""))
        with self._lock:
            handlers = tuple(self._destinations.get(sink, ()))
        for handler in handlers:
            if record.levelno >= handler.level:
                handler.handle(record)


class LoggerRuntime:
    """Global queue/listener state shared by all Logger instances.

    Destinations live here, keyed by sink, not on the listener: the listener
    can be stopped and restarted (``stop_listener()`` / ``ensure_listener()``)
    without losing them.
    """

    lock = threading.RLock()
    _attached_keys: set[tuple[str, str]] = set()
    _destinations: dict[Sink, list[logging.Handler]] = {}
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
        *,
        sink: Sink | None = None,
    ) -> None:
        """Register *handler* for records routed to *sink*.

        ``key`` is ``(logger_name, destination_id)``; the destination id (a
        file path or a sentinel such as ``"__console__"``) stops the same
        destination being attached twice. *sink* defaults to
        ``(logger_name, "")``, which is where records from a plain
        ``logging.getLogger(logger_name)`` are routed.
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
            cls._destinations.setdefault(sink or (logger_name, ""), []).append(handler)
            cls._attached_keys.add(key)

    @classmethod
    def remove_destinations(cls, sink: Sink, keys: set[tuple[str, str]]) -> int:
        """Detach and close every destination registered for *sink*.

        *keys* are the ``add_destination()`` keys that were used for it, so
        they can be attached again later. Records for that sink still waiting
        in the queue are discarded. Returns the number of handlers closed.
        """
        with cls.lock:
            handlers = cls._destinations.pop(sink, [])
            cls._attached_keys -= keys
        # Close outside the lock: a file handler's close() may flush to disk.
        for handler in handlers:
            handler.close()
        return len(handlers)

    @classmethod
    def has_sinks_for(cls, logger_name: str) -> bool:
        """Whether any registered sink still belongs to *logger_name*."""
        with cls.lock:
            return any(name == logger_name for name, _ in cls._destinations)

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
