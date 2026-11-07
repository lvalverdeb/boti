"""End-to-end tests for the ``boti`` CLI: every subcommand and flag through ``main(argv)``."""

import importlib.metadata
import subprocess
import sys
from pathlib import Path

import pytest

import boti.core
from boti import main as main_module


def _run(argv: list[str], capsys) -> tuple[int, str, str]:
    with pytest.raises(SystemExit) as exc_info:
        main_module.main(argv)
    out, err = capsys.readouterr()
    code = exc_info.value.code
    return (code if isinstance(code, int) else 1), out, err


def test_version_flag_prints_installed_version(capsys):
    code, out, _ = _run(["--version"], capsys)
    assert code == 0
    assert out.strip() == f"boti v{main_module._get_version()}"


@pytest.mark.parametrize("argv", [[], ["bogus"]], ids=["no-command", "unknown-command"])
def test_usage_errors_exit_2(argv, capsys):
    code, _, err = _run(argv, capsys)
    assert code == 2
    assert "usage: boti" in err


def test_info_lists_runtime_facts_and_every_public_symbol(capsys):
    code, out, _ = _run(["info"], capsys)
    assert code == 0
    assert out.startswith(f"boti v{main_module._get_version()}")
    for label in ("Python:", "Platform:", "Project:", "Installed:", "Open strategy:", "Extras:"):
        assert label in out
    assert f"Public API: {len(boti.core.__all__)} symbols" in out
    for name in boti.core.__all__:
        assert f"    - {name}" in out


def test_info_reports_each_optional_extra(capsys, monkeypatch):
    monkeypatch.setattr(
        main_module, "_OPTIONAL_EXTRAS", {"s3": "s3fs", "nope": "boti-no-such-dist"}
    )
    _, out, _ = _run(["info"], capsys)
    assert "    - s3: installed (s3fs==" in out
    assert "    - nope: missing (pip install 'boti[nope]')" in out


def test_check_passes_in_a_healthy_environment(capsys):
    code, out, _ = _run(["check"], capsys)
    assert code == 0
    assert out.rstrip().endswith("All checks passed.")
    assert "[ok] Project root:" in out
    assert "[ok] Version:" in out
    assert "[ok] Extra: s3 (s3fs==" in out
    for dep in ("pydantic", "fsspec", "pyarrow"):
        assert f"[ok] Dependency: {dep}==" in out


def test_check_discovers_every_core_module(capsys):
    """The import check is discovered from the package directory, so a module
    added to boti.core is covered without being registered by hand."""
    errors = main_module._check_core_modules_importable()
    out = capsys.readouterr().out

    assert errors == []
    on_disk = {
        f"boti.core.{p.stem}"
        for p in Path(boti.core.__file__).parent.glob("*.py")
        if p.stem != "__init__"
    }
    checked = {line.split("Module: ")[1] for line in out.splitlines() if "Module: " in line}
    assert checked == {"boti.core", *on_disk}


def test_check_reports_a_missing_extra_as_info_not_error(capsys, monkeypatch):
    monkeypatch.setattr(main_module, "_OPTIONAL_EXTRAS", {"s3": "boti-no-such-dist"})
    code, out, _ = _run(["check"], capsys)
    assert code == 0
    assert "[info] Extra: s3 not installed (pip install 'boti[s3]')" in out
    assert "issue(s) found" not in out


def test_check_fails_and_lists_every_problem(capsys, monkeypatch):
    monkeypatch.setattr(
        main_module, "_check_critical_dependencies", lambda: ["Missing critical dependency: x"]
    )
    monkeypatch.setattr(
        main_module, "_check_project_root", lambda: ["Project root detection failed: y"]
    )
    code, out, _ = _run(["check"], capsys)
    assert code == 1
    assert "2 issue(s) found:" in out
    assert "! Missing critical dependency: x" in out
    assert "! Project root detection failed: y" in out


def test_check_reports_an_unimportable_core_module(capsys, monkeypatch):
    real_import = main_module.importlib.import_module

    def failing(name):
        if name == "boti.core.secure_io":
            raise ImportError("boom")
        return real_import(name)

    monkeypatch.setattr(main_module.importlib, "import_module", failing)
    code, out, _ = _run(["check"], capsys)
    assert code == 1
    assert "! Module boti.core.secure_io failed to import: boom" in out


def test_version_falls_back_when_boti_is_not_installed(monkeypatch):
    def missing(_name):
        raise importlib.metadata.PackageNotFoundError("boti")

    monkeypatch.setattr(main_module.importlib.metadata, "version", missing)
    assert main_module._get_version() == "0.0.0 (dev)"
    assert main_module._installed_version("s3fs") is None


def test_python_dash_m_boti_runs_the_entry_point():
    proc = subprocess.run(
        [sys.executable, "-m", "boti", "--version"], capture_output=True, text=True, check=False
    )
    assert proc.returncode == 0
    assert proc.stdout.strip() == f"boti v{main_module._get_version()}"


def test_dunder_main_module_in_process(capsys, monkeypatch):
    """Same entry point, run in-process so coverage sees boti/__main__.py."""
    import runpy

    monkeypatch.setattr(sys, "argv", ["boti", "--version"])
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module("boti", run_name="__main__", alter_sys=True)
    assert exc_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"boti v{main_module._get_version()}"
