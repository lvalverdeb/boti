"""
Extended security examples: environment bindings, identifier validation.

Demonstrates:
  - validate_environment_bindings() — reject NUL bytes, newlines, tabs
  - is_valid_env_var_name() — safe env var name checking
  - is_valid_identifier() — Python identifier validation
  - is_valid_dotted_identifier() — dotted module path validation
  - is_secure_path() — sandbox path verification with symlink resolution
  - Edge cases and rejection patterns
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from boti.core import is_secure_path
from boti.core.security import (
    is_valid_dotted_identifier,
    is_valid_env_var_name,
    is_valid_identifier,
    validate_environment_bindings,
)


def example_validate_env_var_name() -> None:
    """Check which strings are safe environment variable names."""
    cases = [
        ("MY_VAR", True),
        ("_secret", True),
        ("var1", True),
        ("1var", False),       # starts with digit
        ("my-var", False),     # hyphen not allowed
        ("my var", False),     # space not allowed
        ("", False),           # empty
    ]
    for name, expected in cases:
        result = is_valid_env_var_name(name)
        status = "ok" if result == expected else "UNEXPECTED"
        print(f"  [{status}] is_valid_env_var_name({name!r:12s}) = {result}")
    print()


def example_validate_bindings() -> None:
    """Reject malicious or malformed environment bindings."""
    valid = {"MY_VAR": "value", "COUNT": "42"}
    print(f"  valid bindings: {validate_environment_bindings(valid)}")

    try:
        validate_environment_bindings({"BAD\nKEY": "value"})
    except ValueError as exc:
        print(f"  newline in key rejected: {exc}")

    try:
        validate_environment_bindings({"KEY": "value\x00more"})
    except ValueError as exc:
        print(f"  NUL byte in value rejected: {exc}")

    try:
        validate_environment_bindings({"KEY": "line1\nline2"})
    except ValueError as exc:
        print(f"  newline in value rejected: {exc}")

    try:
        validate_environment_bindings({"KEY": "tab\there"})
    except ValueError as exc:
        print(f"  tab in value rejected: {exc}")
    print()


def example_identifiers() -> None:
    """Validate Python identifiers and dotted module paths."""
    identifiers = [
        ("valid_name", True),
        ("_private", True),
        ("SomeClass", True),
        ("has spaces", False),
        ("123abc", False),
        ("class", True),  # Python keyword IS technically a valid identifier
    ]
    for name, expected in identifiers:
        result = is_valid_identifier(name)
        status = "ok" if result == expected else "UNEXPECTED"
        print(f"  [{status}] is_valid_identifier({name!r:16s}) = {result}")

    dotted = [
        ("boti.core.ManagedResource", True),
        ("a.b.c", True),
        ("single", True),
        ("", False),
        ("leading..dot", False),
        (".leading", False),
        ("trailing.", False),
        ("has. spaces.name", False),
    ]
    for name, expected in dotted:
        result = is_valid_dotted_identifier(name)
        status = "ok" if result == expected else "UNEXPECTED"
        print(f"  [{status}] is_valid_dotted_identifier({name!r:32s}) = {result}")
    print()


def example_secure_path() -> None:
    """Verify path sandboxing with symlink resolution."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        sandbox = Path(tmp_dir) / "sandbox"
        sandbox.mkdir()
        safe_file = sandbox / "data.txt"
        safe_file.write_text("safe")

        outside_file = Path(tmp_dir) / "secret.txt"
        outside_file.write_text("secret")

        allowed_dirs = [sandbox]

        # Inside sandbox — allowed
        print(f"  safe path: {is_secure_path(safe_file, allowed_dirs)}")

        # Outside sandbox — rejected
        print(f"  outside path: {is_secure_path(outside_file, allowed_dirs)}")

        # Path traversal attempt — rejected
        traversal = sandbox / ".." / "secret.txt"
        print(f"  traversal: {is_secure_path(traversal, allowed_dirs)}")

        # Non-existent path within sandbox — allowed (resolve() works)
        nonexistent = sandbox / "future.txt"
        print(f"  nonexistent within: {is_secure_path(nonexistent, allowed_dirs)}")
    print()


def main() -> None:
    print("=== Security examples ===\n")
    example_validate_env_var_name()
    example_validate_bindings()
    example_identifiers()
    example_secure_path()


if __name__ == "__main__":
    main()
