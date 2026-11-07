"""Properties of the env-binding, identifier, LoggerConfig-name and sandbox-path
parsers (PRD-003 R14–R18)."""

import os
import re
import tempfile
from pathlib import Path

import pytest

pytest.importorskip("hypothesis")
from hypothesis import example, given  # noqa: E402
from hypothesis import strategies as st  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from boti.core import secure_io as secure_io_module  # noqa: E402
from boti.core.models import LoggerConfig, ResourceConfig  # noqa: E402
from boti.core.secure_io import SecureResource  # noqa: E402
from boti.core.security import (  # noqa: E402
    has_dunder_identifier,
    is_valid_dotted_identifier,
    is_valid_identifier,
    validate_environment_bindings,
)

pytestmark = pytest.mark.property

_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_IDENT_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# Free text alone almost never lands on the interesting boundary cases (an
# identifier followed by a lone newline is the historical re.match bug), so
# the name generators mix in near-identifiers built around the grammar.
_identifier = st.from_regex(r"[A-Za-z_][A-Za-z0-9_]{0,8}", fullmatch=True)
_near_identifiers = st.builds(
    lambda head, sep, tail: f"{head}{sep}{tail}",
    _identifier,
    st.sampled_from(["", ".", "..", "\n", "\r\n", " ", "-", "__", "\x00", "$"]),
    st.one_of(st.just(""), _identifier, st.just("__x__")),
)
names = st.one_of(st.text(), _near_identifiers)


@example("NAME\n", "v")
@example("NAME", "v\n")
@example("NA ME", "v")
@example("NAME", "a\x00b")
@given(names, st.text())
def test_environment_bindings_accept_exactly_the_posix_safe_set(key, value):
    """A binding is applied iff its name matches ``[A-Za-z_][A-Za-z0-9_]*`` and
    its value contains none of NUL, CR, LF, TAB — the characters that would
    let one variable smuggle another or break a child's environment block."""
    acceptable = bool(_ENV_NAME.fullmatch(key)) and not any(c in value for c in "\x00\n\r\t")
    if acceptable:
        assert validate_environment_bindings({key: value}) == {key: value}
    else:
        with pytest.raises(ValueError):
            validate_environment_bindings({key: value})


@example("name\n")  # the historical re.match bug: `$` matches before a trailing newline
@example("a..b")
@example("x.__class__")
@example("a__b")
@given(names)
def test_identifier_validators_agree_with_the_grammar_and_each_other(text):
    """``is_valid_identifier`` is the ASCII grammar with no newline leniency;
    a dotted identifier is a non-empty dot-join of valid identifiers; a dunder
    identifier is a whole token wrapped in ``__``, never a bare substring."""
    assert is_valid_identifier(text) == bool(_IDENT_TOKEN.fullmatch(text))
    assert is_valid_dotted_identifier(text) == (
        text != "" and all(is_valid_identifier(part) for part in text.split("."))
    )
    assert has_dunder_identifier(text) == any(
        tok.startswith("__") and tok.endswith("__") for tok in _IDENT_TOKEN.findall(text)
    )


@pytest.fixture(scope="module")
def log_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("props-logs")


@given(st.text(min_size=1), st.one_of(st.none(), st.text()))
def test_logger_names_cannot_escape_the_log_directory(log_dir, logger_name, log_file):
    """For any logger_name/log_file text, either LoggerConfig rejects it or the
    effective ``<name>.log`` resolves to a direct child of log_dir."""
    try:
        config = LoggerConfig(log_dir=log_dir, logger_name=logger_name, log_file=log_file)
    except ValidationError:
        return
    effective = config.log_file or config.logger_name
    assert Path(log_dir, f"{effective}.log").resolve().parent == Path(log_dir).resolve()


_segments = st.from_regex(r"[A-Za-z0-9_]{1,8}", fullmatch=True)


@given(
    st.from_regex(r"[a-z0-9_]{1,6}", fullmatch=True), st.lists(_segments, min_size=1, max_size=4)
)
def test_containment_is_decided_by_path_components_not_string_prefix(sibling_suffix, parts):
    """A sibling whose string form merely starts with the root's path
    (``/r`` vs ``/r_evil``) contains nothing the sandbox allows."""
    with tempfile.TemporaryDirectory() as tmp:
        fake_temp = Path(tmp, "faketemp")
        fake_temp.mkdir()
        root = Path(tmp, "root").resolve()
        root.mkdir()
        original = secure_io_module.tempfile.gettempdir
        secure_io_module.tempfile.gettempdir = lambda: str(fake_temp)  # type: ignore[assignment]
        try:
            res = SecureResource(config=ResourceConfig(project_root=root, skip_logger=True))
        finally:
            secure_io_module.tempfile.gettempdir = original  # type: ignore[assignment]
        with res:
            sibling = Path(str(root) + sibling_suffix).joinpath(*parts)
            assert res._sandbox_root_for(sibling) is None
            assert res._sandbox_root_for(root.joinpath(*parts)) == root


@given(st.lists(_segments, min_size=1, max_size=4), st.sampled_from(["root", "/", "temp"]))
def test_relative_paths_anchor_to_the_root_regardless_of_cwd(parts, cwd_kind):
    """For any relative path without ``..``, get_secure_path(p) is
    (project_root / p).resolve() whether the process runs in the root, in
    ``/`` or in the temp directory."""
    relative = Path(*parts)
    original_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp, "root").resolve()
        root.mkdir()
        os.chdir({"root": root, "/": "/", "temp": tmp}[cwd_kind])
        try:
            with SecureResource(config=ResourceConfig(project_root=root, skip_logger=True)) as res:
                assert res.get_secure_path(relative) == (root / relative).resolve()
                assert res.get_secure_path(str(relative)) == (root / relative).resolve()
        finally:
            os.chdir(original_cwd)
