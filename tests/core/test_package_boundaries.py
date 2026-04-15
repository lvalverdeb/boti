from __future__ import annotations

import ast
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[2] / "src" / "boti" / "core"


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.add(node.module)
    return modules


def test_core_modules_do_not_depend_on_data():
    offenders: list[str] = []
    for path in sorted(SRC_ROOT.glob("*.py")):
        imported_modules = _imported_modules(path)
        if any(module == "boti_data" or module.startswith("boti_data.") for module in imported_modules):
            offenders.append(path.name)

    assert offenders == []
