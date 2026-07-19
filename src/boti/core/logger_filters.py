from __future__ import annotations

import logging
import re
from typing import Any

__all__ = ["PIISecretFilter"]

# Matches sensitive data patterns in strings:
# 1. Explicit assignments:  ``key=value`` or ``key: value``
# 2. Inline description:    ``key is: value``, ``key is value``, ``key is 'value'``
# A plain word like "access_key" in "Loaded access_key from env" does NOT match
# because there is no value following it.
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(?:password|passwd|secret|token|api_key|access_key|auth_token|authorization|bearer)"
    r"(?:\s+is\s*[=:]?\s*|\s*[=:]\s*)\S+"
)


class PIISecretFilter(logging.Filter):
    """Redact secrets from log records without suppressing innocent messages.

    Strategy:
    - Format string (``record.msg``): redacted only when it contains an
      explicit key=value, key: value, or "key is: value" pattern for a
      sensitive keyword.  Plain mentions like "loaded access_key from env"
      are preserved so operational log messages remain useful.
    - Positional args: each string arg is inspected for sensitive assignment
      patterns and redacted if found.
    - Keyword args (dict): values for sensitive keys are always redacted;
      other values are recursively inspected.
    - Structured extra fields on the log record: deep-traversed and redacted
      by key name (sensitive keys) or by value (sensitive assignment patterns).
    """

    _SENSITIVE_KEYS: frozenset[str] = frozenset(
        {
            "password",
            "passwd",
            "secret",
            "token",
            "api_key",
            "access_key",
            "auth_token",
            "authorization",
            "bearer",
        }
    )
    _REDACTED_MESSAGE = "[REDACTED SENSITIVE DATA]"

    # Attributes that are part of LogRecord's standard schema — never redacted
    # as "extra" fields even if their names happen to match a sensitive keyword.
    LOGRECORD_STDLIB_ATTRS: frozenset[str] = frozenset(
        {
            "name",
            "msg",
            "args",
            "levelname",
            "levelno",
            "pathname",
            "filename",
            "module",
            "exc_info",
            "exc_text",
            "stack_info",
            "lineno",
            "funcName",
            "created",
            "msecs",
            "relativeCreated",
            "thread",
            "threadName",
            "processName",
            "process",
            "message",
            "taskName",
        }
    )

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str) and _SENSITIVE_ASSIGNMENT_RE.search(record.msg):
            record.msg = self._REDACTED_MESSAGE
            record.args = ()
        elif record.args:
            record.args = self._redact_args(record.args)

        # Deep-redact structured extra fields added via ``logging.info(..., extra={...})``
        # but leave standard LogRecord attributes alone.
        for key in list(record.__dict__.keys()):
            if key in self.LOGRECORD_STDLIB_ATTRS:
                continue
            if str(key).lower() in self._SENSITIVE_KEYS:
                record.__dict__[key] = "[REDACTED]"
            else:
                record.__dict__[key] = self._redact_value(record.__dict__[key])

        return True

    def _redact_args(self, args: Any) -> Any:
        """Redact sensitive values from positional or keyword format arguments."""
        if isinstance(args, tuple):
            return tuple(self._redact_value(arg) for arg in args)
        if isinstance(args, dict):
            return {
                k: "[REDACTED]" if str(k).lower() in self._SENSITIVE_KEYS else self._redact_value(v)
                for k, v in args.items()
            }
        return args

    # Security-sensitive PII redaction: the dispatch across 8 container types
    # plus the cycle-detection DoS guard is inherent complexity, and this is
    # the one place a refactor mistake could leak secrets into logs. Do not
    # restructure it to satisfy a linter threshold.
    # spaghetti-ignore[high-complexity,excessive-returns]: see above
    def _redact_value(self, value: Any, visited: dict[int, Any] | None = None) -> Any:
        if visited is None:
            visited = {}

        if value is None:
            return None

        if isinstance(value, str):
            return "[REDACTED]" if _SENSITIVE_ASSIGNMENT_RE.search(value) else value

        value_id = id(value)
        if value_id in visited:
            return visited[value_id]

        if isinstance(value, dict):
            redacted: dict[Any, Any] = {}
            visited[value_id] = redacted
            for key, item in value.items():
                if str(key).lower() in self._SENSITIVE_KEYS:
                    redacted[key] = "[REDACTED]"
                else:
                    redacted[key] = self._redact_value(item, visited)
            return redacted

        if isinstance(value, list):
            redacted_list: list[Any] = []
            visited[value_id] = redacted_list
            redacted_list.extend(self._redact_value(item, visited) for item in value)
            return redacted_list

        if isinstance(value, set):
            redacted_set: set[Any] = set()
            visited[value_id] = redacted_set
            for item in value:
                redacted_set.add(self._redact_value(item, visited))
            return redacted_set

        if isinstance(value, tuple):
            redacted_tuple = tuple(self._redact_value(item, visited) for item in value)
            visited[value_id] = redacted_tuple
            return redacted_tuple

        if isinstance(value, frozenset):
            redacted_frozenset = frozenset(self._redact_value(item, visited) for item in value)
            visited[value_id] = redacted_frozenset
            return redacted_frozenset

        return value
