"""
LoggerRuntime example: global queue listener with custom destinations.

Demonstrates:
  - LoggerRuntime.ensure_listener() to start the background queue listener
  - LoggerRuntime.add_destination() to register custom handlers
  - SafeRotatingFileHandler for symlink-safe log rotation
  - PIISecretFilter standalone usage on log records
  - LoggerRuntime.stop_listener() for graceful shutdown
  - Multiple destinations (file + stderr) with different formatters
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from boti.core.logger_filters import PIISecretFilter
from boti.core.logger_handlers import SafeRotatingFileHandler
from boti.core.logger_runtime import LoggerRuntime


def example_listener_lifecycle() -> None:
    """Start and stop the global listener (idempotent)."""
    LoggerRuntime.ensure_listener()
    print(f"  listener started: {LoggerRuntime._listener is not None}")

    # Second call is a no-op
    LoggerRuntime.ensure_listener()
    print(f"  listener still running: {LoggerRuntime._listener is not None}")

    LoggerRuntime.stop_listener()
    print(f"  listener stopped: {LoggerRuntime._listener is None}")
    print()


def example_custom_destination() -> None:
    """Register a custom SafeRotatingFileHandler with PII redaction."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        LoggerRuntime.ensure_listener()

        log_path = Path(tmp_dir) / "custom.log"
        handler = SafeRotatingFileHandler(
            log_path,
            maxBytes=1024 * 1024,
            backupCount=3,
            delay=True,
        )
        handler.addFilter(PIISecretFilter())

        formatter = logging.Formatter(
            "[%(asctime)s][%(levelname)s][%(name)s] %(message)s",
            datefmt="%H:%M:%S",
        )

        LoggerRuntime.add_destination(
            ("custom_app", str(log_path)),
            handler,
            formatter,
        )

        # Emit a log record through a standard logger
        logger = logging.getLogger("custom_app")
        logger.setLevel(logging.DEBUG)
        logger.addHandler(logging.handlers.QueueHandler(LoggerRuntime._log_queue))
        logger.info("hello from custom_app")
        logger.warning("sensitive data", extra={"token": "super-secret"})

        LoggerRuntime.stop_listener()

        print(f"  Log written to {log_path}")
        print("  Contents:")
        for line in log_path.read_text().splitlines():
            print(f"    {line}")
    print()


def example_multiple_destinations() -> None:
    """Same logger writing to both a file and stderr with different formats."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        LoggerRuntime.ensure_listener()

        # File handler with detailed format
        file_path = Path(tmp_dir) / "detailed.log"
        file_handler = SafeRotatingFileHandler(file_path, delay=True)
        file_handler.addFilter(PIISecretFilter())
        file_fmt = logging.Formatter(
            "[%(asctime)s][%(levelname)s][%(name)s] %(message)s",
        )

        # Stderr handler with short format
        stderr_handler = logging.StreamHandler()
        stderr_handler.addFilter(PIISecretFilter())
        stderr_fmt = logging.Formatter("%(levelname)s: %(message)s")

        LoggerRuntime.add_destination(
            ("multi", str(file_path)),
            file_handler,
            file_fmt,
        )
        LoggerRuntime.add_destination(
            ("multi", "__stderr__"),
            stderr_handler,
            stderr_fmt,
        )

        logger = logging.getLogger("multi")
        logger.setLevel(logging.INFO)
        logger.addHandler(logging.handlers.QueueHandler(LoggerRuntime._log_queue))
        logger.info("this goes to both file and stderr")

        LoggerRuntime.stop_listener()
        print(f"  Multi-destination log written to {file_path}")
    print()


def main() -> None:
    print("=== LoggerRuntime examples ===\n")
    example_listener_lifecycle()
    example_custom_destination()
    example_multiple_destinations()


if __name__ == "__main__":
    main()
