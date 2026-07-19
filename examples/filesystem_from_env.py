"""
Filesystem configuration from environment variables and settings models.

Demonstrates:
  - FilesystemSettings — typed settings model for filesystem connection profiles
  - FilesystemConfig.from_settings() — build config from a FilesystemSettings instance
  - FilesystemConfig.from_env_prefix() — load config from prefixed environment variables
  - Integration with create_filesystem() for actual filesystem access
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from boti.core.filesystem import FilesystemAdapter, FilesystemConfig, create_filesystem
from boti.core.settings import FilesystemSettings


def example_from_settings_model() -> None:
    """Build FilesystemConfig from a FilesystemSettings instance."""
    settings = FilesystemSettings(
        fs_type="memory",
        fs_path="demo-bucket/data",
    )
    config = FilesystemConfig.from_settings(settings)
    print(f"  fs_type:      {config.fs_type}")
    print(f"  fs_path:      {config.fs_path}")
    print(f"  storage_path: {config.storage_path}")

    # Use it to create a working filesystem
    fs = create_filesystem(config)
    fs.touch("demo-bucket/data/hello.txt")
    print(f"  files created: {fs.ls('demo-bucket/data')}")
    print()


def example_from_settings_with_overrides() -> None:
    """Override specific fields when converting from settings."""
    settings = FilesystemSettings(fs_type="file", fs_path="/tmp")
    config = FilesystemConfig.from_settings(settings, fs_type="memory", fs_path="override-bucket")
    print(f"  overridden fs_type: {config.fs_type!r}")
    print(f"  overridden fs_path: {config.fs_path!r}")
    print()


def example_from_env_prefix() -> None:
    """Load configuration from prefixed environment variables."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        env_file = Path(tmp_dir) / ".env"
        env_file.write_text("STORAGE_FS_TYPE=memory\nSTORAGE_FS_PATH=env-bucket/data\n")

        config = FilesystemConfig.from_env_prefix("STORAGE_", env_file=env_file)
        print(f"  loaded fs_type: {config.fs_type!r}")
        print(f"  loaded fs_path: {config.fs_path!r}")

        fs = create_filesystem(config)
        fs.touch("env-bucket/data/file.txt")
        print(f"  created: {fs.ls('env-bucket/data')}")
    print()


def example_from_env_prefix_with_overrides() -> None:
    """Override specific fields when loading from env prefix."""
    os.environ["CACHE_FS_TYPE"] = "memory"
    os.environ["CACHE_FS_PATH"] = "cache-bucket"

    config = FilesystemConfig.from_env_prefix("CACHE_", fs_path="override-cache-bucket")
    print(f"  fs_type (from env): {config.fs_type!r}")
    print(f"  fs_path (overridden): {config.fs_path!r}")

    for k in ("CACHE_FS_TYPE", "CACHE_FS_PATH"):
        os.environ.pop(k, None)
    print()


def example_filesystem_settings_to_adapter() -> None:
    """End-to-end: FilesystemSettings -> FilesystemConfig -> FilesystemAdapter."""
    settings = FilesystemSettings(fs_type="memory", fs_path="adapter-demo")
    config = FilesystemConfig.from_settings(settings)
    adapter = FilesystemAdapter(config)

    fs = adapter.get_filesystem()
    fs.mkdir("adapter-demo", exist_ok=True)
    with fs.open("adapter-demo/test.txt", "w") as f:
        f.write("from settings to adapter")

    with fs.open("adapter-demo/test.txt", "r") as f:
        print(f"  content: {f.read()!r}")

    adapter.invalidate()
    print(f"  adapter invalidated: {adapter._fs is None}")
    print()


def main() -> None:
    print("=== Filesystem from environment examples ===\n")
    example_from_settings_model()
    example_from_settings_with_overrides()
    example_from_env_prefix()
    example_from_env_prefix_with_overrides()
    example_filesystem_settings_to_adapter()


if __name__ == "__main__":
    main()
