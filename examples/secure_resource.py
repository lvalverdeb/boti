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
from boti.core.logger_runtime import LoggerRuntime
from boti.core.models import ResourceConfig


def example_basic_io(project_root: Path) -> None:
    """Write and read text through the sandbox."""
    target = project_root / "safe.txt"
    with SecureResource(config=ResourceConfig(project_root=project_root)) as resource:
        resource.write_text_secure(target, "sandboxed hello")
        content = resource.read_text_secure(target)
        secure_path = resource.get_secure_path(target)

    print(f"  wrote/read: {content!r}")
    print(f"  secure_path: {secure_path}")


def example_traversal_rejected(project_root: Path) -> None:
    """Paths outside the sandbox raise PermissionError."""
    with SecureResource(config=ResourceConfig(project_root=project_root)) as resource:
        try:
            resource.read_text_secure(Path("/etc/passwd"))
        except PermissionError as exc:
            print(f"  blocked: {exc}")


def example_extra_allowed_paths(project_root: Path) -> None:
    """Expand the sandbox with extra_allowed_paths."""
    extra_dir = project_root / "extra_data"
    extra_dir.mkdir()
    extra_file = extra_dir / "external.txt"

    config = ResourceConfig(
        project_root=project_root,
        extra_allowed_paths=[str(extra_dir)],
    )

    with SecureResource(config=config) as resource:
        resource.write_text_secure(extra_file, "allowed extra path content")
        print(f"  extra path read: {resource.read_text_secure(extra_file)!r}")


def example_open_secure(project_root: Path) -> None:
    """Stream-based I/O with open_secure()."""
    target = project_root / "streamed.txt"
    with SecureResource(config=ResourceConfig(project_root=project_root)) as resource:
        with resource.open_secure(target, "w") as f:
            f.write("line 1\nline 2\nline 3\n")

        with resource.open_secure(target, "r") as f:
            lines = f.readlines()

        print(f"  streamed lines: {[l.rstrip() for l in lines]}")


def example_symlink_rejected(project_root: Path) -> None:
    """A symlinked extra_allowed_path is rejected — it could silently widen the sandbox."""
    outside_dir = project_root.parent / "outside_boundary"
    outside_dir.mkdir(exist_ok=True)
    symlink_path = project_root / "sneaky_link"

    try:
        symlink_path.symlink_to(outside_dir, target_is_directory=True)
    except OSError as exc:
        print(f"  (skipped: platform does not support symlinks here: {exc})")
        return

    try:
        SecureResource(
            config=ResourceConfig(
                project_root=project_root,
                extra_allowed_paths=[str(symlink_path)],
            )
        )
    except ValueError as exc:
        print(f"  blocked: {exc}")


def example_allowed_paths_property(project_root: Path) -> None:
    """Inspect the list of allowed sandbox roots."""
    with SecureResource(config=ResourceConfig(project_root=project_root)) as resource:
        print(f"  allowed_paths ({len(resource.allowed_paths)} entries):")
        for p in resource.allowed_paths:
            print(f"    - {p}")


def main() -> None:
    print("=== SecureResource examples ===\n")
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir)
        (project_root / "pyproject.toml").write_text(
            "[project]\nname='example'\n", encoding="utf-8"
        )
        example_basic_io(project_root)
        print()
        example_traversal_rejected(project_root)
        print()
        example_extra_allowed_paths(project_root)
        print()
        example_open_secure(project_root)
        print()
        example_symlink_rejected(project_root)
        print()
        example_allowed_paths_property(project_root)
        print()
        LoggerRuntime.stop_listener()


if __name__ == "__main__":
    main()
