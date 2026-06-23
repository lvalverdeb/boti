"""
Built-in SecureResource example for sandboxed file I/O.

Demonstrates:
  - SecureResource for sandboxed read/write operations
  - Path traversal rejection (PermissionError)
  - extra_allowed_paths for expanding sandbox boundaries
  - open_secure() for stream-based I/O
  - Symlink detection in extra_allowed_paths
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from boti.core import SecureResource
from boti.core.models import ResourceConfig


def example_basic_io() -> None:
    """Write and read text through the sandbox."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir)
        (project_root / "pyproject.toml").write_text("[project]\nname='example'\n", encoding="utf-8")
        target = project_root / "safe.txt"

        with SecureResource(config=ResourceConfig(project_root=project_root)) as resource:
            resource.write_text_secure(target, "sandboxed hello")
            content = resource.read_text_secure(target)
            secure_path = resource.get_secure_path(target)

        print(f"  wrote/read: {content!r}")
        print(f"  secure_path: {secure_path}")
    print()


def example_traversal_rejected() -> None:
    """Paths outside the sandbox raise PermissionError."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir)
        (project_root / "pyproject.toml").write_text("[project]\nname='blocked'\n", encoding="utf-8")

        with SecureResource(config=ResourceConfig(project_root=project_root)) as resource:
            try:
                resource.read_text_secure(Path("/etc/passwd"))
            except PermissionError as exc:
                print(f"  blocked: {exc}")
    print()


def example_extra_allowed_paths() -> None:
    """Expand the sandbox with extra_allowed_paths."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir)
        (project_root / "pyproject.toml").write_text("[project]\nname='extras'\n", encoding="utf-8")

        extra_dir = Path(tmp_dir) / "extra_data"
        extra_dir.mkdir()
        extra_file = extra_dir / "external.txt"

        config = ResourceConfig(
            project_root=project_root,
            extra_allowed_paths=[str(extra_dir)],
        )

        with SecureResource(config=config) as resource:
            resource.write_text_secure(extra_file, "allowed extra path content")
            print(f"  extra path read: {resource.read_text_secure(extra_file)!r}")
    print()


def example_open_secure() -> None:
    """Stream-based I/O with open_secure()."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir)
        (project_root / "pyproject.toml").write_text("[project]\nname='stream'\n", encoding="utf-8")
        target = project_root / "streamed.txt"

        with SecureResource(config=ResourceConfig(project_root=project_root)) as resource:
            with resource.open_secure(target, "w") as f:
                f.write("line 1\nline 2\nline 3\n")

            with resource.open_secure(target, "r") as f:
                lines = f.readlines()

            print(f"  streamed lines: {[l.rstrip() for l in lines]}")
    print()


def example_allowed_paths_property() -> None:
    """Inspect the list of allowed sandbox roots."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir)
        (project_root / "pyproject.toml").write_text("[project]\nname='inspect'\n", encoding="utf-8")

        with SecureResource(config=ResourceConfig(project_root=project_root)) as resource:
            print(f"  allowed_paths ({len(resource.allowed_paths)} entries):")
            for p in resource.allowed_paths:
                print(f"    - {p}")
    print()


def main() -> None:
    print("=== SecureResource examples ===\n")
    example_basic_io()
    example_traversal_rejected()
    example_extra_allowed_paths()
    example_open_secure()
    example_allowed_paths_property()


if __name__ == "__main__":
    main()
