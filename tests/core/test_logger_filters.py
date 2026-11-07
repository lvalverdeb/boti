"""
Tests for PIISecretFilter and the Logger's post-close redaction guarantee (PRD-001).
"""

import logging
import time
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from redaction_corpus import NEGATIVE, POSITIVE, Creds, cycle_case, random_token

from boti.core import Logger
from boti.core.logger import PIISecretFilter, _ClosedSink
from boti.core.logger_filters import _Scan
from boti.core.models import LoggerConfig


def _filtered_record(msg, args=(), extra=None) -> logging.LogRecord:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, msg, args, None)
    if extra:
        record.__dict__.update(extra)
    PIISecretFilter().filter(record)
    return record


def _rendered(record: logging.LogRecord) -> str:
    extras = {
        k: v
        for k, v in record.__dict__.items()
        if k not in PIISecretFilter.LOGRECORD_STDLIB_ATTRS and not k.startswith("_")
    }
    return f"{record.getMessage()} | {extras!r}"


# ---------------------------------------------------------------------------
# Corpus: every positive shape is redacted as an arg and as an extra field;
# every negative shape survives.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("label", "build"), POSITIVE, ids=[c[0] for c in POSITIVE])
def test_corpus_secret_never_survives_as_arg(label, build):
    token = random_token()
    record = _filtered_record("value=%s", (build(token),))
    assert token not in _rendered(record), label


@pytest.mark.parametrize(("label", "build"), POSITIVE, ids=[c[0] for c in POSITIVE])
def test_corpus_secret_never_survives_as_extra(label, build):
    token = random_token()
    record = _filtered_record("safe message", extra={"payload": build(token)})
    assert token not in _rendered(record), label


@pytest.mark.parametrize(("label", "build"), NEGATIVE, ids=[c[0] for c in NEGATIVE])
def test_corpus_benign_value_survives(label, build):
    token = random_token()
    record = _filtered_record("value=%s", (build(token),), extra={"payload": build(token)})
    assert _rendered(record).count(token) == 2, label


def test_corpus_through_real_logger_file_and_console(temp_log_dir, capsys):
    """The corpus must hold end to end: file destination and stdout destination."""
    logger = Logger(LoggerConfig(log_dir=temp_log_dir, logger_name="corpus", log_file="corpus"))
    tokens = []
    for _label, build in POSITIVE:
        token = random_token()
        tokens.append(token)
        logger.warning("value=%s", build(token), extra={"payload": build(token)})
    time.sleep(0.5)
    logger.close()

    file_text = (temp_log_dir / "corpus.log").read_text()
    console_text = capsys.readouterr().out
    for token in tokens:
        assert token not in file_text
        assert token not in console_text
    assert file_text.count("\n") == len(POSITIVE)


# ---------------------------------------------------------------------------
# Message rule (unchanged policy: a mention redacts the whole format string)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("msg", "args"),
    [
        ("token for user %s is %s", ("alice", "sk-supersecret-12345")),
        ("the api_key belonging to tenant acme: %s", ("sk-supersecret-12345",)),
        ("Authorization header value: %s", ("Bearer sk-supersecret-12345",)),
    ],
)
def test_keyword_in_message_redacts_whole_message(msg, args):
    """A keyword in the format string with the secret arriving as a later %s
    arg: adjacency cannot be required, so the whole message goes."""
    record = _filtered_record(msg, args)
    assert record.getMessage() == PIISecretFilter._REDACTED_MESSAGE
    for value in args:
        assert value not in record.getMessage()


def test_compound_keyword_assignment_in_message_is_redacted():
    """``db_password_v2=%s``: the keyword is a suffix of a longer name and the
    secret arrives as the arg — the message is blanked like a bare mention."""
    record = _filtered_record("db_password_v2=%s", ("hunter2",))
    assert record.getMessage() == PIISecretFilter._REDACTED_MESSAGE


def test_keyword_as_prefix_of_an_ordinary_word_is_not_blanked():
    record = _filtered_record("processed %d tokens for the tokenizer", (5,))
    assert record.getMessage() == "processed 5 tokens for the tokenizer"


def test_bare_keyword_mention_is_over_redacted():
    """The documented tradeoff: a harmless mention is redacted too."""
    record = _filtered_record("does not require a password")
    assert record.getMessage() == PIISecretFilter._REDACTED_MESSAGE


def test_url_credentials_in_message_are_scrubbed_without_whole_message_redaction():
    token = random_token()
    record = _filtered_record(f"connecting to postgresql://app:{token}@db:5432/x now")
    assert record.getMessage() == "connecting to postgresql://[REDACTED]@db:5432/x now"


def test_arg_redaction_keeps_the_key_for_context():
    record = _filtered_record("credential=%s", ("api_key=abc-xyz",))
    assert record.getMessage() == "credential=api_key=[REDACTED]"


def test_message_without_keywords_is_untouched():
    record = _filtered_record("processed %d rows for %s", (3, "tenant-a"))
    assert record.getMessage() == "processed 3 rows for tenant-a"


# ---------------------------------------------------------------------------
# Value shapes
# ---------------------------------------------------------------------------


def test_assignment_value_runs_to_a_separator_not_whitespace():
    """Over-redacting the rest of a free-text line is the safe direction."""
    record = _filtered_record("%s", ("password=my pass word, user=bob",))
    assert record.getMessage() == "password=[REDACTED], user=bob"


def test_bearer_value_is_redacted_whole():
    record = _filtered_record("%s", ("authorization: Bearer abc.def.ghi",))
    assert "abc.def.ghi" not in record.getMessage()


def test_bytes_become_str_only_when_redacted():
    untouched = b"payload=plain"
    assert PIISecretFilter()._redact_value(untouched) is untouched
    assert PIISecretFilter()._redact_value(b"password=x") == "password=[REDACTED]"


def test_secret_str_values_are_left_alone():
    secret = SecretStr("hunter2")
    out = PIISecretFilter()._redact_value({"wrapped": secret})
    assert out["wrapped"] is secret
    assert "hunter2" not in repr(out)


def test_cycles_through_dicts_lists_and_objects_terminate():
    token = random_token()
    record = _filtered_record("safe", extra={"payload": cycle_case(token)})
    assert token not in _rendered(record)
    assert record.payload["self"] is record.payload

    class Node:
        def __init__(self) -> None:
            self.me = self
            self.password = token

    record = _filtered_record("safe", extra={"node": Node()})
    assert token not in _rendered(record)


def test_same_plain_object_twice_in_one_record_is_redacted_both_times():
    """Found by the R10 property: the second occurrence of a plain object used
    to be served raw because the cached revisit reported "nothing changed"."""
    token = random_token()
    obj = SimpleNamespace(password=token, user="bob")
    record = _filtered_record("value=%s", (obj,), extra={"payload": obj, "again": [obj, obj]})
    assert token not in _rendered(record)
    assert record.payload == {"password": "[REDACTED]", "user": "bob"}


def test_plain_object_with_revealing_repr_in_a_cycle_does_not_leak():
    token = random_token()

    class Loud:
        def __init__(self) -> None:
            self.me = self
            self.token = token

        def __repr__(self) -> str:
            return f"Loud(token={self.token!r})"

    record = _filtered_record("safe", extra={"node": Loud()})
    assert token not in _rendered(record)


def test_plain_object_without_secrets_is_passed_through_unchanged():
    class Opaque:
        def __init__(self) -> None:
            self.name = "x"
            self.count = 3

    obj = Opaque()
    assert PIISecretFilter()._redact_value(obj) is obj


def test_dataclass_is_rendered_as_redacted_dict_even_when_benign():
    out = PIISecretFilter()._redact_value(Creds(user="bob", password="x"))
    assert out == {"user": "bob", "password": "[REDACTED]"}


def test_object_with_too_many_fields_is_replaced_not_passed_through():
    class Wide:
        pass

    wide = Wide()
    for i in range(201):
        setattr(wide, f"f{i}", i)
    out = PIISecretFilter()._redact_value(wide)
    assert out == "[UNINSPECTABLE Wide: too many fields]"


def test_object_that_explodes_on_inspection_is_replaced():
    class Hostile:
        @property
        def __dict__(self):  # type: ignore[override]
            raise RuntimeError("no")

    out = PIISecretFilter()._redact_value(Hostile())
    assert out == "[UNINSPECTABLE Hostile]"


def test_filter_is_idempotent():
    token = random_token()
    record = _filtered_record("value=%s", ({"db_password": token, "u": "bob"},))
    first = _rendered(record)
    PIISecretFilter().filter(record)
    assert _rendered(record) == first


def test_scan_reports_whether_anything_changed():
    scan = _Scan()
    benign, secret = {"user": "bob"}, {"token": "x"}  # kept alive: visited is keyed by id()
    PIISecretFilter()._redact_value(benign, scan)
    assert scan.changed is False
    PIISecretFilter()._redact_value(secret, scan)
    assert scan.changed is True


def test_temporaries_built_during_a_scan_cannot_alias_later_objects():
    """model_dump() results are temporaries; if one were freed mid-scan a later
    object could reuse its id and be served the stale redacted copy."""
    from redaction_corpus import ApiModel

    models = [ApiModel(endpoint=f"https://h{i}", api_key=f"k{i}") for i in range(50)]
    out = PIISecretFilter()._redact_value(models)
    assert [m["endpoint"] for m in out] == [f"https://h{i}" for i in range(50)]
    assert all(m["api_key"] == "[REDACTED]" for m in out)


# ---------------------------------------------------------------------------
# Exception text
# ---------------------------------------------------------------------------


def test_secrets_in_exception_tracebacks_are_redacted(temp_log_dir):
    logger = Logger(LoggerConfig(log_dir=temp_log_dir, logger_name="exc_test", log_file="exc"))
    try:
        raise RuntimeError("connect failed password=hunter2 for host db1")
    except RuntimeError:
        logger.error("boom", exc_info=True)
    time.sleep(0.5)

    content = (temp_log_dir / "exc.log").read_text()
    assert "hunter2" not in content
    assert "RuntimeError" in content
    assert "for host db1" not in content  # the value runs to the separator; line survives
    assert "password=[REDACTED]" in content


def test_preformatted_exc_text_is_scrubbed():
    record = logging.LogRecord("x", logging.ERROR, __file__, 1, "boom", (), None)
    record.exc_text = "Traceback...\nRuntimeError: token=abc123"
    assert PIISecretFilter().filter(record)
    assert "abc123" not in record.exc_text


# ---------------------------------------------------------------------------
# After Logger.close(): nothing reaches any destination, including lastResort
# ---------------------------------------------------------------------------


def test_nothing_is_emitted_after_close(temp_log_dir, capsys, monkeypatch):
    logger = Logger(LoggerConfig(log_dir=temp_log_dir, logger_name="closed", log_file="closed"))
    bound = logger.bind(req="r1")
    logger.info("before")
    time.sleep(0.3)
    logger.close()

    last_resort_calls: list[logging.LogRecord] = []
    monkeypatch.setattr(logging.lastResort, "handle", last_resort_calls.append)
    logger.error("password=hunter2")
    bound.error("token=abc")
    logging.getLogger("closed").error("raw stdlib user password=x")
    time.sleep(0.3)

    captured = capsys.readouterr()
    assert "hunter2" not in captured.out + captured.err
    assert "abc" not in captured.out + captured.err
    assert "password=x" not in captured.out + captured.err
    assert last_resort_calls == []
    assert "before" in (temp_log_dir / "closed.log").read_text()
    assert "hunter2" not in (temp_log_dir / "closed.log").read_text()


def test_closed_sink_is_replaced_when_the_name_is_reopened(temp_log_dir):
    first = Logger(LoggerConfig(log_dir=temp_log_dir, logger_name="reopen", log_file="a"))
    first.close()
    core = logging.getLogger("reopen")
    assert any(isinstance(h, _ClosedSink) for h in core.handlers)

    second = Logger(LoggerConfig(log_dir=temp_log_dir, logger_name="reopen", log_file="b"))
    assert not any(isinstance(h, _ClosedSink) for h in core.handlers)
    second.info("alive again")
    time.sleep(0.3)
    second.close()
    assert "alive again" in (temp_log_dir / "b.log").read_text()
