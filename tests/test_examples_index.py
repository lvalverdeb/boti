"""examples/README.md must list every example, in both its run block and its table."""

import re
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def test_examples_index_matches_the_directory():
    on_disk = {path.name for path in EXAMPLES_DIR.glob("*.py")}
    index = (EXAMPLES_DIR / "README.md").read_text(encoding="utf-8")
    in_run_block = set(re.findall(r"^python examples/(\w+\.py)", index, re.MULTILINE))
    in_table = set(re.findall(r"^\| `(\w+\.py)` \|", index, re.MULTILINE))

    assert on_disk - in_run_block == set(), "examples missing from the run block"
    assert in_run_block - on_disk == set(), "run block names examples that do not exist"
    assert on_disk - in_table == set(), "examples missing from the API table"
    assert in_table - on_disk == set(), "API table names examples that do not exist"
