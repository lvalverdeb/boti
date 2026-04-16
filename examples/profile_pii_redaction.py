"""
Profile: PII redaction under load.

Exercises PIISecretFilter.filter() directly — the hot path for every log record
that passes through boti's logger. Uses deeply nested, PII-heavy payloads to
stress the recursive traversal and string-scanning logic.

Run:
    uv run python -m cProfile -s cumulative examples/profile_pii_redaction.py
    uv run python -m cProfile -s tottime   examples/profile_pii_redaction.py
"""

from __future__ import annotations

import logging
import time
from typing import Any

from boti.core.logger_filters import PIISecretFilter

# ---------------------------------------------------------------------------
# Payload factories
# ---------------------------------------------------------------------------

def _make_shallow_pii_record() -> dict[str, Any]:
    """Flat dict with a mix of sensitive and benign keys."""
    return {
        "user_id": "u-12345",
        "email": "alice@example.com",
        "token": "eyJhbGciOiJIUzI1NiJ9.payload.signature",
        "action": "login",
        "ip": "203.0.113.42",
        "password": "hunter2",
        "session_id": "sess-abcdef",
        "api_key": "sk-live-XXXXXXXXXXXXXXXXXXXX",
    }


def _make_deep_nested_pii_record(depth: int = 6) -> dict[str, Any]:
    """Recursively nested dict — worst-case for _redact_value traversal."""
    if depth == 0:
        return {
            "secret": "bottom-level-secret",
            "value": 42,
            "tags": ["safe", "authorization:bearer abc123", "public"],
        }
    return {
        "level": depth,
        "meta": {"token": f"tok-{depth}", "label": f"node-{depth}"},
        "children": [
            _make_deep_nested_pii_record(depth - 1),
            {"bearer": "Bearer xyz", "count": depth * 10},
        ],
        "extras": {
            "access_key": f"AKIA{'X' * 16}",
            "region": "us-east-1",
            "inner": _make_deep_nested_pii_record(depth - 1) if depth > 2 else None,
        },
    }


def _make_wide_record(width: int = 50) -> dict[str, Any]:
    """Many keys at the same level — stresses the key-scan loop."""
    record: dict[str, Any] = {}
    sensitive_keys = ["password", "token", "api_key", "secret", "authorization"]
    for i in range(width):
        key = sensitive_keys[i % len(sensitive_keys)] if i % 7 == 0 else f"field_{i}"
        record[key] = f"value_{i}"
    return record


def _make_log_record(msg: str, extra: dict[str, Any]) -> logging.LogRecord:
    record = logging.LogRecord(
        name="profile.pii",
        level=logging.INFO,
        pathname=__file__,
        lineno=0,
        msg=msg,
        args=(),
        exc_info=None,
    )
    record.__dict__.update(extra)
    return record


# ---------------------------------------------------------------------------
# Benchmark scenarios
# ---------------------------------------------------------------------------

N = 10_000  # records per scenario


def bench_shallow(filt: PIISecretFilter) -> None:
    payload = _make_shallow_pii_record()
    for _ in range(N):
        rec = _make_log_record("user action", {"payload": payload})
        filt.filter(rec)


def bench_deep_nested(filt: PIISecretFilter) -> None:
    payload = _make_deep_nested_pii_record(depth=6)
    for _ in range(N):
        rec = _make_log_record("nested payload", {"data": payload})
        filt.filter(rec)


def bench_wide_flat(filt: PIISecretFilter) -> None:
    payload = _make_wide_record(width=50)
    for _ in range(N):
        rec = _make_log_record("wide record", {"context": payload})
        filt.filter(rec)


def bench_args_tuple(filt: PIISecretFilter) -> None:
    """Exercises _redact_args with positional format args."""
    for _ in range(N):
        rec = _make_log_record(
            "user=%s token=%s action=%s",
            {},
        )
        rec.args = ("alice", "tok-secret-xyz", "delete")
        filt.filter(rec)


def bench_clean_records(filt: PIISecretFilter) -> None:
    """Baseline: records with no sensitive data — measures filter overhead on clean traffic."""
    payload = {"user_id": "u-1", "action": "read", "count": 7, "tags": ["ok", "verified"]}
    for _ in range(N):
        rec = _make_log_record("benign event", {"info": payload})
        filt.filter(rec)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    filt = PIISecretFilter()

    scenarios = [
        ("shallow PII dict", bench_shallow),
        ("deep nested (depth=6)", bench_deep_nested),
        ("wide flat (50 keys)", bench_wide_flat),
        ("args tuple", bench_args_tuple),
        ("clean records (baseline)", bench_clean_records),
    ]

    print(f"PIISecretFilter load profile — {N:,} records per scenario\n")
    for label, fn in scenarios:
        t0 = time.perf_counter()
        fn(filt)
        elapsed = time.perf_counter() - t0
        print(f"  {label:<30s}  {elapsed*1000:7.1f} ms  ({N/elapsed:,.0f} rec/s)")

    print()


if __name__ == "__main__":
    main()
