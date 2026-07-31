"""
Boti CLI — diagnostics and environment verification.

Available commands:
    boti info          Show version, project root, and runtime info
    boti check         Verify the environment is healthy
"""

from __future__ import annotations

import argparse
import importlib.metadata
import platform
import sys
from pathlib import Path


def _get_version() -> str:
    try:
        return importlib.metadata.version("boti")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0 (dev)"


def _cmd_info(_args: argparse.Namespace) -> int:
    import boti
    from boti.core.project import ProjectService

    version = _get_version()
    root = ProjectService.detect_project_root()

    print(f"boti v{version}")
    print(f"  Python:     {platform.python_version()} ({platform.python_implementation()})")
    print(f"  Platform:   {sys.platform}")
    print(f"  Project:    {root}")
    print("  PyPI name:  boti")
    print(f"  Installed:  {Path(boti.__file__).parent.resolve()}")

    try:
        from boti.core import __all__ as public_api

        print(f"  Public API: {len(public_api)} symbols")
        for name in sorted(public_api):
            print(f"    - {name}")
    except Exception as exc:
        print(f"  Public API: (unavailable: {exc})")

    return 0


def _check_project_root() -> list[str]:
    errors: list[str] = []
    from boti.core.project import ProjectService

    try:
        root = ProjectService.detect_project_root()
        if not root.is_dir():
            errors.append(f"Project root {root} is not a directory")
        print(f"  [ok] Project root: {root}")
    except Exception as exc:
        errors.append(f"Project root detection failed: {exc}")
    return errors


def _check_core_modules_importable() -> list[str]:
    errors: list[str] = []
    core_modules = [
        "boti.core",
        "boti.core.models",
        "boti.core.security",
        "boti.core.settings",
        "boti.core.project",
        "boti.core.logger",
        "boti.core.logger_filters",
        "boti.core.logger_handlers",
        "boti.core.logger_runtime",
        "boti.core.managed_resource",
        "boti.core.secure_io",
        "boti.core.filesystem",
    ]
    for mod_name in core_modules:
        try:
            importlib.import_module(mod_name)
            print(f"  [ok] Module: {mod_name}")
        except Exception as exc:
            errors.append(f"Module {mod_name} failed to import: {exc}")
    return errors


def _check_version_readable() -> list[str]:
    errors: list[str] = []
    try:
        version = _get_version()
        print(f"  [ok] Version: v{version}")
    except Exception as exc:
        errors.append(f"Version detection failed: {exc}")
    return errors


def _check_critical_dependencies() -> list[str]:
    errors: list[str] = []
    critical_deps = ["pydantic", "fsspec", "pyarrow"]
    for dep in critical_deps:
        try:
            importlib.import_module(dep)
            dv = importlib.metadata.version(dep)
            print(f"  [ok] Dependency: {dep}=={dv}")
        except ImportError:
            errors.append(f"Missing critical dependency: {dep}")
        except Exception as exc:
            errors.append(f"Dependency {dep} error: {exc}")
    return errors


def _cmd_check(_args: argparse.Namespace) -> int:
    errors: list[str] = [
        *_check_project_root(),
        *_check_core_modules_importable(),
        *_check_version_readable(),
        *_check_critical_dependencies(),
    ]

    if errors:
        print(f"\n  {len(errors)} issue(s) found:")
        for err in errors:
            print(f"    ! {err}")
        return 1

    print("\n  All checks passed.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="boti",
        description="Boti runtime CLI — diagnostics and environment verification",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"boti v{_get_version()}",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    info_parser = sub.add_parser("info", help="Show version, project root, and runtime info")
    info_parser.set_defaults(func=_cmd_info)

    check_parser = sub.add_parser("check", help="Verify the environment is healthy")
    check_parser.set_defaults(func=_cmd_check)

    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
