"""
Built-in SecureResource example for sandboxed file I/O.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from boti.core import SecureResource
from boti.core.models import ResourceConfig


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir)
        (project_root / "pyproject.toml").write_text("[project]\nname='example'\n", encoding="utf-8")
        target = project_root / "safe.txt"

        with SecureResource(config=ResourceConfig(project_root=project_root)) as resource:
            resource.write_text_secure(target, "sandboxed hello")
            print(resource.read_text_secure(target))
            print(resource.get_secure_path(target))


if __name__ == "__main__":
    main()
