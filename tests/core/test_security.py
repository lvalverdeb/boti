"""
Tests for security and path sandboxing.
"""

import logging
import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from boti.core import ProjectService, SecureResource
from boti.core import project as project_module
from boti.core import secure_io as secure_io_module
from boti.core.models import ResourceConfig
from boti.core.security import (
    has_dunder_identifier,
    is_valid_dotted_identifier,
    is_valid_identifier,
)


def test_project_root_detection(temp_project_root):
    """Verify that ProjectService correctly finds the root marker."""
    # Test from within a subdirectory
    sub = temp_project_root / "src" / "deep" / "path"
    sub.mkdir(parents=True)

    root = ProjectService.detect_project_root(start_path=sub)
    assert root == temp_project_root.resolve()


def test_project_root_detection_uses_caller_frame_when_cwd_is_root(monkeypatch, temp_project_root):
    """Verify auto-detection can recover from a root cwd by inspecting caller frames."""
    notebook_dir = temp_project_root / "notebooks"
    notebook_dir.mkdir()
    notebook_file = notebook_dir / "example_notebook.py"
    notebook_file.touch()

    monkeypatch.setattr(project_module.os, "getcwd", lambda: "/")
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.setattr(
        project_module.inspect,
        "stack",
        lambda *_: [
            SimpleNamespace(filename="<frame>"),
            SimpleNamespace(filename=str(notebook_file)),
        ],
    )

    root = ProjectService.detect_project_root()
    assert root == temp_project_root.resolve()


def test_project_root_detection_supports_custom_markers(tmp_path):
    project_root = tmp_path / "workspace"
    nested = project_root / "deep" / "inside"
    nested.mkdir(parents=True)
    (project_root / ".workspace-root").touch()

    root = ProjectService.detect_project_root(start_path=nested, markers=[".workspace-root"])

    assert root == project_root.resolve()


def test_secure_resource_sandboxing(temp_project_root):
    """Verify that SecureResource blocks path traversal."""
    config = ResourceConfig(project_root=temp_project_root)
    with SecureResource(config=config) as res:
        # Valid path
        valid = temp_project_root / "valid.txt"
        assert res.get_secure_path(valid) == valid.resolve()

        # Invalid path (outside root)
        with pytest.raises(PermissionError, match="outside the configured sandbox roots"):
            res.get_secure_path("/etc/passwd")

        # Traversal attempt (definitely outside)
        with pytest.raises(PermissionError):
            res.get_secure_path("/System/not_allowed")


def test_secure_resource_temp_allowed(temp_project_root):
    """Verify the system temp root is allowlisted for notebook-style workflows."""
    config = ResourceConfig(project_root=temp_project_root)
    with SecureResource(config=config) as res:
        tmp = Path(tempfile.gettempdir()).resolve()
        assert res.get_secure_path(tmp) == tmp


def test_secure_resource_extra_allowed(temp_project_root, tmp_path):
    """Verify that explicitly added paths are allowed."""
    extra = (tmp_path / "sibi_extra_test").resolve()
    extra.mkdir()
    config = ResourceConfig(project_root=temp_project_root, extra_allowed_paths=[extra])
    with SecureResource(config=config) as res:
        assert res.get_secure_path(extra) == extra


def test_secure_resource_rejected_config_does_not_warn_on_gc(temp_project_root, tmp_path):
    """A constructor that rejects its sandbox config must not leave a GC leak finalizer
    behind — there was never a live resource to leak."""
    import gc
    import warnings

    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    config = ResourceConfig(
        project_root=temp_project_root, extra_allowed_paths=[link], skip_logger=True
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(ValueError, match="symlink"):
            SecureResource(config=config)
        gc.collect()

    assert not [w for w in caught if "garbage collected" in str(w.message)]


def test_secure_resource_violation_log_keeps_path_despite_pii_filter(temp_project_root):
    """The traversal alert must still name the target even when it is /etc/passwd —
    'passwd' is a PII keyword and would blank an interpolated message."""
    from boti.core.logger_filters import PIISecretFilter

    seen: list[logging.LogRecord] = []

    class _Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            seen.append(record)

    core = logging.getLogger("test.secure.violation")
    core.setLevel(logging.ERROR)
    handler = _Capture()
    handler.addFilter(PIISecretFilter())
    core.addHandler(handler)
    try:
        config = ResourceConfig(project_root=temp_project_root, logger=core)
        with SecureResource(config=config) as res, pytest.raises(PermissionError):
            res.get_secure_path("/etc/passwd")
    finally:
        core.removeHandler(handler)

    assert len(seen) == 1
    rendered = seen[0].getMessage()
    assert "SECURITY VIOLATION" in rendered
    assert "/etc/passwd" in rendered


def test_setup_environment_loads_in_project_env_file(monkeypatch, temp_project_root):
    """Verify setup_environment loads files inside the project root."""
    env_file = temp_project_root / ".env.local"
    env_file.write_text("BOTI_TEST_ENV='loaded'\n", encoding="utf-8")
    monkeypatch.delenv("BOTI_TEST_ENV", raising=False)

    used = ProjectService.setup_environment(temp_project_root, ".env.local")

    assert used == env_file.resolve()
    assert os.environ["BOTI_TEST_ENV"] == "loaded"


def test_setup_environment_rejects_invalid_env_var_name(temp_project_root):
    """Verify setup_environment rejects dotenv keys that are not valid environment names."""
    env_file = temp_project_root / ".env.local"
    env_file.write_text("BAD-NAME=loaded\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid environment bindings"):
        ProjectService.setup_environment(temp_project_root, ".env.local")


def test_setup_environment_rejects_nul_bytes_in_value(temp_project_root):
    """Verify setup_environment rejects dotenv values that contain NUL bytes."""
    env_file = temp_project_root / ".env.local"
    env_file.write_bytes(b"BOTI_TEST_ENV=bad\x00value\n")

    with pytest.raises(ValueError, match="Invalid environment bindings"):
        ProjectService.setup_environment(temp_project_root, ".env.local")


def test_setup_environment_rejects_external_absolute_path(temp_project_root):
    """Verify setup_environment rejects env files outside the project root."""
    outside_dir = temp_project_root.parent / "outside-envs"
    outside_dir.mkdir(exist_ok=True)
    env_file = outside_dir / "evil.env"
    env_file.write_text("BOTI_TEST_ENV='blocked'\n", encoding="utf-8")

    with pytest.raises(PermissionError, match="must be inside project root"):
        ProjectService.setup_environment(temp_project_root, env_file)


def test_setup_environment_rejects_relative_traversal(temp_project_root):
    """Verify setup_environment rejects traversal paths that escape the project root."""
    outside_dir = temp_project_root.parent / "outside-traversal"
    outside_dir.mkdir(exist_ok=True)
    env_file = outside_dir / "evil.env"
    env_file.write_text("BOTI_TEST_ENV='blocked'\n", encoding="utf-8")

    traversal = Path("..") / outside_dir.name / env_file.name
    with pytest.raises(PermissionError, match="must be inside project root"):
        ProjectService.setup_environment(temp_project_root, traversal)


def test_setup_environment_supports_custom_candidate_files(monkeypatch, temp_project_root):
    env_file = temp_project_root / "settings.env"
    env_file.write_text("BOTI_TEST_ENV='custom'\n", encoding="utf-8")
    monkeypatch.delenv("BOTI_TEST_ENV", raising=False)

    used = ProjectService.setup_environment(
        temp_project_root,
        candidate_files=["settings.env"],
    )

    assert used == env_file.resolve()
    assert os.environ["BOTI_TEST_ENV"] == "custom"


def test_setup_environment_does_not_clobber_existing_env_var_by_default(
    monkeypatch, temp_project_root
):
    """A key already present in os.environ must win over the .env file's value
    unless override=True is passed explicitly — matching python-dotenv's own
    load_dotenv() convention. Regression test for the silent-clobber bug where
    a test suite's ephemeral env vars got overwritten back to .env's values on
    every fresh ProjectService.setup_environment() call."""
    env_file = temp_project_root / ".env.local"
    env_file.write_text("BOTI_TEST_ENV='from_dotenv'\n", encoding="utf-8")
    monkeypatch.setenv("BOTI_TEST_ENV", "already_set")

    ProjectService.setup_environment(temp_project_root, ".env.local")

    assert os.environ["BOTI_TEST_ENV"] == "already_set"


def test_setup_environment_override_true_lets_dotenv_win(monkeypatch, temp_project_root):
    """override=True restores the old always-clobber behavior explicitly."""
    env_file = temp_project_root / ".env.local"
    env_file.write_text("BOTI_TEST_ENV='from_dotenv'\n", encoding="utf-8")
    monkeypatch.setenv("BOTI_TEST_ENV", "already_set")

    ProjectService.setup_environment(temp_project_root, ".env.local", override=True)

    assert os.environ["BOTI_TEST_ENV"] == "from_dotenv"


def test_secure_resource_default_logger_uses_project_root(temp_project_root):
    """Verify default logger paths are anchored to the configured project root."""
    config = ResourceConfig(project_root=temp_project_root)

    with SecureResource(config=config) as res:
        assert res.logger.log_dir == (temp_project_root / "logs").resolve()


# ---------------------------------------------------------------------------
# Adversarial / zero-day-style regressions
#
# Each test below reproduces a bypass technique that was verified against the
# actual implementation before being fixed (or, where the implementation was
# already correct, locks in that correctness so a future refactor can't
# silently reintroduce it).
# ---------------------------------------------------------------------------


class TestIdentifierRegexAnchorBypass:
    """`is_valid_identifier` is documented as a code-injection guard for
    dynamically generated code and dotted-import paths (see
    boti-data's sql_model_registry/sql_model_builder). It used
    `re.match(r'^...$', name)`; `$` matches just before a trailing newline
    rather than only at the true end of string, so `re.match` (which does not
    require consuming the whole input) let a name like "foo\\n" pass as a
    valid identifier. `re.fullmatch` closes this."""

    def test_rejects_trailing_newline(self):
        assert is_valid_identifier("valid_name") is True
        assert is_valid_identifier("valid_name\n") is False
        assert is_valid_identifier("valid_name\n\n") is False

    def test_rejects_embedded_content_after_newline(self):
        assert is_valid_identifier("os\nimport sys") is False

    def test_dotted_identifier_rejects_trailing_newline(self):
        assert is_valid_dotted_identifier("pkg.module") is True
        assert is_valid_dotted_identifier("pkg.module\n") is False


class TestHasDunderIdentifier:
    """`has_dunder_identifier` gates strings before they reach eval/exec-like
    sinks (e.g. boti-data's RowFilter/DerivedColumn, which pass expressions
    to pandas DataFrame.eval()). It replaced a raw substring test for "__",
    which rejected legitimate identifiers that merely contain "__" in the
    middle (e.g. a column named "a__b") while still being no stronger against
    an attacker than tokenizing — this test locks down both the true
    positives (dunder-wrapped tokens, the building block of documented eval
    sandbox-escape chains like CVE-2024-9880) and the false positives the
    old substring test would have produced."""

    def test_flags_dunder_attribute_access(self):
        assert has_dunder_identifier("x.__class__") is True
        assert has_dunder_identifier("().__class__.__bases__[0].__subclasses__()") is True

    def test_flags_bare_dunder_name(self):
        assert has_dunder_identifier('__import__("os")') is True
        assert has_dunder_identifier("__class__") is True

    def test_allows_identifier_with_embedded_double_underscore(self):
        assert has_dunder_identifier("a__b > 1") is False
        assert has_dunder_identifier("col__suffix == 1") is False

    def test_allows_identifier_with_only_leading_or_trailing_double_underscore(self):
        assert has_dunder_identifier("__internal_id > 0") is False
        assert has_dunder_identifier("trailing__ > 0") is False

    def test_allows_expression_with_no_dunder_at_all(self):
        assert has_dunder_identifier("value > 1000") is False
        assert has_dunder_identifier("arrival_date + pd.Timedelta(days=7)") is False


class TestGetSecurePathFailsClosed:
    """`get_secure_path` is documented to raise only `PermissionError` on
    denial. Its `Path(path).resolve()` call was unguarded, so malformed
    input (e.g. a NUL byte) raised a raw ValueError/OSError instead —
    callers written against the documented contract (like boti-data's
    former ad-hoc null-byte check) could let that escape uncaught."""

    def test_null_byte_raises_permission_error_not_value_error(self, temp_project_root):
        config = ResourceConfig(project_root=temp_project_root)
        with (
            SecureResource(config=config) as res,
            pytest.raises(PermissionError, match="could not be resolved"),
        ):
            res.get_secure_path("valid\x00.txt")

    def test_null_byte_blocked_through_open_secure(self, temp_project_root):
        config = ResourceConfig(project_root=temp_project_root)
        with SecureResource(config=config) as res, pytest.raises(PermissionError):
            res.write_text_secure("valid\x00.txt", "content")


class TestSandboxBypassTechniques:
    """These isolate the sandbox from the default system-temp allowlist (by
    patching tempfile.gettempdir) so that `tmp_path`-adjacent siblings are
    genuinely outside every allowed root, letting each bypass technique be
    tested against a real boundary instead of accidentally landing back
    inside the always-allowed system temp directory."""

    def _isolated_resource(self, tmp_path, monkeypatch):
        fake_temp = tmp_path / "faketemp"
        fake_temp.mkdir()
        root = tmp_path / "root"
        root.mkdir()
        (root / "pyproject.toml").touch()
        monkeypatch.setattr(secure_io_module.tempfile, "gettempdir", lambda: str(fake_temp))
        config = ResourceConfig(project_root=root)
        return root, SecureResource(config=config)

    def test_symlink_inside_root_escaping_to_outside_target_is_blocked(self, tmp_path, monkeypatch):
        """A symlink that physically lives inside the project root but points
        to a file outside every allowed root must be denied — Path.resolve()
        follows symlinks, so validation must run against the resolved target,
        never the link's own location."""
        root, res = self._isolated_resource(tmp_path, monkeypatch)
        outside = tmp_path / "outside_secret"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("TOP SECRET")
        link = root / "escape_link"
        link.symlink_to(secret)

        with res, pytest.raises(PermissionError):
            res.get_secure_path(link)

    def test_sibling_directory_prefix_confusion_is_blocked(self, tmp_path, monkeypatch):
        """A sibling directory whose string form merely starts with the
        allowed root's path (e.g. '.../root' vs '.../root_evil') must not be
        treated as contained within it. This locks in that the boundary
        check uses Path.is_relative_to (component-wise) rather than a raw
        string prefix comparison, which would be fooled by this pattern."""
        root, res = self._isolated_resource(tmp_path, monkeypatch)
        sibling_evil = Path(str(root) + "_evil")
        sibling_evil.mkdir()
        evil_file = sibling_evil / "x.txt"
        evil_file.write_text("evil")

        with res, pytest.raises(PermissionError):
            res.get_secure_path(evil_file)

    def test_relative_traversal_escaping_root_via_dotdot_is_blocked(self, tmp_path, monkeypatch):
        """'..' segments in a path that otherwise starts inside the root
        must not be able to walk back out past it."""
        root, res = self._isolated_resource(tmp_path, monkeypatch)
        nested = root / "a" / "b"
        nested.mkdir(parents=True)
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("TOP SECRET")

        traversal = nested / ".." / ".." / ".." / "outside" / "secret.txt"
        with res, pytest.raises(PermissionError):
            res.get_secure_path(traversal)


# ---------------------------------------------------------------------------
# Project-root detection: fail closed for the sandbox, anchors, markers
# ---------------------------------------------------------------------------


def _markerless_cwd(monkeypatch, tmp_path):
    """Point every root-detection candidate at a directory tree with no marker."""
    plain = tmp_path / "plain" / "deep"
    plain.mkdir(parents=True)
    script = plain / "script.py"
    script.touch()
    monkeypatch.setattr(project_module.os, "getcwd", lambda: str(plain))
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.setattr(
        project_module.inspect,
        "stack",
        lambda *_: [SimpleNamespace(filename="<frame>"), SimpleNamespace(filename=str(script))],
    )
    return plain


def test_find_project_root_returns_none_without_marker(monkeypatch, tmp_path):
    _markerless_cwd(monkeypatch, tmp_path)
    assert ProjectService.find_project_root() is None


def test_detect_project_root_still_falls_back_with_warning(monkeypatch, tmp_path):
    plain = _markerless_cwd(monkeypatch, tmp_path)
    with pytest.warns(UserWarning, match="could not locate a project root marker"):
        assert ProjectService.detect_project_root() == plain.resolve()


def test_secure_resource_refuses_to_sandbox_a_guessed_root(monkeypatch, tmp_path):
    """No project_root and no marker: the sandbox must not silently become cwd
    or $HOME. The half-built object must not leave a GC leak warning either."""
    import gc
    import warnings

    _markerless_cwd(monkeypatch, tmp_path)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(ValueError, match="could not detect a project root"):
            SecureResource(config=ResourceConfig(skip_logger=True))
        gc.collect()
    assert not [w for w in caught if "garbage collected" in str(w.message)]


def test_secure_resource_uses_detected_marker_root(monkeypatch, tmp_path):
    plain = _markerless_cwd(monkeypatch, tmp_path)
    (plain.parent / ".git").mkdir()
    with SecureResource(config=ResourceConfig(skip_logger=True)) as res:
        assert res.project_root == plain.parent.resolve()


def test_src_boti_is_no_longer_a_root_marker(tmp_path):
    fake = tmp_path / "somewhere"
    (fake / "src" / "boti").mkdir(parents=True)
    assert ProjectService.find_project_root(start_path=fake) is None


def test_stack_walk_skips_boti_package_frames(monkeypatch, tmp_path):
    """boti's own modules are never an anchor — otherwise any call from inside
    a boti checkout would 'find' boti's repository root."""
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setattr(project_module.os, "getcwd", lambda: str(plain))
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.setattr(
        project_module.inspect,
        "stack",
        lambda *_: [
            SimpleNamespace(filename="<frame>"),
            SimpleNamespace(filename=project_module.__file__),
        ],
    )
    assert ProjectService.find_project_root() is None


def test_stack_walk_does_not_read_source_context(monkeypatch, temp_project_root):
    """inspect.stack() with the default context=1 reads source lines for every
    frame; detection only needs filenames."""
    seen = []

    def fake_stack(context=1):
        seen.append(context)
        return [SimpleNamespace(filename="<frame>")]

    monkeypatch.setattr(project_module.os, "getcwd", lambda: str(temp_project_root))
    monkeypatch.setattr(project_module.inspect, "stack", fake_stack)
    ProjectService.detect_project_root()
    assert seen == [0]


# ---------------------------------------------------------------------------
# open_secure: modes, and a symlink appearing after validation
# ---------------------------------------------------------------------------


class TestOpenSecure:
    def _res(self, root):
        return SecureResource(config=ResourceConfig(project_root=root, skip_logger=True))

    def test_text_round_trip_through_modes(self, temp_project_root):
        target = temp_project_root / "nested" / "f.txt"
        target.parent.mkdir()
        with self._res(temp_project_root) as res:
            with res.open_secure(target, "w") as fh:
                fh.write("one\n")
            with res.open_secure(target, "a") as fh:
                fh.write("two\n")
            with res.open_secure(target) as fh:
                assert fh.read() == "one\ntwo\n"
            with res.open_secure(target, "rb") as fh:
                assert fh.read() == b"one\ntwo\n"
            with res.open_secure(target, "r+") as fh:
                fh.seek(0)
                fh.write("ONE")
            assert target.read_text() == "ONE\ntwo\n"
            assert res.read_text_secure(target) == "ONE\ntwo\n"
            res.write_text_secure(target, "fresh")
            assert target.read_text() == "fresh"

    def test_exclusive_create_and_missing_file_keep_stdlib_errors(self, temp_project_root):
        existing = temp_project_root / "exists.txt"
        existing.write_text("x")
        with self._res(temp_project_root) as res:
            with pytest.raises(FileExistsError):
                res.open_secure(existing, "x")
            with pytest.raises(FileNotFoundError):
                res.open_secure(temp_project_root / "missing.txt")
            with pytest.raises(IsADirectoryError):
                res.open_secure(temp_project_root)
            with pytest.raises(ValueError, match="invalid mode"):
                res.open_secure(existing, "rw")

    def test_created_file_mode_matches_open(self, temp_project_root):
        target = temp_project_root / "perm.txt"
        with self._res(temp_project_root) as res, res.open_secure(target, "w") as fh:
            fh.write("x")
        expected = 0o666 & ~_current_umask()
        assert stat.S_IMODE(target.stat().st_mode) == expected

    @pytest.mark.skipif(not secure_io_module._OPEN_BENEATH_SUPPORTED, reason="needs openat()")
    def test_symlink_planted_after_validation_is_refused(self, temp_project_root, monkeypatch):
        """TOCTOU: the path validated by get_secure_path() is then opened via a
        NOFOLLOW walk, so a component swapped for a symlink in between is
        refused rather than followed. Simulated by handing open_secure a
        validated path whose directory is already a symlink."""
        root = temp_project_root.resolve()
        outside = root.parent / "outside_for_toctou"
        outside.mkdir(exist_ok=True)
        (outside / "secret.txt").write_text("TOP SECRET")
        link = root / "data"
        link.symlink_to(outside, target_is_directory=True)
        swapped = link / "secret.txt"
        with self._res(root) as res:
            monkeypatch.setattr(res, "_secure_path_and_root", lambda _p: (swapped, root))
            with pytest.raises(PermissionError, match="changed to a symlink"):
                res.open_secure(swapped)

    @pytest.mark.skipif(not secure_io_module._OPEN_BENEATH_SUPPORTED, reason="needs openat()")
    def test_symlink_as_final_component_is_refused_on_write(self, temp_project_root, monkeypatch):
        root = temp_project_root.resolve()
        outside = root.parent / "outside_target.txt"
        outside.write_text("untouched")
        link = root / "link.txt"
        link.symlink_to(outside)
        with self._res(root) as res:
            monkeypatch.setattr(res, "_secure_path_and_root", lambda _p: (link, root))
            with pytest.raises(PermissionError, match="changed to a symlink"):
                res.open_secure(link, "w")
        assert outside.read_text() == "untouched"


def _current_umask() -> int:
    current = os.umask(0)
    os.umask(current)
    return current
