"""
Security and path validation utilities for the Boti Tools ecosystem.

This module provides essential tools for path sandboxing to prevent traversal attacks
and identifier validation for secure code generation.
"""

from __future__ import annotations
import re
from pathlib import Path
from typing import Iterable, Mapping, Union


def is_secure_path(target_file: Union[str, Path], allowed_dirs: Iterable[Union[str, Path]]) -> bool:
    """
    Verifies if a target path resides within an allowed sandbox directory.
    
    Uses path resolution to prevent traversal attacks and ensures the resolved
    path is relative to one of the trusted base directories.

    Args:
        target_file: The path to verify.
        allowed_dirs: A collection of directories where access is permitted.
            Usually includes the project root and specific temporary directories.

    Returns:
        bool: True if the path is secure and stays within the sandbox, False otherwise.
    """
    try:
        target_path = Path(target_file).resolve()
        for allowed in allowed_dirs:
            allowed_path = Path(allowed).resolve()
            # is_relative_to ensures target_path starts with allowed_path
            if target_path.is_relative_to(allowed_path):
                return True
    except (ValueError, RuntimeError, OSError):
        # Resolve errors or relative_to mismatch return False
        return False
    return False


def is_valid_identifier(name: str) -> bool:
    """
    Checks if a string is a valid Python identifier.
    
    This is used to prevent code injection when generating code dynamically
    or handling user-supplied names in templates.

    Args:
        name: The string to validate.

    Returns:
        bool: True if it is a valid Python identifier.
    """
    return bool(re.match(r'^[a-zA-Z_][a-zA-Z0-9_]*$', name))


def is_valid_dotted_identifier(name: str) -> bool:
    """
    Checks if a dotted string is a valid Python module-style identifier path.
    """
    if not name:
        return False
    parts = name.split(".")
    return all(is_valid_identifier(part) for part in parts)


_ENV_VAR_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_valid_env_var_name(name: str) -> bool:
    """Checks whether a string is a safe environment variable name."""
    return bool(_ENV_VAR_NAME_PATTERN.fullmatch(name))


def validate_environment_bindings(bindings: Mapping[str, str]) -> dict[str, str]:
    """Validate dotenv-style environment bindings before applying them to os.environ."""
    validated: dict[str, str] = {}
    for key, value in bindings.items():
        if "\x00" in key:
            raise ValueError("Environment variable names must not contain NUL bytes.")
        if not is_valid_env_var_name(key):
            raise ValueError(
                f"Invalid environment variable name '{key}'. Names must match [A-Za-z_][A-Za-z0-9_]*."
            )
        if "\x00" in value:
            raise ValueError(
                f"Environment variable '{key}' must not contain NUL bytes."
            )
        if "\n" in value or "\r" in value:
            raise ValueError(
                f"Environment variable '{key}' must not contain newline characters."
            )
        validated[key] = value
    return validated
