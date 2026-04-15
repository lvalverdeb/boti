"""
ProjectService example focused on runtime use cases rather than scaffolding.
"""

from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

from boti.core import ProjectService


def main() -> dict[str, str]:
    original_runtime_mode = os.environ.get("BOTI_RUNTIME_MODE")
    original_runtime_region = os.environ.get("BOTI_RUNTIME_REGION")

    try:
        with TemporaryDirectory() as tmp_dir:
            workspace_root = Path(tmp_dir) / "workspace"
            runtime_marker = workspace_root / ".workspace-root"
            service_dir = workspace_root / "services" / "billing" / "jobs"
            service_dir.mkdir(parents=True)
            runtime_marker.touch()

            runtime_env = workspace_root / "runtime.env"
            runtime_env.write_text(
                "BOTI_RUNTIME_MODE=batch\nBOTI_RUNTIME_REGION=eu-west-1\n",
                encoding="utf-8",
            )

            detected_root = ProjectService.detect_project_root(
                start_path=service_dir,
                markers=[".workspace-root"],
            )
            used_env = ProjectService.setup_environment(
                detected_root,
                candidate_files=["runtime.env"],
            )

            result = {
                "detected_root": str(detected_root),
                "used_env": str(used_env),
                "runtime_mode": os.environ["BOTI_RUNTIME_MODE"],
                "runtime_region": os.environ["BOTI_RUNTIME_REGION"],
            }

            print(f"Detected runtime root: {result['detected_root']}")
            print(f"Loaded runtime env: {result['used_env']}")
            print(
                "Runtime settings: "
                f"mode={result['runtime_mode']} region={result['runtime_region']}"
            )
            return result
    finally:
        if original_runtime_mode is None:
            os.environ.pop("BOTI_RUNTIME_MODE", None)
        else:
            os.environ["BOTI_RUNTIME_MODE"] = original_runtime_mode

        if original_runtime_region is None:
            os.environ.pop("BOTI_RUNTIME_REGION", None)
        else:
            os.environ["BOTI_RUNTIME_REGION"] = original_runtime_region


if __name__ == "__main__":
    main()
