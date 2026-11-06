"""
Curated public API for the core Boti package.

``import boti`` is deliberately cheap: nothing under ``boti.core`` (and so
none of fsspec, pyarrow or the logging runtime) is loaded until one of the
names below — or ``boti.core`` itself — is first accessed.
"""

from __future__ import annotations

import importlib
from typing import Any

__all__ = [
    "Logger",
    "ManagedResource",
    "ProjectService",
    "SecureResource",
    "is_secure_path",
]


def __getattr__(name: str) -> Any:
    """Lazily import symbols from boti.core on first access."""
    if name == "core":
        return importlib.import_module("boti.core")
    if name not in __all__:
        raise AttributeError(f"module 'boti' has no attribute {name!r}")
    return getattr(importlib.import_module("boti.core"), name)
