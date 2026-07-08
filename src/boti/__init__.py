"""
Curated public API for the core Boti package.
"""

from __future__ import annotations

import importlib
from typing import Any

import boti.core  # noqa: F401  # Import submodule so boti.core namespace is resolvable

__all__ = [
    "Logger",
    "ManagedResource",
    "ProjectService",
    "SecureResource",
    "is_secure_path",
]


def __getattr__(name: str) -> Any:
    """Lazily import symbols from boti.core on first access."""
    mod = importlib.import_module("boti.core")
    return getattr(mod, name)
