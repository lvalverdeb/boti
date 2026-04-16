"""
FilesystemConfig and FilesystemAdapter examples.

Demonstrates the three patterns shown in README.md:

  1. Local file access with FilesystemConfig + create_filesystem
  2. In-memory filesystem (ideal for tests / ephemeral workflows)
  3. S3-compatible configuration (config structure only — no live connection)

Run with:
    uv run python examples/filesystem_config.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from boti.core.filesystem import FilesystemAdapter, FilesystemConfig, create_filesystem


# ---------------------------------------------------------------------------
# 1. Local file access
# ---------------------------------------------------------------------------

def example_local_files() -> None:
    """Write and read a file on the local filesystem via FilesystemConfig."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        config = FilesystemConfig(
            fs_type="file",
            fs_path=tmp_dir,
        )

        fs = create_filesystem(config)

        file_path = str(Path(tmp_dir) / "example.txt")
        with fs.open(file_path, "w") as handle:
            handle.write("hello from boti")

        with fs.open(file_path, "r") as handle:
            content = handle.read()

        print(f"[local] read back: {content!r}")


# ---------------------------------------------------------------------------
# 2. In-memory filesystem (no disk I/O)
# ---------------------------------------------------------------------------

def example_memory_filesystem() -> None:
    """Use the memory backend for ephemeral or test workflows."""
    config = FilesystemConfig(fs_type="memory", fs_path="scratch")
    fs = create_filesystem(config)

    fs.mkdir("scratch", exist_ok=True)
    with fs.open("scratch/note.txt", "w") as handle:
        handle.write("in-memory content")

    with fs.open("scratch/note.txt", "r") as handle:
        content = handle.read()

    print(f"[memory] read back: {content!r}")


# ---------------------------------------------------------------------------
# 3. FilesystemAdapter with caching
# ---------------------------------------------------------------------------

def example_filesystem_adapter() -> None:
    """
    FilesystemAdapter wraps FilesystemConfig and caches the live filesystem
    client so the connection is reused across multiple calls.
    """
    config = FilesystemConfig(fs_type="memory", fs_path="adapter-demo")
    adapter = FilesystemAdapter(config)

    fs = adapter.get_filesystem()     # creates and caches the client
    fs2 = adapter.get_filesystem()    # returns cached instance
    assert fs is fs2, "adapter must return the same cached instance"

    adapter.invalidate()              # drop the cache (useful after credential rotation)
    fs3 = adapter.get_filesystem()    # new instance
    assert fs3 is not None

    print(f"[adapter] storage_path={adapter.storage_path!r}")


# ---------------------------------------------------------------------------
# 4. S3-compatible configuration (config structure only)
# ---------------------------------------------------------------------------

def example_s3_config_structure() -> None:
    """
    Build the S3 FilesystemConfig shown in README.md and inspect the fsspec
    options it would pass — without making a real network connection.
    """
    config = FilesystemConfig(
        fs_type="s3",
        fs_path="analytics-bucket/raw/events",
        fs_key="ACCESS_KEY",
        fs_secret="SECRET_KEY",                         # type: ignore[arg-type]
        fs_endpoint="https://minio.internal.example",
        fs_region="eu-west-1",
    )

    print(f"[s3] storage_path:  {config.storage_path}")
    print(f"[s3] fsspec options: {config.to_fsspec_options()}")

    # To get a live filesystem:
    #   adapter = FilesystemAdapter(config)
    #   fs = adapter.get_filesystem()


# ---------------------------------------------------------------------------
# 5. Other backend configurations (README 'Other supported filesystems')
# ---------------------------------------------------------------------------

def example_other_backends() -> None:
    """Construct configs for various fsspec backends without connecting."""
    memory_config = FilesystemConfig(fs_type="memory", fs_path="scratch")
    gcs_config    = FilesystemConfig(fs_type="gcs",    fs_path="my-bucket/datasets")
    azure_config  = FilesystemConfig(fs_type="az",     fs_path="container/path")

    for cfg in (memory_config, gcs_config, azure_config):
        print(f"[backends] {cfg.fs_type!r:10s}  storage_path={cfg.storage_path!r}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    example_local_files()
    example_memory_filesystem()
    example_filesystem_adapter()
    example_s3_config_structure()
    example_other_backends()


if __name__ == "__main__":
    main()
