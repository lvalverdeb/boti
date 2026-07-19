"""Backend-specific fsspec/pyarrow option builders for FilesystemConfig."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from boti.core.filesystem import FilesystemConfig


def _s3_credential_options(config: FilesystemConfig, options: dict[str, Any]) -> dict[str, Any]:
    if config.fs_key:
        options["key"] = config.fs_key
    if config.fs_secret is not None:
        options["secret"] = config.fs_secret.get_secret_value()
    if config.fs_token is not None:
        options["token"] = config.fs_token.get_secret_value()
    return options


def _s3_client_and_config_kwargs(
    config: FilesystemConfig, options: dict[str, Any]
) -> dict[str, Any]:
    client_kwargs: dict[str, Any] = dict(options.get("client_kwargs", {}))
    if config.fs_endpoint:
        client_kwargs["endpoint_url"] = config.fs_endpoint
    if config.fs_region:
        client_kwargs["region_name"] = config.fs_region
    # Inject connect/read timeouts via botocore config if not already set.
    if config.fs_connect_timeout is not None and "connect_timeout" not in client_kwargs:
        client_kwargs["connect_timeout"] = config.fs_connect_timeout
    if config.fs_read_timeout is not None and "read_timeout" not in client_kwargs:
        client_kwargs["read_timeout"] = config.fs_read_timeout
    if client_kwargs:
        options["client_kwargs"] = client_kwargs

    config_kwargs: dict[str, Any] = dict(options.get("config_kwargs", {}))
    if "verify" not in options:
        options["verify"] = config.fs_verify_ssl
    if config_kwargs:
        options["config_kwargs"] = config_kwargs

    return options


def _s3_fsspec_options(config: FilesystemConfig, options: dict[str, Any]) -> dict[str, Any]:
    options = _s3_credential_options(config, options)
    return _s3_client_and_config_kwargs(config, options)


def _http_fsspec_options(config: FilesystemConfig, options: dict[str, Any]) -> dict[str, Any]:
    # aiohttp / requests accept a unified timeout value.
    if "timeout" not in options:
        timeout = config.fs_read_timeout or config.fs_connect_timeout
        if timeout is not None:
            options["timeout"] = timeout
    return options


def _ftp_fsspec_options(config: FilesystemConfig, options: dict[str, Any]) -> dict[str, Any]:
    if config.fs_connect_timeout is not None and "timeout" not in options:
        options["timeout"] = config.fs_connect_timeout
    return options


_FsspecOptionsBuilder = Callable[["FilesystemConfig", dict[str, Any]], dict[str, Any]]

# Keyed by exact fs_type string (validated against _ALLOWED_FS_TYPES), so a
# plain dict lookup is safe and unambiguous — unlike an isinstance-based
# dispatch, there's no subtype/alias overlap to worry about here.
_FSSPEC_OPTIONS_BUILDERS: dict[str, _FsspecOptionsBuilder] = {
    "s3": _s3_fsspec_options,
    "s3a": _s3_fsspec_options,
    "http": _http_fsspec_options,
    "https": _http_fsspec_options,
    "ftp": _ftp_fsspec_options,
    "sftp": _ftp_fsspec_options,
}


def _apply_s3_credential_aliases(normalized: dict[str, Any]) -> None:
    """Map legacy credential key names onto the s3fs names, never overriding."""
    alias_pairs = (
        ("access_key", "key"),
        ("secret_key", "secret"),
        ("session_token", "token"),
    )
    for source, target in alias_pairs:
        if target not in normalized and source in normalized:
            normalized[target] = normalized[source]


def _normalized_s3_client_kwargs(normalized: dict[str, Any]) -> dict[str, Any]:
    """Assemble client_kwargs from endpoint/region/verify aliases; existing keys win."""
    client_kwargs: dict[str, Any] = dict(normalized.get("client_kwargs", {}))
    if "endpoint_url" not in client_kwargs:
        endpoint = normalized.get("endpoint_override") or normalized.get("endpoint")
        if endpoint is not None:
            client_kwargs["endpoint_url"] = endpoint
    if "region_name" not in client_kwargs:
        region = normalized.get("region")
        if region is not None:
            client_kwargs["region_name"] = region

    verify_value = normalized.get("verify")
    if "verify_ssl" in normalized:
        verify_value = normalized.get("verify_ssl")
    # s3fs 2026.3.0 may forward unknown top-level kwargs to AioSession,
    # which does not accept "verify". Keep SSL verification in client kwargs.
    if verify_value is not None and "verify" not in client_kwargs:
        client_kwargs["verify"] = verify_value

    return client_kwargs


def _relocate_botocore_timeouts(
    client_kwargs: dict[str, Any], config_kwargs: dict[str, Any]
) -> None:
    # Some external adapters incorrectly place botocore timeouts in client_kwargs.
    for timeout_key in ("connect_timeout", "read_timeout"):
        if timeout_key not in config_kwargs and timeout_key in client_kwargs:
            config_kwargs[timeout_key] = client_kwargs.pop(timeout_key)


def _normalize_s3_fsspec_options(options: dict[str, Any]) -> dict[str, Any]:
    """Normalize S3 option aliases so downstream callers can pass legacy/new keys."""
    normalized = dict(options)
    _apply_s3_credential_aliases(normalized)

    client_kwargs = _normalized_s3_client_kwargs(normalized)
    config_kwargs: dict[str, Any] = dict(normalized.get("config_kwargs", {}))
    _relocate_botocore_timeouts(client_kwargs, config_kwargs)

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
