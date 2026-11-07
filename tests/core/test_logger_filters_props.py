"""Properties of PIISecretFilter (PRD-003 R10–R13).

The generators share the filter's keyword vocabulary (tests/strategies.py),
so a keyword added to the implementation is exercised here automatically.
"""

import logging

import pytest

pytest.importorskip("hypothesis")
from hypothesis import given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402
from strategies import (  # noqa: E402
    benign_identifiers,
    benign_keys,
    hostnames,
    planted,
    ports,
    sensitive_keys,
    tokens,
)

from boti.core.logger_filters import PIISecretFilter  # noqa: E402

pytestmark = pytest.mark.property

_SCHEMES = st.sampled_from(["http", "https", "postgresql", "mysql", "redis", "amqp", "s3", "ftp"])


def _record(msg: str, args: tuple = (), extra: dict | None = None) -> logging.LogRecord:
    record = logging.LogRecord("p", logging.INFO, __file__, 1, msg, args, None)
    if extra:
        record.__dict__.update(extra)
    return record


def _render(record: logging.LogRecord) -> str:
    extras = {
        k: v
        for k, v in record.__dict__.items()
        if k not in PIISecretFilter.LOGRECORD_STDLIB_ATTRS and not k.startswith("_")
    }
    return f"{record.getMessage()} | {extras!r}"


def _filtered(msg: str, args: tuple = (), extra: dict | None = None) -> str:
    record = _record(msg, args, extra)
    PIISecretFilter().filter(record)
    return _render(record)


@given(st.data(), tokens)
def test_a_secret_under_a_sensitive_key_never_survives(data, token):
    """Whatever the container (dict, list, tuple, dataclass, SimpleNamespace,
    pydantic model, JSON text, bytes of JSON; nested up to depth 4) and
    however the key is spelled (prefix/suffix, `_`/`-`/space, any case), a
    value under a key that contains a sensitive keyword does not reach the
    rendered record — neither as a %-arg nor as an extra field."""
    structure = data.draw(planted(sensitive_keys(), token))
    assert token not in _filtered("value=%s", (structure,))
    assert token not in _filtered("safe", extra={"payload": structure})


@given(st.data(), tokens)
def test_a_value_under_a_keyword_free_key_survives(data, token):
    """The converse guards against a filter that redacts everything: the same
    shapes with a key containing no keyword keep their value verbatim."""
    structure = data.draw(planted(benign_keys, token))
    assert token in _filtered("value=%s", (structure,))
    assert token in _filtered("safe", extra={"payload": structure})


@given(
    _SCHEMES,
    benign_identifiers,
    tokens,
    hostnames.filter(lambda h: "tok" not in h.lower()),
    st.one_of(st.none(), ports),
)
def test_url_credentials_never_survive_but_the_host_does(scheme, user, secret, host, port):
    """A URL carrying ``user:secret@`` loses the secret and keeps the host —
    as the message itself, as a %-arg, and inside a dict value."""
    url = f"{scheme}://{user}:{secret}@{host}{'' if port is None else f':{port}'}/db"
    for rendered in (
        _filtered(url),
        _filtered("connecting to %s", (url,)),
        _filtered("safe", extra={"dsn": url}),
    ):
        assert secret not in rendered
        assert host in rendered


@given(st.data(), tokens)
def test_filtering_is_idempotent(data, token):
    """Filtering an already filtered record changes nothing: the redacted
    text contains no new matches and no replacement is replaced again."""
    structure = data.draw(planted(sensitive_keys(), token))
    record = _record("value=%s", (structure,), extra={"payload": structure})
    PIISecretFilter().filter(record)
    once = _render(record)
    PIISecretFilter().filter(record)
    assert _render(record) == once
