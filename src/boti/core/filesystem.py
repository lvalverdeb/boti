"""
Typed filesystem configuration and runtime adapters.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar
from urllib.parse import urlparse

_logger = logging.getLogger(__name__)
_T = TypeVar("_T")

__all__ = [
    "FilesystemConfig",
    "FilesystemAdapter",
    "create_filesystem",
]

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

# RFC-1918 and link-local ranges that must not be reachable via fs_endpoint
# unless explicitly whitelisted by the operator.
_PRIVATE_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),   # link-local / AWS IMDS
    ipaddress.ip_network("127.0.0.0/8"),      # loopback
    ipaddress.ip_network("::1/128"),          # IPv6 loopback
    ipaddress.ip_network("fe80::/10"),        # IPv6 link-local
)

# Operator-configurable allowlist for storage endpoints.  Populate at startup
# (e.g. ``ENDPOINT_ALLOWLIST |= {"minio.internal:9000"}``) to permit specific
# internal endpoints that would otherwise be blocked by the private-IP guard.
ENDPOINT_ALLOWLIST: frozenset[str] = frozenset()


def _is_private_ip(hostname: str) -> bool:
    """Return True if *hostname* resolves to a private/reserved IP address."""
    try:
        addr = ipaddress.ip_address(hostname)
        return any(addr in network for network in _PRIVATE_NETWORKS)
    except ValueError:
        return False


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
    fs_connect_timeout: Optional[float] = Field(
        default=10.0,
        description="TCP connect timeout in seconds for remote backends. None disables the timeout.",
    )
    fs_read_timeout: Optional[float] = Field(
        default=30.0,
        description="Socket read timeout in seconds for remote backends. None disables the timeout.",
    )
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

    @field_validator("fs_endpoint")
    @classmethod
    def validate_fs_endpoint(cls, value: Optional[str]) -> Optional[str]:
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

        if allowlist_key not in ENDPOINT_ALLOWLIST and hostname not in ENDPOINT_ALLOWLIST:
            if _is_private_ip(hostname):
                raise ValueError(
                    f"fs_endpoint '{stripped}' resolves to a private or reserved IP address "
                    f"({hostname}) which is blocked to prevent SSRF attacks. "
                    "Add the host to boti.core.filesystem.ENDPOINT_ALLOWLIST if this is intentional."
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
        options = dict(self.fs_options)

        if self.fs_type in {"s3", "s3a"}:
            if self.fs_key:
                options["key"] = self.fs_key
            if self.fs_secret is not None:
                options["secret"] = self.fs_secret.get_secret_value()
            if self.fs_token is not None:
                options["token"] = self.fs_token.get_secret_value()

            client_kwargs: dict[str, Any] = dict(options.get("client_kwargs", {}))
            if self.fs_endpoint:
                client_kwargs["endpoint_url"] = self.fs_endpoint
            if self.fs_region:
                client_kwargs["region_name"] = self.fs_region
            # Inject connect/read timeouts via botocore config if not already set.
            if self.fs_connect_timeout is not None and "connect_timeout" not in client_kwargs:
                client_kwargs["connect_timeout"] = self.fs_connect_timeout
            if self.fs_read_timeout is not None and "read_timeout" not in client_kwargs:
                client_kwargs["read_timeout"] = self.fs_read_timeout
            if client_kwargs:
                options["client_kwargs"] = client_kwargs

            config_kwargs: dict[str, Any] = dict(options.get("config_kwargs", {}))
            if "verify" not in options:
                options["verify"] = self.fs_verify_ssl
            if config_kwargs:
                options["config_kwargs"] = config_kwargs

        elif self.fs_type in {"http", "https"}:
            # aiohttp / requests accept a unified timeout value.
            if "timeout" not in options:
                timeout = self.fs_read_timeout or self.fs_connect_timeout
                if timeout is not None:
                    options["timeout"] = timeout

        elif self.fs_type in {"ftp", "sftp"}:
            if self.fs_connect_timeout is not None and "timeout" not in options:
                options["timeout"] = self.fs_connect_timeout

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


def _normalize_s3_fsspec_options(options: dict[str, Any]) -> dict[str, Any]:
    """Normalize S3 option aliases so downstream callers can pass legacy/new keys."""
    normalized = dict(options)

    alias_pairs = (
        ("access_key", "key"),
        ("secret_key", "secret"),
        ("session_token", "token"),
    )
    for source, target in alias_pairs:
        if target not in normalized and source in normalized:
            normalized[target] = normalized[source]

    verify_value = normalized.get("verify")
    if "verify_ssl" in normalized:
        verify_value = normalized.get("verify_ssl")

    client_kwargs: dict[str, Any] = dict(normalized.get("client_kwargs", {}))
    if "endpoint_url" not in client_kwargs:
        endpoint = normalized.get("endpoint_override") or normalized.get("endpoint")
        if endpoint is not None:
            client_kwargs["endpoint_url"] = endpoint
    if "region_name" not in client_kwargs:
        region = normalized.get("region")
        if region is not None:
            client_kwargs["region_name"] = region
    # s3fs 2026.3.0 may forward unknown top-level kwargs to AioSession,
    # which does not accept "verify". Keep SSL verification in client kwargs.
    if verify_value is not None and "verify" not in client_kwargs:
        client_kwargs["verify"] = verify_value

    config_kwargs: dict[str, Any] = dict(normalized.get("config_kwargs", {}))
    # Some external adapters incorrectly place botocore timeouts in client_kwargs.
    for timeout_key in ("connect_timeout", "read_timeout"):
        if timeout_key not in config_kwargs and timeout_key in client_kwargs:
            config_kwargs[timeout_key] = client_kwargs.pop(timeout_key)

    if client_kwargs:
        normalized["client_kwargs"] = client_kwargs
    else:
        normalized.pop("client_kwargs", None)

    if config_kwargs:
        normalized["config_kwargs"] = config_kwargs
    else:
        normalized.pop("config_kwargs", None)

    normalized.pop("verify", None)
    normalized.pop("verify_ssl", None)

    return normalized


def _filesystem_options_with_compat(config: FilesystemConfig) -> dict[str, Any]:
    options = config.to_fsspec_options()
    if config.fs_type in {"s3", "s3a"}:
        return _normalize_s3_fsspec_options(options)
    return options


def _pyarrow_s3_kwargs_with_compat(config: FilesystemConfig) -> dict[str, Any]:
    normalized = _normalize_s3_fsspec_options(config.to_fsspec_options())
    client_kwargs = dict(normalized.get("client_kwargs", {}))

    access_key = config.fs_key or normalized.get("key")
    secret_key = None if config.fs_secret is None else config.fs_secret.get_secret_value()
    if secret_key is None:
        secret_key = normalized.get("secret")
    session_token = None if config.fs_token is None else config.fs_token.get_secret_value()
    if session_token is None:
        session_token = normalized.get("token")

    region = config.fs_region or client_kwargs.get("region_name")
    endpoint_override = config.fs_endpoint or client_kwargs.get("endpoint_url")

    arrow_kwargs: dict[str, Any] = {
        "access_key": access_key,
        "secret_key": secret_key,
        "session_token": session_token,
        "region": region,
    }
    if endpoint_override:
        arrow_kwargs["endpoint_override"] = endpoint_override
        arrow_kwargs["scheme"] = "https" if endpoint_override.startswith("https://") else "http"
    return {k: v for k, v in arrow_kwargs.items() if v is not None}


def _with_retry(
    fn: Callable[[], _T],
    *,
    max_attempts: int = 3,
    base_delay: float = 0.5,
    label: str = "operation",
) -> _T:
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
        self._fs: Optional[fsspec.AbstractFileSystem] = None
        self._arrow_fs: Optional[pafs.FileSystem] = None
        self._arrow_base_path: Optional[str] = None

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
            if self._arrow_fs is not None and self._arrow_base_path is not None:
                return self._arrow_fs, self._arrow_base_path

            if self.config.fs_type == "file":
                self._arrow_fs = pafs.LocalFileSystem()
                self._arrow_base_path = self.storage_path.replace("file://", "")
                return self._arrow_fs, self._arrow_base_path

            if self.config.fs_type == "s3":
                self._arrow_fs = pafs.S3FileSystem(**_pyarrow_s3_kwargs_with_compat(self.config))
                self._arrow_base_path = self.storage_path.replace("s3://", "", 1)
                return self._arrow_fs, self._arrow_base_path

            handler = pafs.FSSpecHandler(self.get_filesystem())
            self._arrow_fs = pafs.PyFileSystem(handler)
            self._arrow_base_path = self.storage_path
            return self._arrow_fs, self._arrow_base_path
