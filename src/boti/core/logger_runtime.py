from __future__ import annotations

import atexit
import logging
import threading
from logging.handlers import QueueListener
from queue import Queue
from typing import Tuple


class LoggerRuntime:
    """Global queue/listener state shared by all Logger instances."""

    _lock = threading.RLock()
    _attached_keys: set[Tuple[str, str]] = set()
    _log_queue: Queue[logging.LogRecord | None] = Queue(-1)
    _listener: QueueListener | None = None

    @classmethod
    def ensure_listener(cls) -> None:
        if cls._listener is not None:
            return
        cls._listener = QueueListener(cls._log_queue, respect_handler_level=True)
        cls._listener.start()
        atexit.register(cls.stop_listener)

    @classmethod
    def add_destination(
        cls,
        key: Tuple[str, str],
        handler: logging.Handler,
        formatter: logging.Formatter,
    ) -> None:
        if key in cls._attached_keys:
            return

        logger_name, _ = key
        handler.addFilter(logging.Filter(name=logger_name))
        handler.setFormatter(formatter)

        if cls._listener:
            cls._listener.handlers = cls._listener.handlers + (handler,)

        cls._attached_keys.add(key)

    @classmethod
    def stop_listener(cls) -> None:
        if cls._listener:
            cls._log_queue.put_nowait(None)
            cls._listener.stop()
