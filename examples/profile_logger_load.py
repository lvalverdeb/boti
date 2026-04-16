"""
Profile: Logger end-to-end under load.

Exercises the full logger pipeline:
  emit → QueueHandler → PIISecretFilter → QueueListener → FileHandler / StreamHandler

Three scenarios isolate different bottlenecks:
  1. High-frequency clean records (baseline throughput)
  2. High-frequency PII-heavy records (filter cost)
  3. Concurrent threads sharing one Logger (lock + queue contention)

Run:
    uv run python -m cProfile -s cumulative examples/profile_logger_load.py
    uv run python -m cProfile -s tottime   examples/profile_logger_load.py
"""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from boti.core.logger import Logger
from boti.core.models import LoggerConfig

N_RECORDS = 5_000   # records per single-threaded scenario
N_THREADS = 8       # concurrent writers for the contention scenario
N_PER_THREAD = 500  # records emitted by each thread


# ---------------------------------------------------------------------------
# Shared payload fixtures
# ---------------------------------------------------------------------------

_CLEAN_EXTRA: dict[str, Any] = {
    "user_id": "u-99",
    "action": "read",
    "resource": "dataset/2024/q1.parquet",
    "latency_ms": 42,
    "tags": ["ok", "cached"],
}

_PII_EXTRA: dict[str, Any] = {
    "context": {
        "token": "eyJhbGciOiJIUzI1NiJ9.payload.sig",
        "user": {
            "email": "alice@example.com",
            "password": "hunter2",
            "profile": {
                "api_key": "sk-live-XXXXXXXXXXXX",
                "access_key": "AKIAIOSFODNN7EXAMPLE",
            },
        },
        "session": {
            "auth_token": "Bearer tok-abc123",
            "authorization": "Basic dXNlcjpwYXNz",
        },
    },
    "pipeline_stage": "ingest",
    "records_processed": 1000,
}


# ---------------------------------------------------------------------------
# Scenario helpers
# ---------------------------------------------------------------------------

def _make_logger(tmp: Path, name: str) -> Logger:
    config = LoggerConfig(
        log_dir=tmp / "logs",
        logger_name=name,
        log_file=name.replace(".", "_"),
        debug=False,
    )
    return Logger(config)


def bench_clean_records(logger: Logger) -> None:
    """Baseline: no PII — measures pure emit + queue overhead."""
    for i in range(N_RECORDS):
        logger.info("event processed record_id=%d", extra=_CLEAN_EXTRA)


def bench_pii_records(logger: Logger) -> None:
    """PII-heavy: every record triggers full recursive redaction."""
    for i in range(N_RECORDS):
        logger.warning("sensitive operation", extra=_PII_EXTRA)


def bench_mixed_records(logger: Logger) -> None:
    """Alternates clean / PII — realistic production mix."""
    for i in range(N_RECORDS):
        if i % 4 == 0:
            logger.warning("sensitive operation", extra=_PII_EXTRA)
        else:
            logger.info("event processed", extra=_CLEAN_EXTRA)


def bench_concurrent(logger: Logger) -> None:
    """Multiple threads emit simultaneously — stresses queue + lock contention."""
    barrier = threading.Barrier(N_THREADS)
    errors: list[Exception] = []

    def worker(tid: int) -> None:
        try:
            barrier.wait()  # start all threads at the same moment
            for i in range(N_PER_THREAD):
                if i % 5 == 0:
                    logger.warning("thread %d pii record %d", tid, extra=_PII_EXTRA)
                else:
                    logger.info("thread %d clean record %d", tid, extra=_CLEAN_EXTRA)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(N_THREADS)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    if errors:
        raise RuntimeError(f"Concurrent bench had {len(errors)} error(s): {errors[0]}")


def bench_cache_hits(tmp: Path) -> None:
    """
    Exercises Logger.default_logger() cache lookup path under repeated calls.
    The same (log_dir, name, file, level) key is used each time — should always
    be a cache hit after the first call. Measures lock + OrderedDict.move_to_end overhead.
    """
    for _ in range(N_RECORDS):
        Logger.default_logger(
            log_dir=tmp / "logs",
            logger_name="profile.cache",
            base_dir=tmp,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)

        single_logger = _make_logger(tmp, "profile.single")
        concurrent_logger = _make_logger(tmp, "profile.concurrent")

        # Allow the QueueListener to warm up before timing
        single_logger.info("warmup")
        time.sleep(0.05)

        total_concurrent = N_THREADS * N_PER_THREAD

        scenarios: list[tuple[str, int, Any]] = [
            ("clean records (baseline)",        N_RECORDS,          lambda: bench_clean_records(single_logger)),
            ("PII-heavy records",               N_RECORDS,          lambda: bench_pii_records(single_logger)),
            ("mixed records (1-in-4 PII)",      N_RECORDS,          lambda: bench_mixed_records(single_logger)),
            (f"concurrent ({N_THREADS}×{N_PER_THREAD})", total_concurrent, lambda: bench_concurrent(concurrent_logger)),
            ("default_logger() cache hits",     N_RECORDS,          lambda: bench_cache_hits(tmp)),
        ]

        print(f"Logger end-to-end load profile\n")
        for label, n, fn in scenarios:
            t0 = time.perf_counter()
            fn()
            elapsed = time.perf_counter() - t0
            print(f"  {label:<40s}  {n:>6,} records  {elapsed*1000:7.1f} ms  ({n/elapsed:,.0f} rec/s)")

        # Flush the queue before exit
        time.sleep(0.1)
    print()


if __name__ == "__main__":
    main()
