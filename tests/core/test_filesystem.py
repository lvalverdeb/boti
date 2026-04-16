"""
Tests for FilesystemConfig, create_filesystem, and FilesystemAdapter.

Covers the use cases shown in README.md:
  - Local file filesystem configuration and access
  - Memory filesystem (in-process, no network)
  - S3 configuration validation (no live connection required)
  - Other supported backends (gcs, az, ftp, sftp)
  - FilesystemAdapter caching and invalidation
  - PyArrow filesystem wrapping
  - Configuration validation (allowlist, empty path, fs_type rejection)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from boti.core.filesystem import FilesystemAdapter, FilesystemConfig, create_filesystem


# ---------------------------------------------------------------------------
# FilesystemConfig validation
# ---------------------------------------------------------------------------


def test_filesystem_config_defaults_to_file_type():
    config = FilesystemConfig(fs_path="/tmp/data")
    assert config.fs_type == "file"


def test_filesystem_config_memory_backend():
    config = FilesystemConfig(fs_type="memory", fs_path="scratch")
    assert config.fs_type == "memory"
    assert config.fs_path == "scratch"


def test_filesystem_config_rejects_empty_fs_type():
    with pytest.raises(ValueError, match="must not be empty"):
        FilesystemConfig(fs_type="", fs_path="/tmp")


def test_filesystem_config_rejects_unlisted_backend():
    """fs_type must be one of the declared allowlist entries."""
    with pytest.raises(ValueError, match="not an allowed backend"):
        FilesystemConfig(fs_type="ssh", fs_path="/remote/host")


def test_filesystem_config_rejects_empty_fs_path():
    with pytest.raises(ValueError):
        FilesystemConfig(fs_type="file", fs_path="")


def test_filesystem_config_strips_trailing_slash_from_path():
    config = FilesystemConfig(fs_type="file", fs_path="/srv/data/")
    assert not config.fs_path.endswith("/")


def test_filesystem_config_s3_region_defaults_to_none():
    config = FilesystemConfig(fs_type="s3", fs_path="my-bucket/prefix")
    assert config.fs_region is None


def test_filesystem_config_s3_region_explicit():
    config = FilesystemConfig(fs_type="s3", fs_path="my-bucket/prefix", fs_region="eu-west-1")
    assert config.fs_region == "eu-west-1"


def test_filesystem_config_s3_storage_path_prefixes_scheme():
    config = FilesystemConfig(fs_type="s3", fs_path="analytics-bucket/raw/events")
    assert config.storage_path == "s3://analytics-bucket/raw/events"


def test_filesystem_config_file_storage_path_unchanged():
    config = FilesystemConfig(fs_type="file", fs_path="/srv/boti/data")
    assert config.storage_path == "/srv/boti/data"


def test_filesystem_config_all_allowed_backends():
    """Spot-check that all well-known allowed backends pass validation."""
    for fs_type in ("file", "local", "memory", "s3", "gcs", "az", "abfs", "ftp", "sftp", "http", "https"):
        config = FilesystemConfig(fs_type=fs_type, fs_path="some/path")
        assert config.fs_type == fs_type


# ---------------------------------------------------------------------------
# FilesystemConfig.to_fsspec_options
# ---------------------------------------------------------------------------


def test_to_fsspec_options_empty_for_local():
    config = FilesystemConfig(fs_type="file", fs_path="/tmp")
    assert config.to_fsspec_options() == {}


def test_to_fsspec_options_s3_with_key_and_secret():
    config = FilesystemConfig(
        fs_type="s3",
        fs_path="bucket/prefix",
        fs_key="AKID",
        fs_secret="SECRET",
        fs_region="us-east-1",
    )
    opts = config.to_fsspec_options()
    assert opts["key"] == "AKID"
    assert opts["secret"] == "SECRET"
    assert opts["client_kwargs"]["region_name"] == "us-east-1"


def test_to_fsspec_options_s3_with_endpoint():
    config = FilesystemConfig(
        fs_type="s3",
        fs_path="bucket",
        fs_endpoint="https://minio.internal.example",
    )
    opts = config.to_fsspec_options()
    assert opts["client_kwargs"]["endpoint_url"] == "https://minio.internal.example"


def test_to_fsspec_options_s3_no_region_omits_client_kwargs_region():
    """When fs_region is None, region_name must not appear in client_kwargs."""
    config = FilesystemConfig(fs_type="s3", fs_path="bucket")
    opts = config.to_fsspec_options()
    assert "region_name" not in opts.get("client_kwargs", {})


# ---------------------------------------------------------------------------
# create_filesystem — local file backend (README use case)
# ---------------------------------------------------------------------------


def test_create_filesystem_local_roundtrip(tmp_path):
    """
    Mirrors the README 'Local files' example:

        config = FilesystemConfig(fs_type="file", fs_path=str(tmp_path))
        fs = create_filesystem(config)
        with fs.open(path, "w") as handle:
            handle.write("hello")
    """
    config = FilesystemConfig(fs_type="file", fs_path=str(tmp_path))
    fs = create_filesystem(config)

    file_path = str(tmp_path / "example.txt")
    with fs.open(file_path, "w") as handle:
        handle.write("hello from boti")

    with fs.open(file_path, "r") as handle:
        content = handle.read()

    assert content == "hello from boti"


# ---------------------------------------------------------------------------
# create_filesystem — memory backend (README use case)
# ---------------------------------------------------------------------------


def test_create_filesystem_memory_roundtrip():
    """
    Mirrors the README 'Other supported filesystems' memory example.
    Memory filesystems are ideal for tests and ephemeral workflows.
    """
    config = FilesystemConfig(fs_type="memory", fs_path="scratch")
    fs = create_filesystem(config)

    fs.mkdir("scratch", exist_ok=True)
    with fs.open("scratch/hello.txt", "w") as handle:
        handle.write("in-memory content")

    with fs.open("scratch/hello.txt", "r") as handle:
        content = handle.read()

    assert content == "in-memory content"


# ---------------------------------------------------------------------------
# FilesystemAdapter — README use cases
# ---------------------------------------------------------------------------


def test_filesystem_adapter_caches_filesystem():
    """FilesystemAdapter returns the same fs instance on repeated calls."""
    config = FilesystemConfig(fs_type="memory", fs_path="cache-test")
    adapter = FilesystemAdapter(config)

    fs1 = adapter.get_filesystem()
    fs2 = adapter.get_filesystem()
    assert fs1 is fs2


def test_filesystem_adapter_invalidate_resets_cache():
    """invalidate() clears the cached fs so the next get_filesystem() rebuilds it.

    Note: memory filesystems are global singletons in fsspec, so we verify the
    adapter's internal cache is None after invalidate rather than object identity.
    """
    config = FilesystemConfig(fs_type="memory", fs_path="invalidate-test")
    adapter = FilesystemAdapter(config)

    adapter.get_filesystem()
    assert adapter._fs is not None

    adapter.invalidate()
    assert adapter._fs is None

    adapter.get_filesystem()
    assert adapter._fs is not None


def test_filesystem_adapter_storage_path():
    config = FilesystemConfig(fs_type="s3", fs_path="my-bucket/prefix")
    adapter = FilesystemAdapter(config)
    assert adapter.storage_path == "s3://my-bucket/prefix"


def test_filesystem_adapter_local_roundtrip(tmp_path):
    """FilesystemAdapter.get_filesystem() works end-to-end with local files."""
    config = FilesystemConfig(fs_type="file", fs_path=str(tmp_path))
    adapter = FilesystemAdapter(config)

    fs = adapter.get_filesystem()
    file_path = str(tmp_path / "adapter_test.txt")
    with fs.open(file_path, "w") as handle:
        handle.write("adapter data")

    with fs.open(file_path, "r") as handle:
        assert handle.read() == "adapter data"


# ---------------------------------------------------------------------------
# FilesystemAdapter.get_pyarrow_filesystem — README use case
# ---------------------------------------------------------------------------


def test_filesystem_adapter_pyarrow_local(tmp_path):
    """get_pyarrow_filesystem returns a usable (fs, base_path) pair for local files."""
    config = FilesystemConfig(fs_type="file", fs_path=str(tmp_path))
    adapter = FilesystemAdapter(config)

    arrow_fs, base_path = adapter.get_pyarrow_filesystem()
    assert base_path == str(tmp_path)

    # Write a small Parquet file via PyArrow to confirm the fs is live.
    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table({"x": [1, 2, 3]})
    out_path = os.path.join(base_path, "test.parquet")
    pq.write_table(table, out_path, filesystem=arrow_fs)

    result = pq.read_table(out_path, filesystem=arrow_fs)
    assert result.equals(table)


def test_filesystem_adapter_pyarrow_local_caches_result(tmp_path):
    """Second call to get_pyarrow_filesystem returns the cached pair."""
    config = FilesystemConfig(fs_type="file", fs_path=str(tmp_path))
    adapter = FilesystemAdapter(config)

    fs1, path1 = adapter.get_pyarrow_filesystem()
    fs2, path2 = adapter.get_pyarrow_filesystem()
    assert fs1 is fs2
    assert path1 == path2


def test_filesystem_adapter_pyarrow_memory():
    """get_pyarrow_filesystem falls back to FSSpecHandler for memory backend."""
    config = FilesystemConfig(fs_type="memory", fs_path="arrow-test")
    adapter = FilesystemAdapter(config)

    arrow_fs, base_path = adapter.get_pyarrow_filesystem()
    assert base_path == "arrow-test"
    assert arrow_fs is not None


# ---------------------------------------------------------------------------
# S3 configuration (no live connection — validates config structure only)
# ---------------------------------------------------------------------------


def test_filesystem_config_s3_full_readme_example():
    """
    Constructs the S3 FilesystemConfig shown in README.md and verifies the
    fsspec options it would produce (without making a network connection).
    """
    config = FilesystemConfig(
        fs_type="s3",
        fs_path="analytics-bucket/raw/events",
        fs_key="ACCESS_KEY",
        fs_secret="SECRET_KEY",
        fs_endpoint="https://minio.internal.example",
        fs_region="eu-west-1",
    )

    assert config.storage_path == "s3://analytics-bucket/raw/events"

    opts = config.to_fsspec_options()
    assert opts["key"] == "ACCESS_KEY"
    assert opts["secret"] == "SECRET_KEY"
    assert opts["client_kwargs"]["endpoint_url"] == "https://minio.internal.example"
    assert opts["client_kwargs"]["region_name"] == "eu-west-1"
    assert opts["verify"] is True


def test_filesystem_config_s3_ssl_disabled():
    config = FilesystemConfig(
        fs_type="s3",
        fs_path="bucket",
        fs_verify_ssl=False,
    )
    opts = config.to_fsspec_options()
    assert opts["verify"] is False


# ---------------------------------------------------------------------------
# FilesystemConfig.from_settings — environment-backed construction
# ---------------------------------------------------------------------------


def test_filesystem_config_from_settings():
    from boti.core.settings import FilesystemSettings

    settings = FilesystemSettings(fs_type="file", fs_path="/data/project")
    config = FilesystemConfig.from_settings(settings)
    assert config.fs_type == "file"
    assert config.fs_path == "/data/project"


def test_filesystem_config_from_settings_with_overrides():
    from boti.core.settings import FilesystemSettings

    settings = FilesystemSettings(fs_type="file", fs_path="/data/project")
    config = FilesystemConfig.from_settings(settings, fs_path="/data/override")
    assert config.fs_path == "/data/override"


def test_filesystem_config_from_env_prefix(tmp_path):
    """FilesystemConfig.from_env_prefix() loads config from prefixed env vars.

    load_prefixed_model builds the key as ``prefix + field_name.upper()``, so
    for prefix ``MYFS_`` and field ``fs_type`` the expected key is ``MYFS_FS_TYPE``.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("MYFS_FS_TYPE=memory\nMYFS_FS_PATH=scratch\n", encoding="utf-8")

    config = FilesystemConfig.from_env_prefix("MYFS_", env_file=env_file)
    assert config.fs_type == "memory"
    assert config.fs_path == "scratch"
