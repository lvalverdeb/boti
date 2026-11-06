"""Tests for the ``boti`` CLI."""

from pathlib import Path

import boti.core
from boti import main as main_module


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
