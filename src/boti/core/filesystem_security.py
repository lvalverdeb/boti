"""SSRF guards for filesystem endpoint validation: private-IP and reserved-hostname checks."""

from __future__ import annotations

import contextlib
import ipaddress
import re
import socket

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
