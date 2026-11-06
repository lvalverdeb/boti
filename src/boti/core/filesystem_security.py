"""SSRF guards for filesystem configuration.

Everything here is policy about *where* a backend may connect: the backend
allowlist, the private/reserved host rules, the operator endpoint allowlist,
and the scan of ``fs_options`` for every key fsspec or pyarrow would read as a
network target.  ``filesystem.py`` applies these from its validators.
"""

from __future__ import annotations

import contextlib
import ipaddress
import re
import socket
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

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

# Reserved hostnames that must be blocked regardless of IP resolution.
# Covers loopback aliases and common cloud-metadata service names.
_RESERVED_HOSTNAMES: frozenset[str] = frozenset(
    {
        "localhost",
        "ip6-localhost",
        "ip6-loopback",
        "broadcasthost",
        # Cloud metadata services (reachable by name in some environments)
        "metadata",
        "metadata.google.internal",
        "169.254.169.254",  # literal string form as well
    }
)

# Legacy IPv4 spellings that getaddrinfo()/inet_aton() accept but ipaddress rejects:
# "127.1", "0x7f.0.0.1", "2130706433".
_LEGACY_IPV4_RE = re.compile(r"[0-9a-fx.]+", re.IGNORECASE)

# Characters that make URL parsers disagree on where the host ends. ``urlparse``
# reads ``http://127.0.0.1\@s3.amazonaws.com`` as host ``s3.amazonaws.com``, but
# libcurl (pyarrow's S3 client) connects to ``127.0.0.1`` — so a hostname check
# alone is bypassable unless these are refused up front.
_URL_AMBIGUOUS_CHARS_RE = re.compile(r"[\\\s\x00-\x1f\x7f]")

# fs_options keys that the option builders, or fsspec itself, read as a
# storage endpoint; and the keys inside ``client_kwargs`` that the underlying
# HTTP client (botocore, aiohttp) reads as its base URL.
_ENDPOINT_OPTION_KEYS = ("endpoint_url", "endpoint_override", "endpoint")
_CLIENT_KWARGS_ENDPOINT_KEYS = ("endpoint_url", "base_url")
# Wrapper backends (simplecache, filecache, blockcache, zip, tar, …) reach
# another backend through these two keys, so the scan descends into them.
_TARGET_PROTOCOL_KEY = "target_protocol"
_TARGET_OPTIONS_KEY = "target_options"
_MAX_WRAPPER_DEPTH = 8


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


def _validate_fs_type(value: str, *, field: str = "fs_type") -> str:
    """Return *value* stripped, or raise if it is not an allowlisted backend."""
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field} must not be empty.")
    if normalized not in _ALLOWED_FS_TYPES:
        raise ValueError(
            f"{field} '{normalized}' is not an allowed backend. "
            f"Permitted values: {sorted(_ALLOWED_FS_TYPES)}. "
            "To use a custom backend, construct the fsspec filesystem directly."
        )
    return normalized


def _parse_ip(hostname: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    addr: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
    try:
        addr = ipaddress.ip_address(hostname)
    except ValueError:
        if _LEGACY_IPV4_RE.fullmatch(hostname):
            with contextlib.suppress(OSError):
                addr = ipaddress.IPv4Address(socket.inet_aton(hostname))
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    return addr


def _is_private_ip(hostname: str) -> bool:
    """Return True if *hostname* is a non-public IP address or a blocked hostname.

    Blocks every literal IP that is not globally routable (RFC-1918, loopback,
    link-local, CGNAT, ULA, unspecified, multicast, reserved), including
    IPv4-mapped IPv6 and legacy IPv4 spellings such as ``127.1``, and reserved
    DNS names (``localhost``, ``*.localhost``, cloud metadata names) with or
    without a trailing dot.  Other hostnames return False — DNS resolution is
    intentionally avoided here to prevent slow validation and to keep the check
    deterministic.  External hostname-based SSRF (e.g. via nip.io) is the
    operator's responsibility to prevent via network egress controls.
    """
    name = hostname.lower().rstrip(".")
    if name in _RESERVED_HOSTNAMES or name.endswith(".localhost"):
        return True
    addr = _parse_ip(name)
    return addr is not None and (not addr.is_global or addr.is_multicast)


def _reject_ambiguous_url(url: str) -> None:
    """Raise ValueError if different URL parsers could name different hosts for *url*."""
    if _URL_AMBIGUOUS_CHARS_RE.search(url):
        raise ValueError(
            f"endpoint {url!r} contains a backslash, whitespace or control character, "
            "which is rejected because URL parsers disagree on how to read it."
        )
    parsed = urlparse(url)
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(
            f"endpoint '{url}' must not contain a userinfo ('user:pass@') section; "
            "pass credentials via fs_key/fs_secret/fs_token instead."
        )


def _reject_private_host(url: str) -> None:
    """Raise ValueError if *url*'s host is private/reserved and not allowlisted.

    *url* must carry an authority (``scheme://host`` or ``//host``). It is
    first run through ``_reject_ambiguous_url`` so the host this function
    checks is the host every downstream client will actually connect to.
    """
    url = url.strip()
    _reject_ambiguous_url(url)
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    allowlist_key = f"{hostname}:{parsed.port}" if parsed.port else hostname
    if (
        allowlist_key not in ENDPOINT_ALLOWLIST
        and hostname not in ENDPOINT_ALLOWLIST
        and _is_private_ip(hostname)
    ):
        raise ValueError(
            f"endpoint '{url}' resolves to a private or reserved IP address "
            f"({hostname}) which is blocked to prevent SSRF attacks. "
            "Add the host to boti.core.filesystem.ENDPOINT_ALLOWLIST "
            "if this is intentional."
        )


def _validate_endpoint_url(value: str) -> str:
    """Require an http(s) scheme and a non-private host; return the stripped URL."""
    stripped = value.strip()
    scheme = urlparse(stripped).scheme
    # Require an explicit http/https scheme to block file://, ftp://, etc.
    if scheme not in {"http", "https"}:
        raise ValueError(
            f"endpoint scheme '{scheme}' is not allowed. "
            "Only 'http' and 'https' endpoints are supported."
        )
    _reject_private_host(stripped)
    return stripped


def _validate_option_endpoint(value: str) -> None:
    """SSRF-check an endpoint carried in ``fs_options``.

    Unlike ``fs_endpoint``, these are commonly written as bare ``host:port``
    (pyarrow's ``endpoint_override``, MinIO docs) and the option builders
    supply the scheme, so a schemeless value is accepted and only its host is
    checked. A value with a scheme must still be http(s).
    """
    stripped = value.strip()
    if "://" in stripped:
        _validate_endpoint_url(stripped)
    else:
        _reject_private_host(f"//{stripped}")


def _host_authority(host: str, port: Any) -> str:
    """``//host[:port]`` for a bare ``host``/``port`` option pair (IPv6 bracketed)."""
    authority = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"//{authority}:{port}" if port not in (None, "") else f"//{authority}"


def _validate_option_layer(options: Mapping[str, Any]) -> None:
    """SSRF-check the network targets named directly in one options mapping."""
    candidates = [options.get(key) for key in _ENDPOINT_OPTION_KEYS]
    client_kwargs = options.get("client_kwargs")
    if isinstance(client_kwargs, Mapping):
        candidates.extend(client_kwargs.get(key) for key in _CLIENT_KWARGS_ENDPOINT_KEYS)
    for candidate in candidates:
        if isinstance(candidate, str):
            _validate_option_endpoint(candidate)
    # ftp/sftp/hdfs connect to a bare ``host`` (+ ``port``) rather than a URL.
    host = options.get("host")
    if isinstance(host, str):
        _reject_private_host(_host_authority(host.strip(), options.get("port")))


def _validate_fs_options(options: Mapping[str, Any]) -> None:
    """SSRF-check every network target reachable through ``fs_options``.

    Walks the ``target_protocol``/``target_options`` chain that wrapper
    backends use, so a ``simplecache`` over ``s3`` is held to the same
    backend allowlist and endpoint rules as a bare ``s3`` config.
    """
    layer = options
    for _depth in range(_MAX_WRAPPER_DEPTH + 1):
        _validate_option_layer(layer)
        target_protocol = layer.get(_TARGET_PROTOCOL_KEY)
        if isinstance(target_protocol, str):
            _validate_fs_type(target_protocol, field=_TARGET_PROTOCOL_KEY)
        nested = layer.get(_TARGET_OPTIONS_KEY)
        if not isinstance(nested, Mapping):
            return
        layer = nested
    raise ValueError(
        f"fs_options nests more than {_MAX_WRAPPER_DEPTH} levels of "
        f"'{_TARGET_OPTIONS_KEY}'; refusing to validate a wrapper chain that deep."
    )
