"""
Logger.bind() for request/task-scoped structured logging.

Demonstrates:
  - bind() returning a new Logger copy with merged extra context
  - Context propagating to every subsequent log call automatically
  - Chained bind() calls accumulating fields
  - Isolation: binding on a child logger never mutates the parent
  - Bound context still passes through PII redaction
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from boti.core.logger import Logger
from boti.core.models import LoggerConfig


def example_basic_bind() -> None:
    """A bound logger automatically attaches its context to every call."""
    with TemporaryDirectory() as tmp_dir:
        logger = Logger(LoggerConfig(
            log_dir=Path(tmp_dir) / "logs",
            logger_name="examples.bind.basic",
            debug=True,
        ))
        logger.set_level(Logger.DEBUG)

        request_logger = logger.bind(request_id="req-42", user="alice")
        request_logger.info("request started")
        request_logger.info("request finished", extra={"status": 200})

        print("  request_logger auto-attaches request_id/user to every call above")
    print()


def example_chained_bind() -> None:
    """Successive bind() calls accumulate fields rather than replacing them."""
    with TemporaryDirectory() as tmp_dir:
        logger = Logger(LoggerConfig(
            log_dir=Path(tmp_dir) / "logs",
            logger_name="examples.bind.chained",
            debug=True,
        ))
        logger.set_level(Logger.DEBUG)

        job_logger = logger.bind(job_id="job-1")
        task_logger = job_logger.bind(task_id="task-7")
        task_logger.info("task running")

        print(f"  job_logger._extra:  {job_logger._extra}")
        print(f"  task_logger._extra: {task_logger._extra} (job_id carried over)")
    print()


def example_bind_isolation() -> None:
    """bind() returns a copy — the parent logger's context is untouched."""
    with TemporaryDirectory() as tmp_dir:
        logger = Logger(LoggerConfig(
            log_dir=Path(tmp_dir) / "logs",
            logger_name="examples.bind.isolation",
            debug=True,
        ))
        logger.set_level(Logger.DEBUG)

        child = logger.bind(trace_id="trace-99")
        logger.info("parent log — no trace_id attached")
        child.info("child log — trace_id attached")

        print(f"  logger._extra: {logger._extra} (unchanged)")
        print(f"  child._extra:  {child._extra}")
    print()


def example_bind_with_pii_redaction() -> None:
    """Bound context is subject to the same PII redaction as any extra dict."""
    with TemporaryDirectory() as tmp_dir:
        logger = Logger(LoggerConfig(
            log_dir=Path(tmp_dir) / "logs",
            logger_name="examples.bind.pii",
            debug=True,
        ))
        logger.set_level(Logger.DEBUG)

        session_logger = logger.bind(session_id="sess-1", api_key="sk-live-XXXXXXXX")
        session_logger.warning("suspicious request")

        print("  bound api_key is redacted in the log file, same as a per-call extra dict")
    print()


def main() -> None:
    print("=== Logger.bind() examples ===\n")
    example_basic_bind()
    example_chained_bind()
    example_bind_isolation()
    example_bind_with_pii_redaction()


if __name__ == "__main__":
    main()
