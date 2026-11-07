"""Properties of the SSRF host rules (PRD-003 R4–R9).

Each property states the security contract, not the implementation; the
generators live in tests/strategies.py.
"""

from urllib.parse import urlparse

import pytest

pytest.importorskip("hypothesis")
from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402
from strategies import global_urls, hostnames, is_non_global, non_global_urls, ports  # noqa: E402

from boti.core import filesystem_security as fsec  # noqa: E402

pytestmark = pytest.mark.property


@given(non_global_urls)
def test_non_global_addresses_are_rejected_in_every_spelling(case):
    """Any literal address that is not globally routable — loopback, RFC-1918,
    link-local, CGNAT, ULA, unspecified, multicast, reserved — is rejected
    however it is spelled: dotted, decimal, hex octets, inet_aton short forms,
    trailing dot, IPv4-mapped IPv6, with or without scheme and port."""
    _addr, url = case
    with pytest.raises(ValueError, match="private or reserved"):
        fsec._reject_private_host(url)


@given(global_urls)
def test_global_addresses_are_accepted_in_every_spelling(case):
    """The guard must not degrade into "reject everything": a globally
    routable literal address is accepted in all the same spellings. Together
    with the previous property this pins the exact boundary."""
    _addr, url = case
    fsec._reject_private_host(url)


@given(st.ip_addresses(v=4).filter(is_non_global), ports, ports)
def test_allowlist_is_exact(addr, port, other_port):
    """Allowlisting a bare host admits it on any port; allowlisting host:port
    admits only that port. The allowlist widens nothing else."""
    host = str(addr)
    saved = set(fsec.ENDPOINT_ALLOWLIST)
    try:
        fsec.ENDPOINT_ALLOWLIST.clear()
        fsec.ENDPOINT_ALLOWLIST.add(host)
        fsec._reject_private_host(f"//{host}")
        fsec._reject_private_host(f"//{host}:{port}")

        fsec.ENDPOINT_ALLOWLIST.clear()
        fsec.ENDPOINT_ALLOWLIST.add(f"{host}:{port}")
        fsec._reject_private_host(f"//{host}:{port}")
        with pytest.raises(ValueError):
            fsec._reject_private_host(f"//{host}")
        if other_port != port:
            with pytest.raises(ValueError):
                fsec._reject_private_host(f"//{host}:{other_port}")
    finally:
        fsec.ENDPOINT_ALLOWLIST.clear()
        fsec.ENDPOINT_ALLOWLIST.update(saved)


_AMBIGUOUS = st.sampled_from(["\\", " ", "\t", "\n", "\r", "\x00", "\x1f", "\x7f"])


@given(hostnames, _AMBIGUOUS, st.integers(0, 12), st.booleans())
def test_parser_differential_inputs_never_reach_the_host_check(host, char, position, userinfo):
    """A URL containing a character on which parsers disagree about where the
    host ends (backslash, whitespace, control characters), or a userinfo
    section, is refused up front — before any host is extracted — so the host
    boti checks is the host every downstream client connects to."""
    body = host[:position] + char + host[position:]
    url = f"//{'u:p@' if userinfo else ''}{body}/path"
    calls: list[str] = []
    original = fsec._is_private_ip
    fsec._is_private_ip = lambda h: calls.append(h) or original(h)  # type: ignore[assignment]
    try:
        with pytest.raises(ValueError, match="parsers disagree|userinfo"):
            fsec._reject_private_host(url)
    finally:
        fsec._is_private_ip = original
    assert calls == []


@given(hostnames)
def test_userinfo_alone_is_refused(host):
    """Credentials belong in fs_key/fs_secret/fs_token, never in the URL."""
    with pytest.raises(ValueError, match="userinfo"):
        fsec._reject_private_host(f"//user:pass@{host}/")


@given(st.integers(0, 12))
def test_wrapper_chains_are_rejected_at_any_depth_without_recursion(depth):
    """A private endpoint hidden under *depth* layers of target_options is
    rejected — for the endpoint while the chain is within the depth cap, for
    the depth beyond it — and never by RecursionError."""
    options = {"endpoint_url": "http://127.0.0.1:9000"}
    for _ in range(depth):
        options = {"target_protocol": "simplecache", "target_options": options}
    with pytest.raises(ValueError):
        fsec._validate_fs_options(options)


@given(
    st.one_of(hostnames, st.ip_addresses(v=6).map(str)),
    st.one_of(st.none(), ports),
)
def test_host_authority_round_trips_through_urlparse(host, port):
    """The authority built for a bare host/port option pair parses back to the
    same host (lower-cased, IPv6 unbracketed) and port, so the check runs
    against what the backend will actually dial."""
    parsed = urlparse(fsec._host_authority(host, port))
    assert parsed.hostname == host.lower()
    assert parsed.port == port
