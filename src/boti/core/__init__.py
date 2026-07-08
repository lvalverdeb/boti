"""
Core infrastructure for the Boti ecosystem.

Includes foundational classes for lifecycle management, thread-safe logging,
security sandboxing, filesystem adapters, settings helpers, and shared models.
"""

__all__ = [
    "FilesystemAdapter",
    "FilesystemConfig",
    "Logger",
    "ManagedResource",
    "ProjectService",
    "ResourceConfig",
    "SecureResource",
    "SqlDatabaseSettings",
    "add_endpoint_to_allowlist",
    "create_filesystem",
    "is_secure_path",
    "is_valid_dotted_identifier",
    "is_valid_env_var_name",
    "is_valid_identifier",
    "load_prefixed_model",
]

from boti.core.filesystem import (
    FilesystemAdapter,
    FilesystemConfig,
    add_endpoint_to_allowlist,
    create_filesystem,
)
from boti.core.logger import Logger
from boti.core.managed_resource import ManagedResource
from boti.core.models import ResourceConfig
from boti.core.project import ProjectService
from boti.core.secure_io import SecureResource
from boti.core.security import (
    is_secure_path,
    is_valid_dotted_identifier,
    is_valid_env_var_name,
    is_valid_identifier,
)
from boti.core.settings import (
    SqlDatabaseSettings,
    load_prefixed_model,
)
