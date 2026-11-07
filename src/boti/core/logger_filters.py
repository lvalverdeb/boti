from __future__ import annotations

import dataclasses
import logging
import re
from collections.abc import Mapping
from typing import Any

__all__ = ["PIISecretFilter"]

# The keyword vocabulary, in one place. Three views of it are compiled below:
#
# * _MESSAGE_KEYWORD_RE  — whole-word match against record.msg (the raw format
#   string). A message that *mentions* a keyword is redacted wholesale, because
#   the secret may arrive as a later %s arg. Coarse on purpose.
# * _SENSITIVE_ASSIGNMENT_RE — ``key=value`` / ``key: value`` / ``"key": "v"`` /
#   ``key is value`` inside a string value; only the value is replaced, so the
#   key survives as context.
# * _KEY_KEYWORD_RE — substring match against a *key name* (dict key, extra
#   field, object attribute) after normalising case and separators, so
#   ``db_password``, ``AWS_SECRET_ACCESS_KEY`` and ``apiToken`` all count.
#   ``session``/``cookie`` appear only here: as key names they hold secrets,
#   but as words in a message ("session started") they would redact far too
#   much ordinary logging.
_VALUE_KEYWORDS = (
    r"password|passwd|secret|token|api[-_ ]?key|access[-_ ]?key|auth[-_ ]?token"
    r"|authorization|bearer|private[-_ ]?key|client[-_ ]?secret"
)
_KEY_KEYWORDS = (
    r"password|passwd|secret|token|api_?key|access_?key|auth_?token"
    r"|authorization|bearer|private_?key|client_?secret|credential|session|cookie"
)
# Inside text an assignment delimiter follows the keyword, which removes the
# ambiguity that keeps the key-only names out of the message rule:
# "session: abc" is a credential, "session started" is not.
_ASSIGNMENT_KEYWORDS = rf"{_VALUE_KEYWORDS}|credential|session|cookie"

# No word boundary *before* the keyword: ``db_password``, ``accessToken`` and
# ``x-api-key`` must count. After it, either a boundary (a plain mention) or a
# run of name characters ending in an assignment delimiter (``db_password_v2=``,
# ``api_keys:``) — so "processed 5 tokens" is not blanked but "tokens=%s" is.
_MESSAGE_KEYWORD_RE = re.compile(rf"(?i)(?:{_VALUE_KEYWORDS})(?:[\w-]*[=:]|\b)")
_KEY_KEYWORD_RE = re.compile(rf"(?:{_KEY_KEYWORDS})")

# Group 1 keeps the key and delimiter; the value — a quoted string, a
# ``Bearer <token>`` pair, or everything up to a separator/newline — is
# replaced. Running to a separator rather than to whitespace over-redacts the
# rest of a free-text line ("password=a b c" loses "b c"), which is the safe
# direction. ``[\w-]*`` lets a suffixed key (``db_password_v2``) through to the
# delimiter; "passwords=5" losing its value is the same safe direction. The
# closing quote of a key may be backslash-escaped (JSON embedded in JSON).
# ``[REDACTED]`` is itself an accepted value so a second pass is a no-op
# rather than ``[REDACTED]]``.
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    rf"(?i)((?:{_ASSIGNMENT_KEYWORDS})[\w-]*(?:\s+is\s*[=:]?\s*|(?:\\*[\"'])?\s*[=:]\s*))"
    r"(?:\[REDACTED\]|(?:bearer|basic)\s+[^\s,;}\]]+|\"[^\"]*\"|'[^']*'|[^,;}\]\n]+)"
)

# ``scheme://user:pass@host`` — the userinfo section is a credential whatever
# the scheme (postgresql, redis, amqp, s3, http …).
_URL_CREDENTIAL_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]*:[^@\s/]+@")

# Cheap pre-check run before the substitutions above and the message rule: a
# superset of every fragment they can match, so a string that fails it cannot
# contain anything to redact. Most strings fail it. Case-sensitive on purpose
# and applied to ``text.lower()``: an ``(?i)`` alternation cannot use ``re``'s
# first-character prefilter (ignore-case literals form no charset prefix) and
# measured ~3.5× slower than lower-then-search.
_TEXT_TRIGGER_RE = re.compile(
    r"password|passwd|secret|token|key|auth|bearer|credential|session|cookie|://"
)


def _may_need_redaction(text: str) -> bool:
    return _TEXT_TRIGGER_RE.search(text.lower()) is not None


_REDACTED = "[REDACTED]"
# An object with more fields than this is not inspected (cost cap); it is
# replaced rather than passed through, because "too big to check" is not
# "safe".
_MAX_OBJECT_FIELDS = 200


# Key names repeat across records (``user_id``, ``latency_ms`` …); the verdict
# is cached per distinct name. A plain dict rather than lru_cache: this runs
# once per key per record and the wrapper's call overhead was measurable.
# Bounded by clearing; a race between threads only recomputes a verdict.
_KEY_VERDICTS: dict[str, bool] = {}
_KEY_VERDICTS_MAX = 4096


def _is_sensitive_key(key: Any) -> bool:
    name = key if isinstance(key, str) else str(key)
    verdict = _KEY_VERDICTS.get(name)
    if verdict is None:
        if len(_KEY_VERDICTS) >= _KEY_VERDICTS_MAX:
            _KEY_VERDICTS.clear()
        normalized = name.lower().replace("-", "_").replace(" ", "_")
        verdict = _KEY_VERDICTS[name] = _KEY_KEYWORD_RE.search(normalized) is not None
    return verdict


class _Scan:
    """Per-record traversal state: cycle map and whether anything was redacted.

    A plain ``__slots__`` class rather than a dataclass: one is built per
    record, on the hot path.
    """

    __slots__ = ("changed", "keepalive", "visited")

    def __init__(self) -> None:
        self.visited: dict[int, Any] = {}
        self.changed = False
        # ``visited`` is keyed by id(); a temporary built during the scan (a
        # model_dump() result) must stay alive until the scan ends, or a later
        # object can reuse its id and be served the stale redacted copy.
        self.keepalive: list[Any] = []


class PIISecretFilter(logging.Filter):
    """Redact secrets from log records, favouring over-redaction over a leak.

    "PII" is kept in the name for API stability; the filter targets
    *credentials* — passwords, tokens, keys, authorization headers, URL
    userinfo — not personal data such as emails or card numbers.

    Covered shapes:

    - Format string (``record.msg``): redacted wholesale whenever it mentions
      a sensitive keyword, even a bare mention with no value adjacent. The
      secret may arrive as a later ``%s`` arg, so adjacency cannot be
      required. URL credentials in the format string are scrubbed first so a
      URL alone does not trigger whole-message redaction.
    - String values (args, extra fields, nested anywhere): ``key=value``,
      ``key: value``, ``"key": "value"``, ``key is value`` have the value
      replaced; ``scheme://user:pass@host`` has the userinfo replaced.
    - Key names: any dict key, extra field or object attribute whose name
      *contains* a sensitive keyword (``db_password``, ``apiToken``,
      ``X-Api-Key``) has its value replaced regardless of content.
    - Containers: dict/Mapping, list, tuple, set, frozenset, nested to any
      depth, with cycle detection.
    - ``bytes``/``bytearray``: scanned as UTF-8 text; replaced by the
      redacted ``str`` when something was found.
    - Objects: dataclasses and pydantic models are rendered as redacted
      dicts; other objects with a ``__dict__`` are inspected and rendered as a
      dict only if something was redacted. ``SecretStr``-like values (anything
      with ``get_secret_value``) are left alone — their repr is already masked.
    - Exception text (``exc_info``/``exc_text``): rendered, scrubbed, pinned
      on ``exc_text``.

    Not covered: secrets with no keyword context (a bare access key id) and
    objects that hide state behind ``__slots__`` or properties.
    """

    _REDACTED_MESSAGE = "[REDACTED SENSITIVE DATA]"

    # Attributes that are part of LogRecord's standard schema — never redacted
    # as "extra" fields even if their names happen to match a sensitive keyword.
    # ``message``/``asctime`` are set on the shared record by whichever
    # formatter runs first, so a later destination's formatter must not treat
    # them as caller-supplied extras either.
    LOGRECORD_STDLIB_ATTRS: frozenset[str] = frozenset(
        {
            "name",
            "msg",
            "args",
            "asctime",
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
        scan = _Scan()
        if isinstance(record.msg, str) and _may_need_redaction(record.msg):
            if "://" in record.msg:
                record.msg = _URL_CREDENTIAL_RE.sub(rf"\1{_REDACTED}@", record.msg)
            if _MESSAGE_KEYWORD_RE.search(record.msg):
                record.msg = self._REDACTED_MESSAGE
                record.args = ()
        if record.args:
            record.args = self._redact_args(record.args, scan)

        # Deep-redact structured extra fields added via ``logging.info(..., extra={...})``
        # but leave standard LogRecord attributes and boti's own routing
        # attributes (``_boti_*``, never rendered) alone.
        attrs = record.__dict__
        for key in list(attrs):
            if key in self.LOGRECORD_STDLIB_ATTRS or key.startswith("_boti_"):
                continue
            if _is_sensitive_key(key):
                attrs[key] = _REDACTED
            else:
                attrs[key] = self._redact_value(attrs[key], scan)

        self._redact_exception(record)
        return True

    # Formatters cache the rendered traceback on ``exc_text``; a formatter
    # of our own would need the same fallback instance, so share one here.
    _EXC_FORMATTER = logging.Formatter()

    def _redact_exception(self, record: logging.LogRecord) -> None:
        """Redact secrets embedded in an exception's message or traceback.

        Exception text bypasses the ``msg``/``args`` checks above: it is only
        rendered by the formatter, after this filter has run — and
        ``QueueHandler.prepare()`` bakes it into ``msg`` for the listener's
        handlers, so the leak would be permanent. Render it here, scrub it, and
        pin the result on ``exc_text`` (which every formatter prefers over
        re-rendering ``exc_info``).
        """
        # The stdlib has already normalised ``exc_info`` to a tuple (or None).
        if record.exc_info and record.exc_info[0] is not None:
            text = self._EXC_FORMATTER.formatException(record.exc_info)
            record.exc_text = self._redact_text(text, _Scan())
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = self._redact_text(record.exc_text, _Scan())

    def _redact_args(self, args: Any, scan: _Scan) -> Any:
        """Redact sensitive values from positional or keyword format arguments."""
        if isinstance(args, tuple):
            return tuple(self._redact_value(arg, scan) for arg in args)
        if isinstance(args, Mapping):
            return self._redact_mapping(args, scan)
        return args

    @staticmethod
    def _redact_text(text: str, scan: _Scan) -> str:
        if not _may_need_redaction(text):
            return text
        redacted = _URL_CREDENTIAL_RE.sub(rf"\1{_REDACTED}@", text)
        redacted = _SENSITIVE_ASSIGNMENT_RE.sub(rf"\g<1>{_REDACTED}", redacted)
        if redacted != text:
            scan.changed = True
        return redacted

    # Security-sensitive redaction: the dispatch across every value shape a
    # caller can log is inherent complexity, and this is the one place a
    # refactor mistake could leak secrets into logs. Do not restructure it to
    # satisfy a linter threshold.
    # spaghetti-ignore[high-complexity,excessive-returns]: see above
    def _redact_value(self, value: Any, scan: _Scan | None = None) -> Any:
        if scan is None:
            scan = _Scan()
        # Tuples, not unions: isinstance against a tuple is the fast C path,
        # and this dispatch runs once per leaf of every record.
        if isinstance(value, str):
            # Trigger check inlined: most strings fail it, and this is the
            # single most frequent leaf.
            if _TEXT_TRIGGER_RE.search(value.lower()) is None:
                return value
            return self._redact_text(value, scan)
        if value is None or isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, (bytes, bytearray)):
            text = bytes(value).decode("utf-8", errors="replace")
            redacted = self._redact_text(text, scan)
            return redacted if redacted != text else value
        if id(value) in scan.visited:
            return scan.visited[id(value)]
        if isinstance(value, Mapping):
            return self._redact_mapping(value, scan)
        if isinstance(value, (list, tuple, set, frozenset)):
            return self._redact_collection(value, scan)
        return self._redact_object(value, scan)

    def _redact_mapping(self, mapping: Mapping[Any, Any], scan: _Scan) -> dict[Any, Any]:
        redacted: dict[Any, Any] = {}
        scan.visited[id(mapping)] = redacted
        for key, item in mapping.items():
            if _is_sensitive_key(key):
                redacted[key] = _REDACTED
                scan.changed = True
            else:
                redacted[key] = self._redact_value(item, scan)
        return redacted

    def _redact_collection(self, value: Any, scan: _Scan) -> Any:
        # Mutable containers are registered before they are filled so a cycle
        # back to them resolves to the (partially built) redacted copy.
        if isinstance(value, list):
            redacted_list: list[Any] = []
            scan.visited[id(value)] = redacted_list
            redacted_list.extend(self._redact_value(item, scan) for item in value)
            return redacted_list
        if isinstance(value, set):
            redacted_set: set[Any] = set()
            scan.visited[id(value)] = redacted_set
            redacted_set.update(self._redact_value(item, scan) for item in value)
            return redacted_set
        items = tuple(self._redact_value(item, scan) for item in value)
        result: Any = frozenset(items) if isinstance(value, frozenset) else items
        scan.visited[id(value)] = result
        return result

    def _redact_object(self, value: Any, scan: _Scan) -> Any:
        """Inspect an arbitrary object's fields; never pass an uninspected one through."""
        if hasattr(value, "get_secret_value"):
            return value  # SecretStr-like: repr is already masked
        try:
            structured, fields = self._object_fields(value)
        except Exception:
            scan.changed = True
            return f"[UNINSPECTABLE {type(value).__name__}]"
        if fields is None:
            return value
        if len(fields) > _MAX_OBJECT_FIELDS:
            scan.changed = True
            return f"[UNINSPECTABLE {type(value).__name__}: too many fields]"
        changed_before = scan.changed
        scan.changed = False
        # Register the object before recursing so a cycle back to it resolves
        # to the copy being built. ``fields`` is a temporary for dataclasses
        # and models (kept alive: visited is keyed by id), the live __dict__
        # for plain objects.
        scan.keepalive.append(fields)
        scan.visited[id(value)] = redacted = {}
        redacted.update(self._redact_mapping(fields, scan))
        changed_here = scan.changed
        scan.changed = changed_before or changed_here
        if structured or changed_here:
            return redacted
        # Nothing to redact in a plain object: pass it through, and record that
        # verdict so a second occurrence in the same record gets the same one
        # (it used to be served the raw object because "nothing changed" on
        # the cached revisit).
        scan.visited[id(value)] = value
        return value

    @staticmethod
    def _object_fields(value: Any) -> tuple[bool, dict[str, Any] | None]:
        """``(is_structured, fields)``; ``fields`` is None for opaque objects."""
        if dataclasses.is_dataclass(value) and not isinstance(value, type):
            return True, {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            dumped = model_dump()
            return True, dumped if isinstance(dumped, dict) else None
        instance_dict = getattr(value, "__dict__", None)
        if isinstance(instance_dict, dict) and instance_dict:
            return False, instance_dict
        return False, None
