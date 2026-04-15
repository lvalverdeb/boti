"""
Core infrastructure for the Boti ecosystem.

Includes foundational classes for lifecycle management, thread-safe logging,
and security sandboxing.
"""

from boti.core.managed_resource import ManagedResource
from boti.core.secure_io import SecureResource
from boti.core.logger import Logger
from boti.core.project import ProjectService
from boti.core.security import is_secure_path

__all__ = [
    "ManagedResource",
    "SecureResource",
    "Logger",
    "ProjectService",
    "is_secure_path",
]
