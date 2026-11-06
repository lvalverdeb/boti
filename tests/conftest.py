"""
Shared pytest fixtures for boti.core tests.
"""

import datetime
import gc
import shutil
import sqlite3
from pathlib import Path
from tempfile import mkdtemp

import pytest

from boti.core.logger_runtime import LoggerRuntime

# Python 3.12+ deprecated the implicit sqlite3 date/datetime adapters.
# Register explicit ISO-string adapters so SQLAlchemy tests using SQLite
# with datetime.date / datetime.datetime bind parameters don't emit warnings.
sqlite3.register_adapter(datetime.date, lambda d: d.isoformat())
sqlite3.register_adapter(datetime.datetime, lambda dt: dt.isoformat())


def _remove_tree_after_log_drain(path: Path) -> None:
    """Delete a temp dir that loggers may have written under, without racing them.

    Resources the test dropped without close() log their GC-leak warning from
    a finalizer, and the queue listener thread writes file records
    asynchronously. Deleting the directory while either is pending makes the
    listener's file handler fail with "--- Logging error ---" noise. Collect
    first so the finalizers fire now, then stop the listener, which drains the
    queue before returning. The next Logger created restarts it.
    """
    gc.collect()
    LoggerRuntime.stop_listener()
    shutil.rmtree(path)


@pytest.fixture
def temp_project_root():
    """Creates a temporary directory acting as a project root."""
    path = Path(mkdtemp())
    # Add a marker to ensure ProjectService detects it correctly
    (path / "pyproject.toml").touch()
    yield path
    _remove_tree_after_log_drain(path)


@pytest.fixture
def temp_log_dir():
    """Creates a temporary logging directory."""
    path = Path(mkdtemp())
    yield path
    _remove_tree_after_log_drain(path)
