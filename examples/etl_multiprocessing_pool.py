"""
Multiprocessing ETL — fan-out file processing across a process pool.

Demonstrates:
  - Pickling a ManagedResource with allow_pickle=True
  - Shipping the pickled config to multiprocessing.Pool workers
  - worker_init() to set the trusted unpickle environment var
  - Each worker restores runtime state and processes a file chunk
  - Fan-out / fan-in pattern: split work, distribute, collect results
"""

from __future__ import annotations

import os
import pickle
import tempfile
from pathlib import Path

from boti.core import ManagedResource
from boti.core.models import ResourceConfig


class FileCounterResource(ManagedResource):
    """Counts records across multiple data files."""

    def __init__(self, data_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.data_dir = data_dir

    def count_records(self, filename: str) -> int:
        path = self.data_dir / filename
        body = path.read_text()
        return len([l for l in body.splitlines() if l.strip()])

    def _restore_runtime_state(self) -> None:
        pass

    def _cleanup(self) -> None:
        pass


def worker_init() -> None:
    os.environ[ManagedResource._TRUSTED_UNPICKLE_ENV] = "1"


def count_file(args: tuple[bytes, str]) -> tuple[str, int]:
    payload, filename = args
    resource: FileCounterResource = pickle.loads(payload)
    with resource:
        count = resource.count_records(filename)
    return filename, count


def example_multiprocessing_etl() -> None:
    import multiprocessing

    with tempfile.TemporaryDirectory() as tmp:
        data_dir = Path(tmp)

        for i in range(1, 6):
            (data_dir / f"batch_{i}.csv").write_text(
                "id,value\n" + "\n".join(f"{j},{j * 10}" for j in range(1, 101)),
            )

        config = ResourceConfig(allow_pickle=True)
        resource = FileCounterResource(data_dir=data_dir, config=config)
        payload = pickle.dumps(resource)
        resource.close()

        filenames = sorted(p.name for p in data_dir.iterdir() if p.suffix == ".csv")

        with multiprocessing.Pool(
            processes=4,
            initializer=worker_init,
        ) as pool:
            results = pool.map(count_file, [(payload, f) for f in filenames])

        total = 0
        for name, count in sorted(results):
            print(f"  {name}: {count} records")
            total += count
        print(f"  total records (all batches): {total}")


def main() -> None:
    print("=== Multiprocessing ETL example ===\n")
    example_multiprocessing_etl()


if __name__ == "__main__":
    main()
