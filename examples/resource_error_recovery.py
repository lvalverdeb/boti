"""
ManagedResource cleanup-failure recovery semantics.

Demonstrates:
  - __exit__ suppressing a cleanup failure when a user exception is already
    propagating, so the original exception is not masked
  - __exit__ letting a cleanup failure propagate normally when no user
    exception occurred
  - The same contrast via explicit close(suppress_errors=...)
  - Cleanup failures being logged even when suppressed
"""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from boti.core import ManagedResource
from boti.core.logger import Logger
from boti.core.logger_runtime import LoggerRuntime
from boti.core.models import LoggerConfig, ResourceConfig


class BrokenResource(ManagedResource):
    """A resource whose cleanup always fails, to exercise error-recovery paths."""

    def __init__(self, label: str = "default", **kwargs) -> None:
        super().__init__(**kwargs)
        self.label = label

    def _cleanup(self) -> None:
        raise RuntimeError("cleanup exploded")


def example_exception_during_body_suppresses_cleanup_error() -> None:
    """A user exception in the `with` body wins; the cleanup failure is swallowed."""
    resource = BrokenResource(label="during-exception")
    try:
        with resource:
            raise ValueError("business logic error")
    except ValueError as exc:
        print(f"  original exception propagated: {exc!r}")
    except RuntimeError:
        print("  (unexpected: cleanup error leaked over the original exception)")

    print(f"  resource closed despite cleanup failure: {resource.closed}")
    print()


def example_clean_body_propagates_cleanup_error() -> None:
    """With no user exception, a cleanup failure surfaces normally."""
    resource = BrokenResource(label="clean-body")
    try:
        with resource:
            pass  # body succeeds
    except RuntimeError as exc:
        print(f"  cleanup error propagated: {exc!r}")

    print(f"  resource closed even though cleanup raised: {resource.closed}")
    print()


def example_explicit_close_suppress_errors() -> None:
    """close(suppress_errors=...) gives the same choice outside a context manager."""
    quiet = BrokenResource(label="quiet-close")
    quiet.close(suppress_errors=True)
    print(f"  suppress_errors=True: closed={quiet.closed}, no exception raised")

    loud = BrokenResource(label="loud-close")
    try:
        loud.close(suppress_errors=False)
    except RuntimeError as exc:
        print(f"  suppress_errors=False: raised {exc!r}, closed={loud.closed}")
    print()


def example_cleanup_failure_is_logged() -> None:
    """Even when suppressed, the cleanup failure is written to the resource's logger."""
    with TemporaryDirectory() as tmp_dir:
        log_dir = Path(tmp_dir) / "logs"
        logger = Logger(LoggerConfig(log_dir=log_dir, logger_name="examples.recovery", debug=True))

        resource = BrokenResource(
            label="logged-failure",
            config=ResourceConfig(logger=logger),
        )
        resource.close(suppress_errors=True)
        LoggerRuntime.stop_listener()  # drain the async queue before reading the file back

        log_file = log_dir / "examples.recovery.log"
        contents = log_file.read_text() if log_file.exists() else ""
        found = "Error during BrokenResource._cleanup()" in contents
        print(f"  cleanup failure recorded in log file: {found}")
    print()


def main() -> None:
    print("=== ManagedResource error-recovery examples ===\n")
    example_exception_during_body_suppresses_cleanup_error()
    example_clean_body_propagates_cleanup_error()
    example_explicit_close_suppress_errors()
    example_cleanup_failure_is_logged()


if __name__ == "__main__":
    main()
