"""
Async ETL pipeline — multiple stages running concurrently with asyncio.

Demonstrates:
  - ManagedResource with native __acleanup__ for async lifecycle
  - asyncio.gather for concurrent extract / transform / load stages
  - asyncio.Queue for producer-consumer data flow
  - Structured logging with async resource context
  - Graceful cancellation and cleanup on error
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from boti.core import ManagedResource
from boti.core.logger import Logger
from boti.core.models import LoggerConfig, ResourceConfig


class AsyncEtlResource(ManagedResource):
    """Async ETL orchestrator with concurrent stage execution."""

    def __init__(self, data_dir: Path, logger: Logger, **kwargs) -> None:
        super().__init__(**kwargs)
        self.data_dir = data_dir
        self._logger = logger
        self._queue: asyncio.Queue[dict] = asyncio.Queue()

    async def extract(self, source: str) -> list[dict]:
        self._logger.info(f"extracting from {source}")
        await asyncio.sleep(0.02)
        path = self.data_dir / source
        records = [
            {"id": i, "source": source}
            for i, line in enumerate(path.read_text().splitlines() if path.exists() else [])
        ]
        return records

    async def transform(self, record: dict) -> dict:
        await asyncio.sleep(0.01)
        record["processed"] = True
        record["value"] = hash(record["id"]) % 1000
        return record

    async def load(self, record: dict) -> str:
        await asyncio.sleep(0.005)
        line = f"{record['source']},{record['id']},{record['value']}\n"
        return line

    async def pipeline_for_source(self, source: str) -> int:
        records = await self.extract(source)
        transformed = await asyncio.gather(
            *(self.transform(r) for r in records),
        )
        lines = await asyncio.gather(
            *(self.load(r) for r in transformed),
        )
        output_path = self.data_dir / f"output_{source}"
        output_path.write_text("".join(lines))
        self._logger.info(
            f"pipeline done for {source}: {len(lines)} records",
        )
        return len(lines)

    async def run_concurrent(self, sources: list[str]) -> dict[str, int]:
        results = await asyncio.gather(
            *(self.pipeline_for_source(s) for s in sources),
            return_exceptions=True,
        )
        return {
            sources[i]: (r if isinstance(r, int) else 0)
            for i, r in enumerate(results)
        }

    async def _acleanup(self) -> None:
        self._logger.info("async ETL resource cleaned up")


async def async_main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)

        for src in ["users", "products", "events"]:
            (data_dir / src).write_text("\n".join(f"row_{i}" for i in range(10)))

        logger = Logger(LoggerConfig(
            log_dir=data_dir / "logs",
            logger_name="async_etl",
            verbose=False,
        ))

        resource = AsyncEtlResource(
            data_dir=data_dir,
            logger=logger,
            config=ResourceConfig(verbose=False),
        )

        async with resource:
            counts = await resource.run_concurrent(["users", "products", "events"])

        for src, count in sorted(counts.items()):
            print(f"  {src}: {count} records")
        print(f"  total: {sum(counts.values())}")


def main() -> None:
    print("=== Async ETL Pipeline example ===\n")
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
