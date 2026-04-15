from __future__ import annotations

import logging
from typing import Any, Mapping


class PIISecretFilter(logging.Filter):
    """Redact obvious secrets and sensitive fields from log records."""

    _SENSITIVE_KEYS = {
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
    _REDACTED_MESSAGE = "[REDACTED SENSITIVE DATA]"

    def filter(self, record: logging.LogRecord) -> bool:
        if self._contains_sensitive_data(record.msg) or self._contains_sensitive_data(record.args):
            record.msg = self._REDACTED_MESSAGE
            record.args = ()

        for key in list(record.__dict__.keys()):
            if str(key).lower() in self._SENSITIVE_KEYS:
                record.__dict__[key] = "[REDACTED]"
            else:
                record.__dict__[key] = self._redact_value(record.__dict__[key])

        return True

    def _redact_value(self, value: Any, visited: dict[int, Any] | None = None) -> Any:
        if visited is None:
            visited = {}

        if value is None:
            return None

        if isinstance(value, str):
            return "[REDACTED]" if self._contains_sensitive_data(value) else value

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

    def _contains_sensitive_data(self, value: Any, visited: set[int] | None = None) -> bool:
        if visited is None:
            visited = set()

        if id(value) in visited:
            return False

        visited.add(id(value))

        if value is None:
            return False

        if isinstance(value, str):
            lowered = value.lower()
            return any(marker in lowered for marker in self._SENSITIVE_KEYS)

        if isinstance(value, Mapping):
            return any(
                self._contains_sensitive_data(key, visited) or self._contains_sensitive_data(item, visited)
                for key, item in value.items()
            )

        if isinstance(value, tuple | list | set | frozenset):
            return any(self._contains_sensitive_data(item, visited) for item in value)

        return False
