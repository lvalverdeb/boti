"""
FilesystemAdapter PyArrow integration example.

Demonstrates:
  - FilesystemAdapter.get_pyarrow_filesystem() for local filesystem
  - PyArrow + in-memory filesystem via FSSpecHandler
  - Reading/writing Parquet through pyarrow on an fsspec-backed filesystem
  - Adapter caching behavior (same instance returned on repeated calls)
"""

from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq

from boti.core.filesystem import FilesystemAdapter, FilesystemConfig


def example_local_filesystem() -> None:
    """Create a pyarrow LocalFileSystem through the adapter."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp_dir:
        config = FilesystemConfig(fs_type="file", fs_path=tmp_dir)
        adapter = FilesystemAdapter(config)

        arrow_fs, base_path = adapter.get_pyarrow_filesystem()
        print(f"  arrow_fs type: {type(arrow_fs).__name__}")
        print(f"  base_path: {base_path!r}")

        # Write a small parquet file through pyarrow
        table = pa.table({"x": [1, 2, 3], "y": ["a", "b", "c"]})
        file_path = str(Path(tmp_dir) / "test.parquet")
        pq.write_table(table, file_path, filesystem=arrow_fs)

        # Read it back
        restored = pq.read_table(file_path, filesystem=arrow_fs)
        print(f"  restored table: {restored}")
    print()


def example_memory_fsspec_handler() -> None:
    """Use pyarrow on an in-memory fsspec filesystem via FSSpecHandler."""
    config = FilesystemConfig(fs_type="memory", fs_path="/pyarrow-test")
    adapter = FilesystemAdapter(config)

    arrow_fs, base_path = adapter.get_pyarrow_filesystem()
    print(f"  arrow_fs type: {type(arrow_fs).__name__}")
    print(f"  base_path: {base_path!r}")

    # Write and read a parquet file on the in-memory filesystem
    table = pa.table({"id": [10, 20, 30], "value": [1.5, 2.5, 3.5]})
    pq.write_table(table, "/pyarrow-test/data.parquet", filesystem=arrow_fs)

    restored = pq.read_table("/pyarrow-test/data.parquet", filesystem=arrow_fs)
    print(f"  restored memory table: {restored}")
    print()


def example_adapter_caching() -> None:
    """get_pyarrow_filesystem() returns the cached instance on subsequent calls."""
    config = FilesystemConfig(fs_type="memory", fs_path="/cache-test")
    adapter = FilesystemAdapter(config)

    fs1, path1 = adapter.get_pyarrow_filesystem()
    fs2, path2 = adapter.get_pyarrow_filesystem()
    print(f"  same instance cached: {fs1 is fs2}")
    print(f"  same base path: {path1 == path2}")

    adapter.invalidate()
    fs3, path3 = adapter.get_pyarrow_filesystem()
    print(f"  new after invalidate: {fs1 is fs3}")
    print()


def main() -> None:
    print("=== PyArrow filesystem examples ===\n")
    example_local_filesystem()
    example_memory_fsspec_handler()
    example_adapter_caching()


if __name__ == "__main__":
    main()
