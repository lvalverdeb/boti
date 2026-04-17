"""Attempt filesystem client creation for every backend supported by boti.

This script performs constructor-level connection attempts for all fs types in
`boti.core.filesystem._ALLOWED_FS_TYPES`. Some backends require optional extras,
credentials, or reachable services; those attempts are reported with the
exception type and message so you can quickly identify missing prerequisites.

Run with:
    uv run python examples/filesystem_supported_backends.py
"""

from __future__ import annotations

import io
import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from boti.core.filesystem import _ALLOWED_FS_TYPES, FilesystemAdapter, FilesystemConfig


@dataclass(frozen=True)
class BackendSpec:
    fs_path: str
    fs_options: dict[str, Any]


def _build_sample_archives(base_dir: Path) -> tuple[Path, Path]:
    zip_path = base_dir / "sample.zip"
    with zipfile.ZipFile(zip_path, mode="w") as archive:
        archive.writestr("hello.txt", "hello from zip")

    tar_path = base_dir / "sample.tar"
    tar_bytes = b"hello from tar"
    tar_info = tarfile.TarInfo(name="hello.txt")
    tar_info.size = len(tar_bytes)
    with tarfile.open(tar_path, mode="w") as archive:
        archive.addfile(tar_info, io.BytesIO(tar_bytes))

    return zip_path, tar_path


def _build_specs(tmp_dir: Path) -> dict[str, BackendSpec]:
    zip_path, tar_path = _build_sample_archives(tmp_dir)
    cache_dir = tmp_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    return {
        "file": BackendSpec(fs_path=str(tmp_dir), fs_options={}),
        "local": BackendSpec(fs_path=str(tmp_dir), fs_options={}),
        "memory": BackendSpec(fs_path="memory-demo", fs_options={}),
        "s3": BackendSpec(fs_path="demo-bucket/path", fs_options={"anon": True}),
        "s3a": BackendSpec(fs_path="demo-bucket/path", fs_options={"anon": True}),
        "gcs": BackendSpec(fs_path="demo-bucket/path", fs_options={"token": "anon"}),
        "gs": BackendSpec(fs_path="demo-bucket/path", fs_options={"token": "anon"}),
        "az": BackendSpec(fs_path="container/path", fs_options={}),
        "abfs": BackendSpec(fs_path="container/path", fs_options={}),
        "adl": BackendSpec(fs_path="container/path", fs_options={}),
        "ftp": BackendSpec(fs_path="/", fs_options={"host": "localhost"}),
        "sftp": BackendSpec(fs_path="/", fs_options={"host": "localhost"}),
        "http": BackendSpec(fs_path="https://example.com", fs_options={}),
        "https": BackendSpec(fs_path="https://example.com", fs_options={}),
        "zip": BackendSpec(fs_path=str(zip_path), fs_options={"fo": str(zip_path)}),
        "tar": BackendSpec(fs_path=str(tar_path), fs_options={"fo": str(tar_path)}),
        "blockcache": BackendSpec(
            fs_path=str(tmp_dir),
            fs_options={"target_protocol": "file", "cache_storage": str(cache_dir)},
        ),
        "filecache": BackendSpec(
            fs_path=str(tmp_dir),
            fs_options={"target_protocol": "file", "cache_storage": str(cache_dir)},
        ),
        "simplecache": BackendSpec(
            fs_path=str(tmp_dir),
            fs_options={"target_protocol": "file", "cache_storage": str(cache_dir)},
        ),
        "github": BackendSpec(
            fs_path="/",
            fs_options={"org": "fsspec", "repo": "filesystem_spec"},
        ),
        "git": BackendSpec(fs_path=str(tmp_dir), fs_options={}),
        "arrow_hdfs": BackendSpec(fs_path="/", fs_options={"host": "localhost", "port": 8020}),
        "hdfs": BackendSpec(fs_path="/", fs_options={"host": "localhost", "port": 8020}),
    }


def main() -> None:
    print("Checking backend instantiation for all supported fs_type values...")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        specs = _build_specs(tmp_dir)

        for fs_type in sorted(_ALLOWED_FS_TYPES):
            spec = specs.get(fs_type, BackendSpec(fs_path="/", fs_options={}))
            try:
                config = FilesystemConfig(fs_type=fs_type, fs_path=spec.fs_path, fs_options=spec.fs_options)
                adapter = FilesystemAdapter(config, max_attempts=1)
                adapter.get_filesystem()
                status = "ok"
                detail = "client created"
            except Exception as exc:  # pragma: no cover - example diagnostics
                status = "failed"
                detail = f"{type(exc).__name__}: {exc}"

            print(f"[{status:6}] fs_type={fs_type:10} path={spec.fs_path!r} -> {detail}")


if __name__ == "__main__":
    main()

