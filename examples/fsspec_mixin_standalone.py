"""
FsspecMixin composed directly onto LifecycleCore, with no ManagedResource.

FsspecMixin is the piece of ManagedResource that owns lazy, thread-safe
fsspec filesystem access. This example composes it with LifecycleCore
directly to build a minimal filesystem-only resource — proving you can pick
up just the filesystem piece without pickle-security gating or the
ResourceConfig/Logger machinery ManagedResource carries.

Demonstrates:
  - Composing FsspecMixin + LifecycleCore into a two-line custom class
  - Lazy filesystem materialization via require_fs()
  - Single-flight factory calls under concurrent access
  - Filesystem release wired into the close() barrier via
    _release_transient_state()
  - RuntimeError when a filesystem is required but never configured
"""

from __future__ import annotations

import threading

from fsspec.implementations.memory import MemoryFileSystem

from boti.core.fsspec_mixin import FsspecMixin
from boti.core.lifecycle import LifecycleCore


class FsHandle(FsspecMixin, LifecycleCore):
    """A minimal fsspec-backed handle — no config model, no logger setup."""

    def __init__(self, fs_factory=None) -> None:
        super().__init__(fs_factory=fs_factory)


def example_lazy_materialization() -> None:
    """The filesystem is not built until first use."""
    with FsHandle(fs_factory=MemoryFileSystem) as handle:
        print(f"  before require_fs(): fs={handle.fs}")
        fs = handle.require_fs()
        fs.pipe_file("/example.txt", b"hello from fsspec")
        print(f"  after require_fs(): fs is materialized: {handle.fs is not None}")
        print(f"  file contents: {fs.cat_file('/example.txt').decode()}")

    print(f"  after close: fs released (owned): {handle.fs is None}")
    print()


def example_single_flight_factory() -> None:
    """Concurrent require_fs() calls trigger the factory exactly once."""
    factory_calls = 0
    factory_lock = threading.Lock()

    def counting_factory():
        nonlocal factory_calls
        with factory_lock:
            factory_calls += 1
        return MemoryFileSystem()

    handle = FsHandle(fs_factory=counting_factory)
    threads = [threading.Thread(target=handle.require_fs) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    print(f"  factory invoked exactly once across 20 concurrent callers: {factory_calls == 1}")
    handle.close()
    print()


def example_require_fs_without_factory_raises() -> None:
    """require_fs() fails fast when nothing was ever configured."""
    handle = FsHandle()
    try:
        handle.require_fs()
    except RuntimeError as exc:
        print(f"  require_fs() raised as expected: {exc}")
    finally:
        handle.close()
    print()


def main() -> None:
    print("=== FsspecMixin standalone examples ===\n")
    example_lazy_materialization()
    example_single_flight_factory()
    example_require_fs_without_factory_raises()


if __name__ == "__main__":
    main()
