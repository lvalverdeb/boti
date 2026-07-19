"""
Multiple concurrent ETL operations — mixed sources, error isolation.

Demonstrates:
  - Multiple ETL sources processed concurrently (API, file, in-memory)
  - FilesystemConfig with different backends (memory + file)
  - Per-source error handling so one failure doesn't stop others
  - ManagedResource lifecycle with filesystem adapters
  - Thread-safe concurrent execution with ThreadPoolExecutor
  - Per-task logging with structured context
"""

from __future__ import annotations

import csv
import io
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from boti.core import Logger, ManagedResource
from boti.core.filesystem import FilesystemAdapter, FilesystemConfig
from boti.core.models import ResourceConfig


class MultiSourceEtlResource(ManagedResource):
    """ETL orchestrator managing multiple source filesystem adapters."""

    def __init__(
        self,
        file_adapter: FilesystemAdapter,
        memory_adapter: FilesystemAdapter,
        logger: Logger,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.file_adapter = file_adapter
        self.memory_adapter = memory_adapter
        self._logger = logger
        self._results: dict[str, dict[str, Any]] = {}

    def _source_label(self, source: str) -> str:
        return source

    def extract_file(self, source: str) -> list[dict]:
        fs = self.file_adapter.get_filesystem()
        with fs.open(source, "r") as f:
            raw = f.read()
        return list(csv.DictReader(io.StringIO(raw)))

    def transform(self, records: list[dict], multiplier: int = 1) -> list[dict]:
        for r in records:
            r["transformed_value"] = str(int(r.get("value", 0)) * multiplier)
        return records

    def load_memory(self, records: list[dict], table: str) -> int:
        fs = self.memory_adapter.get_filesystem()
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
        with fs.open(f"{table}.csv", "w") as f:
            f.write(buf.getvalue())
        return len(records)

    def run_source(self, source_id: str, source_file: str) -> dict[str, Any]:
        try:
            self._logger.info(
                f"source={source_id} starting",
                extra={"source": source_id},
            )
            records = self.extract_file(source_file)
            self._logger.info(
                f"source={source_id} extracted {len(records)} records",
                extra={"source": source_id, "phase": "extract", "count": len(records)},
            )

            transformed = self.transform(records, multiplier=source_id.count(""))
            loaded = self.load_memory(transformed, table=f"output_{source_id}")

            return {"source": source_id, "status": "ok", "records": loaded}
        except Exception as exc:
            self._logger.error(
                f"source={source_id} failed: {exc}",
                extra={"source": source_id, "error": str(exc)},
            )
            return {"source": source_id, "status": "error", "error": str(exc)}

    def _cleanup(self) -> None:
        self.file_adapter.invalidate()
        self.memory_adapter.invalidate()


def example_concurrent_multiple() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        sources = {
            "sales": "id,name,value\n1,alice,100\n2,bob,200\n3,carol,300\n4,dave,400",
            "inventory": "sku,name,value\nA1,widget,50\nB2,gadget,150\nC3,doohickey,250",
            "analytics": "metric,value\npageviews,5000\nsessions,1200\nconversions,85",
        }
        for name, content in sources.items():
            (base / f"{name}.csv").write_text(content)

        logger = Logger.default_logger(
            logger_name="multi_source_etl",
            base_dir=base,
        )

        file_adapter = FilesystemAdapter(
            FilesystemConfig(fs_type="file", fs_path=str(base)),
        )
        memory_adapter = FilesystemAdapter(
            FilesystemConfig(fs_type="memory", fs_path="etl_output"),
        )

        resource = MultiSourceEtlResource(
            file_adapter=file_adapter,
            memory_adapter=memory_adapter,
            logger=logger,
            config=ResourceConfig(verbose=False),
        )

        with resource, ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(
                    resource.run_source,
                    name,
                    str(base / f"{name}.csv"),
                ): name
                for name in sources
            }

            all_results = {}
            for future in as_completed(futures):
                result = future.result()
                all_results[result["source"]] = result

        for sid in sorted(all_results):
            r = all_results[sid]
            status_icon = "ok" if r["status"] == "ok" else "FAILED"
            print(f"  {sid}: {r.get('records', '?')} records  [{status_icon}]")


def main() -> None:
    print("=== Multiple Concurrent ETL Operations example ===\n")
    example_concurrent_multiple()


if __name__ == "__main__":
    main()
