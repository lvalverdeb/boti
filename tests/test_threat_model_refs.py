"""Every test the threat model cites must exist (docs/THREAT_MODEL.md, PRD-006 A2)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
THREAT_MODEL = ROOT / "docs" / "THREAT_MODEL.md"
_REF = re.compile(r"`(tests/[\w/]+\.py)::([\w:]+)`")


def _references() -> list[tuple[str, str]]:
    return _REF.findall(THREAT_MODEL.read_text(encoding="utf-8"))


def test_threat_model_cites_at_least_one_test_per_component():
    text = THREAT_MODEL.read_text(encoding="utf-8")
    sections = re.split(r"^## \d+\. ", text, flags=re.MULTILINE)[1:]
    assert len(sections) == 6
    for section in sections:
        assert _REF.search(section), section.splitlines()[0]


@pytest.mark.parametrize(
    "file_name,qualname", _references(), ids=[f"{f}::{q}" for f, q in _references()]
)
def test_cited_test_exists(file_name, qualname):
    source = (ROOT / file_name).read_text(encoding="utf-8")
    *classes, test_name = qualname.split("::")
    for class_name in classes:
        assert re.search(rf"^class {class_name}\b", source, re.MULTILINE), f"no class {class_name}"
    assert re.search(rf"^\s*(?:async )?def {test_name}\(", source, re.MULTILINE), (
        f"no test {test_name}"
    )
