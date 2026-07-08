"""
Concurrent threaded ETL — multiple pipeline tasks via ThreadPoolExecutor.

Demonstrates:
  - ThreadPoolExecutor for concurrent ETL operations
  - Multiple tasks (extract, transform, load) running in parallel
  - Thread-safe ManagedResource filesystem access
  - Shared Logger with per-task structured logging
  - Each task reads from a source, transforms records, and writes output
"""

from __future__ import annotations

import csv
import io
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from boti.core import ManagedResource, Logger
from boti.core.filesystem import FilesystemAdapter, FilesystemConfig
from boti.core.models import LoggerConfig, ResourceConfig


class EtlWorkerResource(ManagedResource):
    """Thread-safe ETL worker attached to a shared filesystem."""

    def __init__(
        self,
        storage_adapter: FilesystemAdapter,
        logger: Logger,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.storage_adapter = storage_adapter
        self._logger = logger

    def _get_fs(self):
        return self.storage_adapter.get_filesystem()

    def run_task(self, task_id: str, source_path: str) -> dict[str, Any]:
        self._logger.info(
            f"task={task_id} extracting from {source_path}",
            extra={"task": task_id, "phase": "extract"},
        )
        raw = self._get_fs().read_text(source_path)

        self._logger.info(
            f"task={task_id} transforming {len(raw)} bytes",
            extra={"task": task_id, "phase": "transform", "bytes": len(raw)},
        )
        reader = csv.DictReader(io.StringIO(raw))
        transformed = []
        for row in reader:
            row["value"] = str(int(row["value"]) * 2)
            transformed.append(row)

        self._logger.info(
            f"task={task_id} loaded {len(transformed)} records",
            extra={"task": task_id, "phase": "load", "records": len(transformed)},
        )
        return {"task_id": task_id, "records": transformed, "count": len(transformed)}

    def _cleanup(self) -> None:
        self._logger.info("worker closed")


def example_concurrent_threads() -> None:
    import threading

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        source_dir = base / "sources"
        source_dir.mkdir()

        tasks_def = {
            "orders": ["id,value", "1,100", "2,200", "3,300"],
            "returns": ["id,value", "4,400", "5,500"],
            "refunds": ["id,value", "6,600"],
        }
        for name, lines in tasks_def.items():
            (source_dir / f"{name}.csv").write_text("\n".join(lines) + "\n")

        logger = Logger.default_logger(
            logger_name="concurrent_etl",
            base_dir=base,
        )

        storage_config = FilesystemConfig(fs_type="file", fs_path=str(source_dir))
        adapter = FilesystemAdapter(storage_config)

        worker = EtlWorkerResource(
            storage_adapter=adapter,
            logger=logger,
        )

        with worker, ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(worker.run_task, name, str(source_dir / f"{name}.csv")): name
                for name in tasks_def
            }

            all_results = {}
            for future in as_completed(futures):
                tid = futures[future]
                result = future.result()
                all_results[tid] = result

        for tid in sorted(all_results):
            r = all_results[tid]
            print(f"  {tid}: {r['count']} records")


def main() -> None:
    print("=== Concurrent Threaded ETL example ===\n")
    example_concurrent_threads()


if __name__ == "__main__":
    main()
