"""
Typed filesystem configuration and runtime adapters.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

import fsspec
import pyarrow.fs as pafs
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from boti.core.settings import FilesystemSettings, load_prefixed_model

# Explicit allowlist of fsspec backend identifiers that are safe to instantiate
# from user-supplied or environment-backed configuration.  Arbitrary backends
# (e.g. custom handlers, ssh, smb) must be constructed directly via fsspec.
_ALLOWED_FS_TYPES: frozenset[str] = frozenset({
    "file",
    "local",
    "memory",
    "s3",
    "s3a",
    "gcs",
    "gs",
    "az",
    "abfs",
    "adl",
    "ftp",
    "sftp",
    "http",
    "https",
    "zip",
    "tar",
    "blockcache",
    "filecache",
    "simplecache",
    "github",
    "git",
    "arrow_hdfs",
    "hdfs",
})


class FilesystemConfig(BaseModel):
    """Typed configuration for local and remote filesystem profiles."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    fs_type: str = Field(default="file")
    fs_path: str = Field(..., min_length=1)
    fs_key: Optional[str] = Field(default=None)
    fs_secret: Optional[SecretStr] = Field(default=None)
    fs_endpoint: Optional[str] = Field(default=None)
    fs_token: Optional[SecretStr] = Field(default=None)
    fs_region: Optional[str] = Field(default=None)
    fs_verify_ssl: bool = Field(default=True)
    fs_options: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: FilesystemSettings, **overrides: Any) -> "FilesystemConfig":
        payload = settings.model_dump(exclude_none=True)
        payload.update(overrides)
        return cls(**payload)

    @classmethod
    def from_env_prefix(
        cls,
        prefix: str,
        *,
        env_file: Optional[str | Path] = None,
        **overrides: Any,
    ) -> "FilesystemConfig":
        settings = load_prefixed_model(FilesystemSettings, prefix, env_file=env_file)
        return cls.from_settings(settings, **overrides)

    @field_validator("fs_type")
    @classmethod
    def validate_fs_type(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("fs_type must not be empty.")
        if normalized not in _ALLOWED_FS_TYPES:
            raise ValueError(
                f"fs_type '{normalized}' is not an allowed backend. "
                f"Permitted values: {sorted(_ALLOWED_FS_TYPES)}. "
                "To use a custom backend, construct the fsspec filesystem directly."
            )
        return normalized

    @field_validator("fs_path")
    @classmethod
    def validate_fs_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("fs_path must be specified.")
        return normalized.rstrip("/")

    @property
    def storage_path(self) -> str:
        if self.fs_type == "s3" and not self.fs_path.startswith("s3://"):
            return f"s3://{self.fs_path}"
        return self.fs_path

    def to_fsspec_options(self) -> dict[str, Any]:
        options = dict(self.fs_options)
        if self.fs_type == "s3":
            if self.fs_key:
                options["key"] = self.fs_key
            if self.fs_secret is not None:
                options["secret"] = self.fs_secret.get_secret_value()
            if self.fs_token is not None:
                options["token"] = self.fs_token.get_secret_value()

            client_kwargs = dict(options.get("client_kwargs", {}))
            if self.fs_endpoint:
                client_kwargs["endpoint_url"] = self.fs_endpoint
            if self.fs_region:
                client_kwargs["region_name"] = self.fs_region
            if client_kwargs:
                options["client_kwargs"] = client_kwargs

            config_kwargs = dict(options.get("config_kwargs", {}))
            if "verify" not in options:
                options["verify"] = self.fs_verify_ssl
            if config_kwargs:
                options["config_kwargs"] = config_kwargs

        return options


def create_filesystem(config: FilesystemConfig) -> fsspec.AbstractFileSystem:
    """Build a concrete fsspec filesystem instance from typed config."""
    return fsspec.filesystem(config.fs_type, **config.to_fsspec_options())


class FilesystemAdapter:
    """Runtime adapter that caches filesystem clients for a named profile."""

    def __init__(self, config: FilesystemConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._fs: Optional[fsspec.AbstractFileSystem] = None
        self._arrow_fs: Optional[pafs.FileSystem] = None
        self._arrow_base_path: Optional[str] = None

    @property
    def storage_path(self) -> str:
        return self.config.storage_path

    def get_filesystem(self) -> fsspec.AbstractFileSystem:
        with self._lock:
            if self._fs is None:
                self._fs = create_filesystem(self.config)
            return self._fs

    def invalidate(self) -> None:
        with self._lock:
            fs = self._fs
            if fs is not None and hasattr(fs, "invalidate_cache"):
                fs.invalidate_cache()
            self._fs = None
            self._arrow_fs = None
            self._arrow_base_path = None

    def get_pyarrow_filesystem(self) -> tuple[pafs.FileSystem, str]:
        with self._lock:
            if self._arrow_fs is not None and self._arrow_base_path is not None:
                return self._arrow_fs, self._arrow_base_path

            if self.config.fs_type == "file":
                self._arrow_fs = pafs.LocalFileSystem()
                self._arrow_base_path = self.storage_path.replace("file://", "")
                return self._arrow_fs, self._arrow_base_path

            if self.config.fs_type == "s3":
                arrow_kwargs: dict[str, Any] = {
                    "access_key": self.config.fs_key,
                    "secret_key": None if self.config.fs_secret is None else self.config.fs_secret.get_secret_value(),
                    "session_token": None if self.config.fs_token is None else self.config.fs_token.get_secret_value(),
                    "region": self.config.fs_region,
                }
                if self.config.fs_endpoint:
                    arrow_kwargs["endpoint_override"] = self.config.fs_endpoint
                    arrow_kwargs["scheme"] = "https" if self.config.fs_endpoint.startswith("https://") else "http"

                self._arrow_fs = pafs.S3FileSystem(**{k: v for k, v in arrow_kwargs.items() if v is not None})
                self._arrow_base_path = self.storage_path.replace("s3://", "", 1)
                return self._arrow_fs, self._arrow_base_path

            handler = pafs.FSSpecHandler(self.get_filesystem())
            self._arrow_fs = pafs.PyFileSystem(handler)
            self._arrow_base_path = self.storage_path
            return self._arrow_fs, self._arrow_base_path
