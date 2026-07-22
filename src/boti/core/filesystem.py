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
from urllib.parse import urlparse

import fsspec
import pyarrow.fs as pafs
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from boti.core.filesystem_options import (
    _FSSPEC_OPTIONS_BUILDERS,
    _filesystem_options_with_compat,
    _pyarrow_s3_kwargs_with_compat,
)
from boti.core.filesystem_security import _is_private_ip
from boti.core.settings import FilesystemSettings, load_prefixed_model

_logger = logging.getLogger(__name__)

__all__ = [
    "FilesystemConfig",
    "FilesystemAdapter",
    "create_filesystem",
    "add_endpoint_to_allowlist",
]

# Explicit allowlist of fsspec backend identifiers that are safe to instantiate
# from user-supplied or environment-backed configuration.  Arbitrary backends
# (e.g. custom handlers, ssh, smb) must be constructed directly via fsspec.
_ALLOWED_FS_TYPES: frozenset[str] = frozenset(
    {
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
    }
)

# Operator-configurable allowlist for storage endpoints.  Use
# ``add_endpoint_to_allowlist("minio.internal:9000")`` at startup to permit
# specific internal endpoints that would otherwise be blocked by the private-IP
# guard.  Do NOT mutate this set directly from multiple threads.
ENDPOINT_ALLOWLIST: set[str] = set()


def add_endpoint_to_allowlist(*hosts: str) -> None:
    """Add one or more host[:port] strings to the endpoint allowlist.

    Call this once at application startup before any ``FilesystemConfig`` is
    constructed.  Thread-safe for read-after-write scenarios where callers only
    add entries before the first config validation.

    Example::

        from boti.core.filesystem import add_endpoint_to_allowlist
        add_endpoint_to_allowlist("minio.internal:9000", "minio.internal")
    """
    for host in hosts:
        ENDPOINT_ALLOWLIST.add(host.strip())


def _allowlist_endpoint_from_url(endpoint: str) -> None:
    """Allowlist the host[:port] parsed from *endpoint*.

    Used by ``FilesystemConfig.from_env_prefix(..., trust_env_endpoint=True)``
    so a deployment-trusted endpoint sourced from environment config doesn't
    require a separate manual ``add_endpoint_to_allowlist()`` call.
    """
    parsed = urlparse(endpoint.strip())
    hostname = parsed.hostname or ""
    if not hostname:
        return
    allowlist_key = f"{hostname}:{parsed.port}" if parsed.port else hostname
    add_endpoint_to_allowlist(allowlist_key, hostname)


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

    model_config = ConfigDict(arbitrary_types_allowed=True)

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
        if value is None:
            return None
        stripped = value.strip()
        parsed = urlparse(stripped)

        # Require an explicit http/https scheme to block file://, ftp://, etc.
        if parsed.scheme not in {"http", "https"}:
            raise ValueError(
                f"fs_endpoint scheme '{parsed.scheme}' is not allowed. "
                "Only 'http' and 'https' endpoints are supported."
            )

        hostname = parsed.hostname or ""
        # Build the host:port key used for allowlist lookup
        allowlist_key = f"{hostname}:{parsed.port}" if parsed.port else hostname

        if (
            allowlist_key not in ENDPOINT_ALLOWLIST
            and hostname not in ENDPOINT_ALLOWLIST
            and _is_private_ip(hostname)
        ):
            raise ValueError(
                f"fs_endpoint '{stripped}' resolves to a private or reserved IP address "
                f"({hostname}) which is blocked to prevent SSRF attacks. "
                "Add the host to boti.core.filesystem.ENDPOINT_ALLOWLIST "
                "if this is intentional."
            )

        return stripped

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


# Transient error types that are safe to retry across all backends.
_RETRYABLE_ERRORS: tuple[type[Exception], ...] = (
    OSError,
    TimeoutError,
    ConnectionError,
)


def _with_retry[T](
    fn: Callable[[], T],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    label: str = "operation",
) -> T:
    """Call *fn* with exponential back-off on transient errors.

    Args:
        fn: Zero-argument callable to retry.
        max_attempts: Maximum number of attempts (default 3).
        base_delay: Initial delay between attempts in seconds; doubles each retry.
        label: Human-readable label used in log messages.

    Returns:
        The return value of *fn* on the first successful call.

    Raises:
        The last exception raised by *fn* if all attempts fail.
    """
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except _RETRYABLE_ERRORS as exc:
            last_exc = exc
            if attempt == max_attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
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
        if self.config.fs_type == "file":
            return pafs.LocalFileSystem(), self.storage_path.replace("file://", "")

        if self.config.fs_type == "s3":
            return (
                pafs.S3FileSystem(**_pyarrow_s3_kwargs_with_compat(self.config)),
                self.storage_path.replace("s3://", "", 1),
            )

        handler = pafs.FSSpecHandler(self.get_filesystem())
        return pafs.PyFileSystem(handler), self.storage_path
