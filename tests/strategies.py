"""Hypothesis strategies shared by the property tests (PRD-003).

The keyword vocabulary is imported from the implementation, so the generators
and the filter cannot drift apart; ``keyword_spellings`` turns the regex
alternation into literal spellings. ``ip_spellings`` is a pure function with
its own example tests in ``test_strategies.py``.
"""

from __future__ import annotations

import dataclasses
import ipaddress
import json
import keyword
import re
from types import SimpleNamespace
from typing import Any

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import strategies as st  # noqa: E402
from pydantic import create_model  # noqa: E402

from boti.core.logger_filters import _KEY_KEYWORDS, _VALUE_KEYWORDS  # noqa: E402

# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------

_OPTIONAL_SEPARATOR_RE = re.compile(r"\[-_ \]\?|_\?")


def keyword_spellings(alternation: str) -> list[str]:
    """Literal spellings of a keyword alternation.

    ``api[-_ ]?key`` -> ``apikey``, ``api-key``, ``api_key``, ``api key``;
    ``api_?key`` -> ``apikey``, ``api_key``. Anything else in the pattern
    must be literal text, which is asserted.
    """
    spellings: list[str] = []
    for alternative in alternation.split("|"):
        parts = _OPTIONAL_SEPARATOR_RE.split(alternative)
        separators = _OPTIONAL_SEPARATOR_RE.findall(alternative)
        assert re.escape("".join(parts)) == "".join(parts), alternative
        expanded = [parts[0]]
        for separator, part in zip(separators, parts[1:], strict=True):
            options = ["", "-", "_", " "] if separator.startswith("[") else ["", "_"]
            expanded = [head + option + part for head in expanded for option in options]
        spellings.extend(expanded)
    return spellings


KEY_KEYWORDS = keyword_spellings(_KEY_KEYWORDS)
VALUE_KEYWORDS = keyword_spellings(_VALUE_KEYWORDS)


def normalise_key(key: str) -> str:
    return key.lower().replace("-", "_").replace(" ", "_")


def contains_keyword(name: str) -> bool:
    normalised = normalise_key(name)
    return any(spelling in normalised for spelling in KEY_KEYWORDS)


# ---------------------------------------------------------------------------
# Tokens, identifiers, keys
# ---------------------------------------------------------------------------

# Hex only: no regex metacharacters, and no separator the assignment rule
# treats as a value terminator — so a surviving token is a real leak.
tokens = st.text("0123456789abcdef", min_size=20, max_size=20)

identifiers = st.from_regex(r"[a-z][a-z0-9]{0,7}", fullmatch=True).filter(
    lambda s: not keyword.iskeyword(s) and s != "model"  # dataclass / pydantic field names
)

benign_identifiers = identifiers.filter(lambda s: not contains_keyword(s))


def _apply_case(text: str, case: str) -> str:
    return {"lower": text.lower, "upper": text.upper, "title": text.title}[case]()


def sensitive_keys(separators: str = "_- ") -> st.SearchStrategy[str]:
    """``[prefix][sep]keyword[sep][suffix]`` in random case: ``Db_Password_V2``,
    ``x-api-key``, ``AWS SECRET ACCESS KEY``."""
    return st.builds(
        lambda prefix, kw, suffix, sep, case: _apply_case(
            sep.join(part for part in (prefix, kw, suffix) if part), case
        ),
        prefix=st.one_of(st.just(""), identifiers),
        kw=st.sampled_from(KEY_KEYWORDS),
        suffix=st.one_of(st.just(""), identifiers),
        sep=st.sampled_from(list(separators)),
        case=st.sampled_from(["lower", "upper", "title"]),
    )


benign_keys = st.builds(
    lambda a, b, sep: sep.join(part for part in (a, b) if part),
    a=benign_identifiers,
    b=st.one_of(st.just(""), benign_identifiers),
    sep=st.sampled_from(["_", "-", " "]),
).filter(lambda k: not contains_keyword(k))


# ---------------------------------------------------------------------------
# Container shapes with one (key, token) pair planted at a leaf
# ---------------------------------------------------------------------------

LEAF_KINDS = ("dict", "namespace", "dataclass", "pydantic")
WRAPPERS = ("list", "tuple", "dict", "json", "bytes")


def _attribute_name(key: str) -> str:
    name = re.sub(r"[^0-9A-Za-z_]", "_", key)
    return name if name[0].isalpha() else f"k_{name}"


def _leaf(kind: str, key: str, value: str) -> Any:
    if kind == "dict":
        return {key: value}
    if kind == "namespace":
        return SimpleNamespace(**{key: value})
    name = _attribute_name(key)
    if kind == "dataclass":
        return dataclasses.make_dataclass("Leaf", [(name, str)])(value)
    return create_model("Leaf", **{name: (str, ...)})(**{name: value})


@st.composite
def planted(draw: st.DrawFn, keys: st.SearchStrategy[str], token: str) -> Any:
    """A structure of depth <= 4 with ``(key, token)`` at a leaf.

    Text wrappers (a JSON string, or bytes of JSON) can only wrap dicts, and
    inside text only the *value* rule applies, which reads a key up to its
    delimiter as ``keyword[\\w-]*`` — so keys under a text wrapper use ``_``/``-``
    separators. Everything else is open.
    """
    wrappers = draw(st.lists(st.sampled_from(WRAPPERS), max_size=3))
    textual = "json" in wrappers or "bytes" in wrappers
    kind = "dict" if textual else draw(st.sampled_from(LEAF_KINDS))
    key = draw(keys)
    if textual:
        key = key.replace(" ", "_")
    value: Any = _leaf(kind, key, token)
    jsonable = kind == "dict"
    for wrapper in wrappers:
        if wrapper == "list":
            value = [value]
        elif wrapper == "tuple":
            value = (value,)
        elif wrapper == "dict":
            value = {"outer": value}
        elif wrapper == "json" and jsonable:
            value = json.dumps(value)
        elif wrapper == "bytes" and jsonable:
            value = json.dumps(value).encode()
            jsonable = False
    return value


# ---------------------------------------------------------------------------
# Hosts and IP spellings
# ---------------------------------------------------------------------------

hostnames = st.from_regex(
    r"[A-Za-z0-9]([A-Za-z0-9-]{0,10}[A-Za-z0-9])?(\.[A-Za-z0-9]{1,5}){0,3}", fullmatch=True
)
ports = st.integers(1, 65535)

IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def effective_address(addr: IPAddress) -> IPAddress:
    """The address a client would connect to: IPv4-mapped IPv6 unwraps to its IPv4."""
    mapped = getattr(addr, "ipv4_mapped", None)
    return mapped if mapped is not None else addr


def is_non_global(addr: IPAddress) -> bool:
    effective = effective_address(addr)
    return not effective.is_global or effective.is_multicast


def ip_spellings(addr: IPAddress) -> list[str]:
    """Every spelling of *addr* a URL host can carry that names the same address.

    IPv4: dotted, decimal integer, hex octets, the short forms ``inet_aton``
    accepts when the dropped octets are zero (``127.1``, ``10.0.1``), trailing
    dot, and IPv4-mapped IPv6 in both dotted and hex notation (bracketed).
    IPv6: compressed, exploded and upper-case, bracketed.
    """
    if isinstance(addr, ipaddress.IPv4Address):
        a, b, c, d = addr.packed
        spellings = [
            str(addr),
            str(int(addr)),
            ".".join(f"0x{octet:x}" for octet in addr.packed),
            f"{addr}.",
            f"[::ffff:{addr}]",
            f"[::ffff:{(a << 8) | b:x}:{(c << 8) | d:x}]",
            f"[::FFFF:{addr}]",
        ]
        if c == 0:
            spellings.append(f"{a}.{b}.{d}")
        if b == 0 and c == 0:
            spellings.append(f"{a}.{d}")
        return spellings
    return [f"[{addr}]", f"[{addr.exploded}]", f"[{str(addr).upper()}]"]


URL_FORMS = ("//{}", "http://{}", "https://{}", "//{}:8080")


@st.composite
def spelled_urls(draw: st.DrawFn, addresses: st.SearchStrategy[IPAddress]) -> tuple[IPAddress, str]:
    addr = draw(addresses)
    spelling = draw(st.sampled_from(ip_spellings(addr)))
    form = draw(st.sampled_from(URL_FORMS))
    return addr, form.format(spelling)


non_global_urls = spelled_urls(st.ip_addresses().filter(is_non_global))
global_urls = spelled_urls(st.ip_addresses().filter(lambda a: not is_non_global(a)))
