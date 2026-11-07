"""
Boti CLI — diagnostics and environment verification.

Available commands:
    boti info          Show version, project root, and runtime info
    boti check         Verify the environment is healthy
"""

from __future__ import annotations

import argparse
import importlib.metadata
import pkgutil
import platform
import sys
from pathlib import Path

# Optional extras declared in pyproject.toml: extra name -> the distribution
# whose presence means it is installed.
_OPTIONAL_EXTRAS: dict[str, str] = {"s3": "s3fs"}


def _get_version() -> str:
    try:
        return importlib.metadata.version("boti")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0 (dev)"


def _installed_version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _cmd_info(_args: argparse.Namespace) -> int:
    import boti
    from boti.core.project import ProjectService
    from boti.core.secure_io import SecureResource

    version = _get_version()
    root = ProjectService.detect_project_root()

    print(f"boti v{version}")
    print(f"  Python:     {platform.python_version()} ({platform.python_implementation()})")
    print(f"  Platform:   {sys.platform}")
    print(f"  Project:    {root}")
    print("  PyPI name:  boti")
    print(f"  Installed:  {Path(boti.__file__).parent.resolve()}")
    print(f"  Open strategy: {SecureResource.open_strategy}")

    print("  Extras:")
    for extra, distribution in _OPTIONAL_EXTRAS.items():
        installed = _installed_version(distribution)
        if installed is None:
            print(f"    - {extra}: missing (pip install 'boti[{extra}]')")
        else:
            print(f"    - {extra}: installed ({distribution}=={installed})")

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
    # Discovered, not listed: a module added to boti.core is checked without
    # anyone remembering to register it here.
    core_dir = Path(__file__).parent / "core"
    core_modules = [
        "boti.core",
        *(f"boti.core.{info.name}" for info in pkgutil.iter_modules([str(core_dir)])),
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


def _report_optional_extras() -> None:
    # Informational only: an extra is opt-in, so its absence is never an error.
    for extra, distribution in _OPTIONAL_EXTRAS.items():
        installed = _installed_version(distribution)
        if installed is None:
            print(f"  [info] Extra: {extra} not installed (pip install 'boti[{extra}]')")
        else:
            print(f"  [ok] Extra: {extra} ({distribution}=={installed})")


def _cmd_check(_args: argparse.Namespace) -> int:
    errors: list[str] = [
        *_check_project_root(),
        *_check_core_modules_importable(),
        *_check_version_readable(),
        *_check_critical_dependencies(),
    ]
    _report_optional_extras()

    if errors:
        print(f"\n  {len(errors)} issue(s) found:")
        for err in errors:
            print(f"    ! {err}")
        return 1

    print("\n  All checks passed.")
    return 0


def main(argv: list[str] | None = None) -> None:
    """Entry point. *argv* defaults to ``sys.argv[1:]``; tests pass it explicitly."""
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

    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
