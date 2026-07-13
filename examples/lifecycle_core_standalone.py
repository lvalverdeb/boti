"""
LifecycleCore used standalone, with no ManagedResource in sight.

LifecycleCore is the piece of ManagedResource that was extracted so it could
back completely unrelated classes — like boti.core.Agent (see
agent_basics.py) — without dragging in fsspec lazy-init or pickle-security
gating. This example proves that decoupling is real: a plain class built
directly on LifecycleCore gets the full sync+async close barrier, context
manager protocol, and GC leak warning, with zero other boti imports.

Demonstrates:
  - A custom class composed from LifecycleCore alone (no config system needed)
  - Sync and async context managers
  - The close() barrier: a second thread blocks until the first's cleanup
    has actually finished, rather than racing past it
  - Reentrant close() from within a cleanup hook (no deadlock)
  - GC leak warning via weakref.finalize
"""

from __future__ import annotations

import asyncio
import gc
import threading
import warnings

from boti.core.lifecycle import LifecycleCore


class WorkerHandle(LifecycleCore):
    """A minimal "connection handle" with nothing but lifecycle guarantees.

    No config model, no logger machinery required — LifecycleCore's
    logger/debug class defaults (None/False) make this work out of the box.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.tasks_run = 0
        self.released = False
        super().__init__()

    def run_task(self) -> int:
        self._assert_open()
        self.tasks_run += 1
        return self.tasks_run

    def _cleanup(self) -> None:
        self.released = True

    async def _acleanup(self) -> None:
        await asyncio.sleep(0)
        self.released = True


def example_sync_context() -> None:
    """Basic synchronous context manager usage."""
    with WorkerHandle("sync-worker") as handle:
        handle.run_task()
        handle.run_task()
        print(f"  inside context: tasks_run={handle.tasks_run}, closed={handle.closed}")

    print(f"  after exit: closed={handle.closed}, released={handle.released}")
    print()


async def example_async_context() -> None:
    """Basic asynchronous context manager usage."""
    async with WorkerHandle("async-worker") as handle:
        handle.run_task()
        print(f"  inside context: tasks_run={handle.tasks_run}, closed={handle.closed}")

    print(f"  after exit: closed={handle.closed}, released={handle.released}")
    print()


def example_concurrent_close_barrier() -> None:
    """A second thread's close() blocks until the first closer's cleanup
    has actually finished — close() is a barrier, not a race."""

    cleanup_started = threading.Event()
    release_cleanup = threading.Event()

    class SlowHandle(WorkerHandle):
        def _cleanup(self) -> None:
            cleanup_started.set()
            release_cleanup.wait(timeout=5)
            super()._cleanup()

    handle = SlowHandle("slow-worker")
    first = threading.Thread(target=handle.close)
    first.start()
    cleanup_started.wait(timeout=5)

    second_returned = threading.Event()

    def second_closer() -> None:
        handle.close()  # must block until the first closer's cleanup is done
        second_returned.set()

    second = threading.Thread(target=second_closer)
    second.start()
    second.join(timeout=0.2)
    print(f"  second close() still blocked while cleanup runs: {not second_returned.is_set()}")

    release_cleanup.set()
    first.join(timeout=5)
    second.join(timeout=5)
    print(f"  second close() returned once cleanup finished: {second_returned.is_set()}")
    print(f"  released={handle.released}, closed={handle.closed}")
    print()


def example_reentrant_close_from_cleanup() -> None:
    """A _cleanup() hook that calls close() again returns immediately
    instead of deadlocking on its own barrier."""

    class ReentrantHandle(WorkerHandle):
        def _cleanup(self) -> None:
            self.close()  # reentrant, same thread: must be a no-op, not a deadlock
            super()._cleanup()

    handle = ReentrantHandle("reentrant-worker")
    closer = threading.Thread(target=handle.close)
    closer.start()
    closer.join(timeout=5)
    print(f"  reentrant close() did not deadlock: {not closer.is_alive()}")
    print(f"  closed={handle.closed}, released={handle.released}")
    print()


def example_gc_leak_warning() -> None:
    """A handle that is garbage collected without close() warns via
    weakref.finalize, so leaked resources are never silent."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _leaked = WorkerHandle("forgotten-worker")
        _leaked = None
        gc.collect()

        leak_warnings = [w for w in caught if "garbage collected" in str(w.message)]
        if leak_warnings:
            print(f"  leak warning issued: {leak_warnings[0].message}")
        else:
            print("  (no warning — handle was not collected)")
    print()


def main() -> None:
    print("=== LifecycleCore standalone examples ===\n")
    example_sync_context()
    asyncio.run(example_async_context())
    example_concurrent_close_barrier()
    example_reentrant_close_from_cleanup()
    example_gc_leak_warning()


if __name__ == "__main__":
    main()
