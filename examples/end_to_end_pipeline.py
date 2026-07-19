"""
End-to-end pipeline combining multiple boti features.

Demonstrates a realistic workflow:
  - ProjectService for root detection and .env loading
  - SecureResource for sandboxed file I/O
  - Logger with structured logging and PII redaction
  - FilesystemConfig + FilesystemAdapter for remote-like storage
  - ManagedResource lifecycle (context manager, cleanup)
  - Security validation (is_secure_path, is_valid_identifier, env bindings)

Scenario: A data processing pipeline that:
  1. Discovers the project root and loads configuration
  2. Reads input data from a sandboxed directory
  3. Processes records with structured logging
  4. Writes output to a managed filesystem
  5. Validates all paths and identifiers for security
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from boti.core import (
    ManagedResource,
    ProjectService,
    SecureResource,
    is_secure_path,
    is_valid_identifier,
)
from boti.core.filesystem import FilesystemAdapter, FilesystemConfig
from boti.core.logger import Logger
from boti.core.models import LoggerConfig, ResourceConfig
from boti.core.security import validate_environment_bindings


class PipelineResource(ManagedResource):
    """Composite resource that orchestrates the data pipeline."""

    def __init__(
        self,
        project_root: Path,
        storage_config: FilesystemConfig,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.project_root = project_root
        self.storage_config = storage_config
        self.storage_adapter = FilesystemAdapter(storage_config)
        self.processed_count = 0
        self._temp_dir: tempfile.TemporaryDirectory | None = None

    @property
    def storage(self):
        return self.storage_adapter.get_filesystem()

    def read_input(self, path: Path) -> str:
        """Read input from a sandboxed path."""
        with SecureResource(
            config=ResourceConfig(project_root=self.project_root),
        ) as secure:
            return secure.read_text_secure(path)

    def write_output(self, path: str, content: str) -> None:
        """Write output to the managed storage."""
        self.storage.touch(path)
        with self.storage.open(path, "w", encoding="utf-8") as f:
            f.write(content)
        self.logger.info("output written", extra={"path": path, "size": len(content)})

    def process_record(self, record: dict) -> dict:
        """Simulate processing a data record with validation."""
        if not is_valid_identifier(record.get("name", "")):
            self.logger.warning("invalid record name skipped", extra={"record": record})
            return {}

        self.processed_count += 1
        self.logger.debug("record processed", extra={"record_id": record.get("id")})
        return {"status": "ok", "original": record}

    def _cleanup(self) -> None:
        if self._temp_dir:
            self._temp_dir.cleanup()
        self.storage_adapter.invalidate()


def example_pipeline() -> None:
    """Run the full pipeline end-to-end."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        project_root = Path(tmp_dir)
        data_dir = project_root / "input"
        data_dir.mkdir()

        # Create project marker
        (project_root / "pyproject.toml").write_text(
            "[project]\nname='pipeline'\n", encoding="utf-8"
        )

        # Create input data
        input_file = data_dir / "records.txt"
        input_file.write_text(
            "id=1,name=alice,value=100\nid=2,name=bob,value=200\nid=3,name=charlie,value=300\n"
        )

        # Create .env with pipeline config
        env_file = project_root / ".env"
        env_file.write_text("PIPELINE_MODE=production\nPIPELINE_BATCH_SIZE=100\n")

        # Step 1: Detect project root and load env
        detected = ProjectService.detect_project_root(project_root)
        ProjectService.setup_environment(detected)
        assert os.environ["PIPELINE_MODE"] == "production"

        # Step 2: Validate environment bindings
        bindings = validate_environment_bindings(
            {
                "PIPELINE_MODE": "production",
                "PIPELINE_BATCH_SIZE": "100",
            }
        )
        print(f"  validated env: {bindings}")

        # Step 3: Set up logging
        log_dir = project_root / "logs"
        logger = Logger(
            LoggerConfig(
                log_dir=log_dir,
                logger_name="pipeline",
                log_level=Logger.DEBUG,
            )
        )
        logger.set_level(Logger.DEBUG)
        logger.info("pipeline started", extra={"project_root": str(project_root)})

        # Step 4: Configure storage
        storage_config = FilesystemConfig(
            fs_type="memory",
            fs_path="pipeline-output",
        )

        # Step 5: Run pipeline with managed resource lifecycle
        with PipelineResource(
            project_root=project_root,
            storage_config=storage_config,
            config=ResourceConfig(verbose=True),
        ) as pipeline:
            # Read and process records
            content = pipeline.read_input(input_file)
            records = []
            for line in content.strip().splitlines():
                parts = dict(p.split("=", 1) for p in line.split(","))
                records.append(parts)

            logger.info(f"loaded {len(records)} input records")

            for rec in records:
                result = pipeline.process_record(rec)
                if not result:
                    logger.warning("skipped invalid record", extra={"input": rec})

            # Write summary output
            summary = f"processed: {pipeline.processed_count}\ntotal: {len(records)}\n"
            pipeline.write_output("pipeline-output/summary.txt", summary)

            # Verify output
            with pipeline.storage.open("pipeline-output/summary.txt", "r") as f:
                print(f"  output: {f.read().strip()}")

            # Path validation
            assert is_secure_path(input_file, [project_root])
            assert not is_secure_path(Path("/etc/passwd"), [project_root])

        # Step 6: Verify cleanup
        print(f"  pipeline closed: {pipeline.closed}")

    print()


def main() -> None:
    print("=== End-to-end pipeline example ===\n")
    example_pipeline()


if __name__ == "__main__":
    main()
