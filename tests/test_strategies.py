"""Example tests for the pure helpers in tests/strategies.py."""

import ipaddress

import pytest

pytest.importorskip("hypothesis")
from strategies import (  # noqa: E402
    KEY_KEYWORDS,
    VALUE_KEYWORDS,
    contains_keyword,
    ip_spellings,
    is_non_global,
    keyword_spellings,
)


def test_keyword_spellings_expand_optional_separators():
    assert keyword_spellings("api[-_ ]?key") == ["apikey", "api-key", "api_key", "api key"]
    assert keyword_spellings("api_?key|token") == ["apikey", "api_key", "token"]


def test_keyword_lists_are_derived_from_the_implementation():
    assert {"password", "api_key", "apikey", "cookie"} <= set(KEY_KEYWORDS)
    assert "api key" in VALUE_KEYWORDS and "api_key" in KEY_KEYWORDS
    assert "cookie" not in VALUE_KEYWORDS  # message words exclude the key-only names
    assert contains_keyword("Db-Password V2")
    assert contains_keyword("AWS_SECRET_ACCESS_KEY")
    assert not contains_keyword("passport")
    assert not contains_keyword("tokyo")


def test_ipv4_spellings_name_the_same_address():
    spellings = ip_spellings(ipaddress.IPv4Address("127.0.0.1"))
    assert {"127.0.0.1", "2130706433", "0x7f.0x0.0x0.0x1", "127.1", "127.0.1", "127.0.0.1."} <= set(
        spellings
    )
    assert "[::ffff:127.0.0.1]" in spellings
    assert "[::ffff:7f00:1]" in spellings
    assert "10.1" not in ip_spellings(ipaddress.IPv4Address("10.1.0.1"))  # 10.1.0.1 != 10.0.0.1


def test_ipv6_spellings_are_bracketed():
    assert ip_spellings(ipaddress.IPv6Address("::1")) == [
        "[::1]",
        "[0000:0000:0000:0000:0000:0000:0000:0001]",
        "[::1]",
    ]


def test_is_non_global_unwraps_ipv4_mapped():
    assert is_non_global(ipaddress.ip_address("::ffff:10.0.0.1"))
    assert not is_non_global(ipaddress.ip_address("::ffff:8.8.8.8"))
    assert is_non_global(ipaddress.ip_address("224.0.0.1"))
