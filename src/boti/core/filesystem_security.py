"""SSRF guards for filesystem endpoint validation: private-IP and reserved-hostname checks."""

from __future__ import annotations

import ipaddress

# RFC-1918 and link-local ranges that must not be reachable via fs_endpoint
# unless explicitly whitelisted by the operator.
_PRIVATE_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),  # link-local / AWS IMDS
    ipaddress.ip_network("127.0.0.0/8"),  # loopback
    ipaddress.ip_network("::1/128"),  # IPv6 loopback
    ipaddress.ip_network("fe80::/10"),  # IPv6 link-local
)

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


def _is_private_ip(hostname: str) -> bool:
    """Return True if *hostname* is a private/reserved IP address or a blocked hostname.

    Checks both literal IP strings (e.g. ``169.254.169.254``) and reserved DNS
    names (e.g. ``localhost``).  Hostnames that are not literal IPs and not in
    the reserved-hostname list return False — DNS resolution is intentionally
    avoided here to prevent slow validation and to keep the check deterministic.
    External hostname-based SSRF (e.g. via nip.io) is the operator's
    responsibility to prevent via network egress controls.
    """
    if hostname.lower() in _RESERVED_HOSTNAMES:
        return True
    try:
        addr = ipaddress.ip_address(hostname)
        return any(addr in network for network in _PRIVATE_NETWORKS)
    except ValueError:
        return False
