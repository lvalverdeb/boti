"""
Logger example with secure defaults, PII redaction, and structured logging.

Demonstrates:
  - Logger instantiation with LoggerConfig
  - Basic logging at all levels
  - PII redaction (passwords, tokens, API keys in extra dicts)
  - default_logger() factory with LRU caching
  - Exception logging with exc_info
  - set_level() runtime control
  - Multiple named loggers
"""

from __future__ import annotations

import time
from pathlib import Path
from tempfile import TemporaryDirectory

from boti.core.logger import Logger
from boti.core.models import LoggerConfig


def example_basic_logging() -> None:
    """Log at all levels through a configured Logger."""
    with TemporaryDirectory() as tmp_dir:
        config = LoggerConfig(
            log_dir=Path(tmp_dir) / "logs",
            logger_name="examples.basic",
            debug=True,
        )
        logger = Logger(config)
        logger.set_level(Logger.DEBUG)

        logger.debug("debug message — appears because level=DEBUG")
        logger.info("info message")
        logger.warning("warning message")
        logger.error("error message")
        logger.critical("critical message")

        print(f"  Logged 5 messages at all levels → {config.log_dir}")
    print()


def example_pii_redaction() -> None:
    """Sensitive fields in extra dicts are automatically redacted."""
    with TemporaryDirectory() as tmp_dir:
        logger = Logger(LoggerConfig(
            log_dir=Path(tmp_dir) / "logs",
            logger_name="examples.pii",
            debug=True,
        ))
        logger.set_level(Logger.DEBUG)

        # These will have their sensitive keys redacted in the log output
        logger.info("user login", extra={
            "email": "alice@example.com",
            "password": "hunter2",
            "token": "eyJhbGciOiJIUzI1NiJ9.payload.sig",
        })

        logger.warning("API call", extra={
            "api_key": "sk-live-XXXXXXXXXXXXXXXX",
            "endpoint": "/v1/data",
            "status": 401,
        })

        logger.info("nested payload", extra={
            "user": {
                "profile": {"access_key": "AKIAIOSFODNN7EXAMPLE"},
                "preferences": {"theme": "dark"},
            },
            "session_id": "sess-abc",
        })

        print(f"  PII-redacted messages written to {Path(tmp_dir) / 'logs'}")
    print()


def example_default_logger() -> None:
    """Quick factory method with LRU-cached instances."""
    with TemporaryDirectory() as tmp_dir:
        # First call creates and caches a new Logger
        logger_a = Logger.default_logger(
            log_dir=Path(tmp_dir) / "logs",
            logger_name="examples.cache",
            base_dir=Path(tmp_dir),
        )
        logger_a.info("first message — cache miss")

        # Second call with same key returns the cached instance
        logger_b = Logger.default_logger(
            log_dir=Path(tmp_dir) / "logs",
            logger_name="examples.cache",
            base_dir=Path(tmp_dir),
        )
        print(f"  logger_a is logger_b: {logger_a is logger_b}")

        # Different name -> cache miss
        logger_c = Logger.default_logger(
            log_dir=Path(tmp_dir) / "logs",
            logger_name="examples.other",
            base_dir=Path(tmp_dir),
        )
        print(f"  logger_a is logger_c: {logger_a is logger_c}")
    print()


def example_exception_logging() -> None:
    """Capture exceptions with exc_info for structured traceback logging."""
    with TemporaryDirectory() as tmp_dir:
        logger = Logger(LoggerConfig(
            log_dir=Path(tmp_dir) / "logs",
            logger_name="examples.exc",
            debug=True,
        ))
        logger.set_level(Logger.DEBUG)

        try:
            _ = 1 / 0
        except ZeroDivisionError:
            logger.error("division failed", exc_info=True)

        print(f"  Exception logged to {Path(tmp_dir) / 'logs'}")
    print()


def example_log_level_runtime() -> None:
    """Change log level at runtime — messages below the threshold are suppressed."""
    with TemporaryDirectory() as tmp_dir:
        log_dir = Path(tmp_dir) / "logs"
        logger = Logger(LoggerConfig(
            log_dir=log_dir,
            logger_name="examples.level",
            log_level=Logger.WARNING,
        ))
        logger.set_level(Logger.WARNING)

        logger.info("this INFO is suppressed (WARNING threshold)")
        logger.warning("this WARNING is printed")
        logger.error("this ERROR is printed")

        # Raise threshold further
        logger.set_level(Logger.CRITICAL)
        logger.warning("this WARNING is now suppressed")
        logger.critical("this CRITICAL is printed")

        print(f"  Level-dependent messages written to {log_dir}")
    print()


def main() -> None:
    print("=== Logger examples ===\n")
    example_basic_logging()
    example_pii_redaction()
    example_default_logger()
    example_exception_logging()
    example_log_level_runtime()


if __name__ == "__main__":
    main()
