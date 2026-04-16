"""
Profile: bulk path validation under load.

Exercises is_secure_path(), is_valid_identifier(), and is_valid_dotted_identifier()
at high call rates. These are called on every sandboxed file operation and every
dynamic name in boti. This profile reveals the cost of Path.resolve() (syscall-bound)
vs pure regex validation (CPU-bound).

Run:
    uv run python -m cProfile -s cumulative examples/profile_path_validation.py
    uv run python -m cProfile -s tottime   examples/profile_path_validation.py
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from boti.core.security import (
    is_secure_path,
    is_valid_dotted_identifier,
    is_valid_identifier,
)

N = 50_000  # calls per scenario


# ---------------------------------------------------------------------------
# Fixtures — built once, reused across iterations
# ---------------------------------------------------------------------------

def _build_fixtures(tmp: Path) -> tuple[list[Path], list[Path], list[Path]]:
    """
    Returns (allowed_dirs, safe_paths, unsafe_paths).

    safe_paths are all inside allowed_dirs.
    unsafe_paths attempt traversal or are outside the sandbox.
    """
    sandbox = tmp / "sandbox"
    sandbox.mkdir()
    other = tmp / "other"
    other.mkdir()

    allowed_dirs = [sandbox, tmp / "extra_a", tmp / "extra_b"]
    for d in allowed_dirs[1:]:
        d.mkdir()

    safe_paths = [
        sandbox / "data" / f"file_{i}.parquet"
        for i in range(20)
    ]
    unsafe_paths = [
        other / "secret.txt",
        tmp / ".." / "etc" / "passwd",
        sandbox / ".." / ".." / "etc" / "shadow",
        Path("/etc/hosts"),
        Path("/tmp/evil.sh"),
    ]
    return allowed_dirs, safe_paths, unsafe_paths


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def bench_secure_path_safe(allowed_dirs: list[Path], safe_paths: list[Path]) -> None:
    """All paths resolve to True — typical hot path during normal I/O."""
    cycle = len(safe_paths)
    for i in range(N):
        is_secure_path(safe_paths[i % cycle], allowed_dirs)


def bench_secure_path_unsafe(allowed_dirs: list[Path], unsafe_paths: list[Path]) -> None:
    """All paths resolve to False — measures early-exit cost for rejections."""
    cycle = len(unsafe_paths)
    for i in range(N):
        is_secure_path(unsafe_paths[i % cycle], allowed_dirs)


def bench_secure_path_mixed(
    allowed_dirs: list[Path], safe_paths: list[Path], unsafe_paths: list[Path]
) -> None:
    """Alternating safe/unsafe — realistic traffic pattern."""
    safe_cycle = len(safe_paths)
    unsafe_cycle = len(unsafe_paths)
    for i in range(N):
        if i % 3 == 0:
            is_secure_path(unsafe_paths[i % unsafe_cycle], allowed_dirs)
        else:
            is_secure_path(safe_paths[i % safe_cycle], allowed_dirs)


def bench_identifier_valid() -> None:
    """Valid Python identifiers — regex match succeeds every time."""
    names = [
        "my_variable", "SomeClass", "_private", "count", "transform_v2",
        "pipeline_stage", "DataLoader", "MAX_RETRIES", "base_url", "api_client",
    ]
    cycle = len(names)
    for i in range(N):
        is_valid_identifier(names[i % cycle])


def bench_identifier_invalid() -> None:
    """Invalid identifiers (numeric start, hyphens, spaces) — regex fails fast."""
    names = [
        "123start", "has-hyphen", "has space", "", "!bang", "dot.name",
        "2fast2furious", "class!", "has\nnewline", "unicode\u00e9",
    ]
    cycle = len(names)
    for i in range(N):
        is_valid_identifier(names[i % cycle])


def bench_dotted_identifier_deep() -> None:
    """Deeply dotted names split into many parts — stress test for the split+all() chain."""
    names = [
        "boti.core.managed_resource.ManagedResource",
        "my.very.deeply.nested.module.path.SomeClass",
        "a.b.c.d.e.f.g.h.i.j",
        "boti.core.logger",
        "single",
        "invalid..double.dot",
        "trailing.",
        ".leading",
    ]
    cycle = len(names)
    for i in range(N):
        is_valid_dotted_identifier(names[i % cycle])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        allowed_dirs, safe_paths, unsafe_paths = _build_fixtures(tmp)

        scenarios = [
            ("is_secure_path — safe (all match)",   lambda: bench_secure_path_safe(allowed_dirs, safe_paths)),
            ("is_secure_path — unsafe (all reject)", lambda: bench_secure_path_unsafe(allowed_dirs, unsafe_paths)),
            ("is_secure_path — mixed traffic",       lambda: bench_secure_path_mixed(allowed_dirs, safe_paths, unsafe_paths)),
            ("is_valid_identifier — valid",          bench_identifier_valid),
            ("is_valid_identifier — invalid",        bench_identifier_invalid),
            ("is_valid_dotted_identifier — deep",    bench_dotted_identifier_deep),
        ]

        print(f"Path validation load profile — {N:,} calls per scenario\n")
        for label, fn in scenarios:
            t0 = time.perf_counter()
            fn()
            elapsed = time.perf_counter() - t0
            print(f"  {label:<42s}  {elapsed*1000:7.1f} ms  ({N/elapsed:,.0f} calls/s)")

    print()


if __name__ == "__main__":
    main()
