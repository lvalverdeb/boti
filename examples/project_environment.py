"""
ProjectService example for project-root discovery and dotenv loading.
"""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from boti.core import ProjectService


def main() -> None:
    with TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir)
        (project_root / "pyproject.toml").write_text("[project]\nname='example'\n", encoding="utf-8")
        nested = project_root / "src" / "demo"
        nested.mkdir(parents=True)

        env_file = project_root / ".env.local"
        env_file.write_text("EXAMPLE_PROJECT_ENV=loaded\n", encoding="utf-8")

        detected_root = ProjectService.detect_project_root(nested)
        used_env = ProjectService.setup_environment(project_root, ".env.local")

        print(f"detected_root={detected_root}")
        print(f"used_env={used_env}")
        print(f"EXAMPLE_PROJECT_ENV={os.environ['EXAMPLE_PROJECT_ENV']}")


if __name__ == "__main__":
    main()
