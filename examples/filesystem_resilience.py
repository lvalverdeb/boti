"""
FilesystemAdapter retry/backoff and SSRF endpoint allowlisting.

Demonstrates:
  - FilesystemAdapter retrying transient connection errors with exponential backoff
  - max_attempts=1 disabling retry entirely
  - Retry exhaustion re-raising the last transient error
  - fs_endpoint validation rejecting private/reserved IPs (SSRF prevention)
  - add_endpoint_to_allowlist() permitting an operator-approved internal host

Run with:
    uv run python examples/filesystem_resilience.py
"""

from __future__ import annotations

import contextlib
from unittest import mock

import fsspec
from pydantic import ValidationError

from boti.core.filesystem import (
    ENDPOINT_ALLOWLIST,
    FilesystemAdapter,
    FilesystemConfig,
    add_endpoint_to_allowlist,
)


def example_retry_recovers_from_transient_failure() -> None:
    """The adapter retries transient errors and returns the client once it succeeds."""
    memory_fs = fsspec.filesystem("memory")
    attempts = {"count": 0}

    def flaky_create(_config: FilesystemConfig) -> fsspec.AbstractFileSystem:
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise ConnectionError("simulated network blip")
        return memory_fs

    config = FilesystemConfig(fs_type="memory", fs_path="resilient")
    adapter = FilesystemAdapter(config, max_attempts=3, retry_base_delay=0.01)

    with mock.patch("boti.core.filesystem.create_filesystem", side_effect=flaky_create):
        fs = adapter.get_filesystem()

    print(f"  succeeded after {attempts['count']} attempt(s), fs={fs}")
    print()


def example_retry_exhausted() -> None:
    """When every attempt fails, the adapter re-raises the last transient error."""

    def always_fails(_config: FilesystemConfig) -> fsspec.AbstractFileSystem:
        raise TimeoutError("upstream never recovers")

    config = FilesystemConfig(fs_type="memory", fs_path="doomed")
    adapter = FilesystemAdapter(config, max_attempts=2, retry_base_delay=0.01)

    with mock.patch("boti.core.filesystem.create_filesystem", side_effect=always_fails):
        try:
            adapter.get_filesystem()
        except TimeoutError as exc:
            print(f"  raised after exhausting retries: {exc}")
    print()


def example_no_retry_with_max_attempts_one() -> None:
    """max_attempts=1 disables retry — the first failure propagates immediately."""
    calls = {"count": 0}

    def fails_once(_config: FilesystemConfig) -> fsspec.AbstractFileSystem:
        calls["count"] += 1
        raise OSError("connection refused")

    config = FilesystemConfig(fs_type="memory", fs_path="no-retry")
    adapter = FilesystemAdapter(config, max_attempts=1)

    with (
        mock.patch("boti.core.filesystem.create_filesystem", side_effect=fails_once),
        contextlib.suppress(OSError),
    ):
        adapter.get_filesystem()
    print(f"  attempts made: {calls['count']} (no retry with max_attempts=1)")
    print()


def example_ssrf_endpoint_blocked() -> None:
    """fs_endpoint values resolving to private/reserved IPs are rejected."""
    for endpoint in ("http://169.254.169.254/", "http://localhost:9000", "http://10.0.0.5:9000"):
        try:
            FilesystemConfig(fs_type="s3", fs_path="bucket", fs_endpoint=endpoint)
        except ValidationError as exc:
            print(f"  blocked {endpoint!r}: {exc.errors()[0]['msg']}")
    print()


def example_ssrf_endpoint_allowlisted() -> None:
    """Operators can explicitly allowlist a known-internal host at startup."""
    host = "minio.internal:9000"
    try:
        add_endpoint_to_allowlist(host)
        config = FilesystemConfig(fs_type="s3", fs_path="bucket", fs_endpoint=f"http://{host}")
        print(f"  allowlisted endpoint accepted: {config.fs_endpoint}")
    finally:
        ENDPOINT_ALLOWLIST.discard(host)
    print()


def main() -> None:
    print("=== FilesystemAdapter resilience & SSRF examples ===\n")
    example_retry_recovers_from_transient_failure()
    example_retry_exhausted()
    example_no_retry_with_max_attempts_one()
    example_ssrf_endpoint_blocked()
    example_ssrf_endpoint_allowlisted()


if __name__ == "__main__":
    main()
