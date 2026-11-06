"""
Typed filesystem configuration and runtime adapters.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import fsspec
import pyarrow.fs as pafs
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from boti.core.filesystem_options import (
    _FSSPEC_OPTIONS_BUILDERS,
    _filesystem_options_with_compat,
    _pyarrow_s3_kwargs_with_compat,
)
from boti.core.filesystem_security import (
    ENDPOINT_ALLOWLIST,
    _allowlist_endpoint_from_url,
    _reject_private_host,
    _validate_endpoint_url,
    _validate_fs_options,
    _validate_fs_type,
    add_endpoint_to_allowlist,
)
from boti.core.settings import FilesystemSettings, load_prefixed_model

_logger = logging.getLogger(__name__)

__all__ = [
    "FilesystemConfig",
    "FilesystemAdapter",
    "create_filesystem",
    "add_endpoint_to_allowlist",
    "ENDPOINT_ALLOWLIST",
]

# Backend identifiers dispatched on explicitly below (storage_path's s3://
# prefixing, _build_pyarrow_filesystem's backend selection), named rather
# than repeated as bare string literals at each comparison site.
_FS_TYPE_FILE = "file"
_FS_TYPE_S3 = "s3"


class FilesystemConfig(BaseModel):
    """Typed configuration for local and remote filesystem profiles.

    ``fs_endpoint`` is validated against literal private/reserved IP
    addresses and reserved hostnames (see ``validate_fs_endpoint``) to block
    SSRF via a directly-supplied private address. This does **not** cover
    hostname-based SSRF via DNS rebinding — a public DNS name that resolves
    to a private IP (e.g. a ``nip.io``-style domain) is not checked, since
    DNS resolution is intentionally avoided here to keep validation fast and
    deterministic. Preventing that is the operator's responsibility via
    network egress controls, not this validator's.
    """

    # validate_assignment: the SSRF guard is only worth having if it also
    # covers `config.fs_endpoint = ...` after construction. Field and model
    # validators re-run on every assignment.
    model_config = ConfigDict(arbitrary_types_allowed=True, validate_assignment=True)

    fs_type: str = Field(default="file")
    fs_path: str = Field(..., min_length=1)
    fs_key: str | None = Field(default=None)
    fs_secret: SecretStr | None = Field(default=None)
    fs_endpoint: str | None = Field(default=None)
    fs_token: SecretStr | None = Field(default=None)
    fs_region: str | None = Field(default=None)
    fs_verify_ssl: bool = Field(default=True)
    fs_connect_timeout: float | None = Field(
        default=10.0,
        description=(
            "TCP connect timeout in seconds for remote backends. None disables the timeout."
        ),
    )
    fs_read_timeout: float | None = Field(
        default=30.0,
        description=(
            "Socket read timeout in seconds for remote backends. None disables the timeout."
        ),
    )
    fs_options: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: FilesystemSettings, **overrides: Any) -> FilesystemConfig:
        payload = settings.model_dump(exclude_none=True)
        payload.update(overrides)
        return cls(**payload)

    @classmethod
    def from_env_prefix(
        cls,
        prefix: str,
        *,
        env_file: str | Path | None = None,
        trust_env_endpoint: bool = False,
        **overrides: Any,
    ) -> FilesystemConfig:
        """Build a :class:`FilesystemConfig` from ``{prefix}*`` environment variables.

        Args:
            trust_env_endpoint: When ``True``, allowlist the endpoint loaded from
                ``{prefix}FS_ENDPOINT`` before validation, so a private/internal
                address configured via trusted deployment config (rather than
                user input) doesn't trip the SSRF guard in
                :meth:`validate_fs_endpoint`. Defaults to ``False`` — callers must
                still opt in explicitly, or call
                :func:`add_endpoint_to_allowlist` themselves beforehand.
        """
        settings = load_prefixed_model(FilesystemSettings, prefix, env_file=env_file)
        if trust_env_endpoint and settings.fs_endpoint:
            _allowlist_endpoint_from_url(settings.fs_endpoint)
        return cls.from_settings(settings, **overrides)

    @field_validator("fs_endpoint")
    @classmethod
    def validate_fs_endpoint(cls, value: str | None) -> str | None:
        return None if value is None else _validate_endpoint_url(value)

    @model_validator(mode="after")
    def validate_endpoint_bearing_fields(self) -> FilesystemConfig:
        """Apply the SSRF guard to every field that can redirect traffic.

        ``fs_endpoint`` is not the only way to point a backend at an internal
        address: an http(s) ``fs_path`` is the target URL itself, and
        ``fs_options`` can carry endpoints, a bare ``host``, or a wrapped
        ``target_protocol`` that fsspec passes through untouched.
        """
        if self.fs_type in {"http", "https"}:
            _reject_private_host(self.fs_path)
        _validate_fs_options(self.fs_options)
        return self

    @field_validator("fs_type")
    @classmethod
    def validate_fs_type(cls, value: str) -> str:
        return _validate_fs_type(value)

    @field_validator("fs_path")
    @classmethod
    def validate_fs_path(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("fs_path must be specified.")
        return normalized.rstrip("/")

    @property
    def storage_path(self) -> str:
        if self.fs_type == _FS_TYPE_S3 and not self.fs_path.startswith("s3://"):
            return f"s3://{self.fs_path}"
        return self.fs_path

    def to_fsspec_options(self) -> dict[str, Any]:
        """Build a kwargs dict suitable for ``fsspec.filesystem(fs_type, **options)``.

        .. warning::
            The returned dict contains **plaintext credentials** (``secret``,
            ``token``) unwrapped from their ``SecretStr`` wrappers.  Never log,
            repr, or otherwise expose the return value of this method.  Pass it
            directly to fsspec and let it go out of scope immediately.
        """
        options = dict(self.fs_options)
        builder = _FSSPEC_OPTIONS_BUILDERS.get(self.fs_type)
        if builder is not None:
            options = builder(self, options)
        return options


def create_filesystem(config: FilesystemConfig) -> fsspec.AbstractFileSystem:
    """Build a concrete fsspec filesystem instance from typed config."""
    return fsspec.filesystem(config.fs_type, **_filesystem_options_with_compat(config))


# Backends surface transient failures (timeouts, resets, throttling) as
# OSError subclasses, but so do permanent ones: a missing key, a 403, a path
# that is a directory. Retrying those only delays the same answer.
_PERMANENT_ERRORS: tuple[type[OSError], ...] = (
    FileNotFoundError,
    FileExistsError,
    PermissionError,
    IsADirectoryError,
    NotADirectoryError,
)

# Exponential back-off base: each retry's delay doubles relative to the last.
_BACKOFF_MULTIPLIER = 2


def _with_retry[T](
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    label: str = "operation",
) -> T:
    """Call *fn* with exponential back-off on transient ``OSError``s.

    Args:
        fn: Zero-argument callable to retry.
        max_attempts: Maximum number of attempts (default 3).
        base_delay: Initial delay between attempts in seconds; doubles each retry.
        label: Human-readable label used in log messages.

    Returns:
        The return value of *fn* on the first successful call.

    Raises:
        The last exception raised by *fn* if all attempts fail, or the first
        one if it is permanent (``_PERMANENT_ERRORS``) and retrying is futile.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except _PERMANENT_ERRORS:
            raise
        except OSError as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = base_delay * (_BACKOFF_MULTIPLIER ** (attempt - 1))
            _logger.warning(
                "boti.filesystem: %s failed (attempt %d/%d): %s -- retrying in %.1fs",
                label,
                attempt,
                max_attempts,
                exc,
                delay,
            )
            time.sleep(delay)
    raise last_exc  # type: ignore[misc]


class FilesystemAdapter:
    """Runtime adapter that caches filesystem clients for a named profile.

    Wraps :func:`create_filesystem` with automatic retry on transient I/O errors
    so brief network hiccups do not immediately surface as hard failures.

    Args:
        config: Typed filesystem configuration.
        max_attempts: How many times to attempt a connection before giving up.
            Defaults to 3. Set to 1 to disable retry.
        retry_base_delay: Initial back-off delay in seconds; doubles each retry.
    """

    def __init__(
        self,
        config: FilesystemConfig,
        *,
        max_attempts: int = 3,
        retry_base_delay: float = 0.5,
    ) -> None:
        self.config = config
        self._max_attempts = max_attempts
        self._retry_base_delay = retry_base_delay
        self._lock = threading.RLock()
        self._fs: fsspec.AbstractFileSystem | None = None
        self._arrow_fs: pafs.FileSystem | None = None
        self._arrow_base_path: str | None = None

    @property
    def storage_path(self) -> str:
        return self.config.storage_path

    def get_filesystem(self) -> fsspec.AbstractFileSystem:
        with self._lock:
            if self._fs is None:
                self._fs = _with_retry(
                    lambda: create_filesystem(self.config),
                    max_attempts=self._max_attempts,
                    base_delay=self._retry_base_delay,
                    label=f"connect({self.config.fs_type}:{self.config.fs_path})",
                )
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
            if self._arrow_fs is None or self._arrow_base_path is None:
                self._arrow_fs, self._arrow_base_path = self._build_pyarrow_filesystem()
            return self._arrow_fs, self._arrow_base_path

    def _build_pyarrow_filesystem(self) -> tuple[pafs.FileSystem, str]:
        if self.config.fs_type == _FS_TYPE_FILE:
            return pafs.LocalFileSystem(), self.storage_path.replace("file://", "")

        if self.config.fs_type == _FS_TYPE_S3:
            return (
                pafs.S3FileSystem(**_pyarrow_s3_kwargs_with_compat(self.config)),
                self.storage_path.replace("s3://", "", 1),
            )

        handler = pafs.FSSpecHandler(self.get_filesystem())
        return pafs.PyFileSystem(handler), self.storage_path
