# PRD-001 — Secret redaction that fails closed

| | |
|---|---|
| Priority | P0 |
| Target | 1.1.1 (patch) |
| Owner | logger maintainers |
| Modules | `src/boti/core/logger_filters.py`, `src/boti/core/logger.py` |
| Status | Done — shipped in 1.1.1 |

## 1. Problem

`PIISecretFilter` is documented as "favouring over-redaction over a leak". Measured against realistic inputs it does the opposite. Probed on `ebed01f`, every line below reached the destination verbatim:

```
creds={'aws_secret_access_key': 'SECRET1', 'db_password': 'SECRET2',
       'api_token': 'SECRET3', 'client_secret': 'SECRET4'}       # compound key names
payload={"password":"SECRET5","token":"SECRET6"}                  # JSON-quoted keys
raw b'password=SECRET8'                                           # bytes never scanned
url https://user:SECRET11@host/x                                  # URL userinfo
conn postgresql://app:SECRET12@db:5432/x                          # connection string
```

Root causes (`logger_filters.py`):

- `_SENSITIVE_KEYS` (l.58) is matched by **exact** lowercase equality, so any key that *contains* a keyword (`db_password`, `AWS_SECRET_ACCESS_KEY`) is treated as benign and its value is scanned by the assignment regex instead — which only fires on `key=value` text.
- `_SENSITIVE_ASSIGNMENT_RE` (l.16) requires the keyword to be immediately followed by whitespace, `=`, `:` or `is`; a closing quote between keyword and delimiter (`"password":`) defeats it.
- `_redact_value` (l.164) handles `str`, `dict`, `list`, `set`, `tuple`, `frozenset` and returns everything else untouched: `bytes`, `bytearray`, pydantic models, dataclasses, `SimpleNamespace`.
- There is no rule for credentials embedded in URLs.

Separately, **records emitted after `Logger.close()` bypass the filter entirely**. `close()` detaches the `QueueHandler` that carries the filter; the stdlib logger then has no handlers and `logging.lastResort` writes WARNING+ records raw to stderr. Confirmed: after `close()`, `lg.error("db password=hunter2 token=abc")` printed `db password=hunter2 token=abc`.

## 2. Goals

- G1. A secret placed under any key that **contains** a sensitive keyword is redacted, at any depth, in `args`, `extra`, and bound fields.
- G2. A secret in `key=value`, `key: value`, `"key": "value"`, `'key': 'value'`, `key is value` form is redacted whether the surrounding text is Python repr, JSON, YAML, or prose.
- G3. Credentials in URL userinfo (`scheme://user:pass@host`) are redacted to `scheme://[REDACTED]@host`.
- G4. `bytes`/`bytearray` values are scanned as text.
- G5. Objects with a `__dict__`, pydantic models and dataclasses are scanned by their field names and values, not passed through.
- G6. After `Logger.close()`, nothing that `Logger` emits reaches any destination, including stdlib `lastResort`.
- G7. No measurable regression on the clean-record path (`examples/profile_logger_load.py`, "plain records" benchmark) beyond 5 %.

## 3. Non-goals

- Detecting high-entropy strings with no keyword context (e.g. a bare `AKIA…` access key id). Entropy scanning is a different product with a different false-positive profile; it may be a later PRD.
- Redacting PII categories other than credentials (emails, card numbers). The class name says PII; the behaviour is secrets. The docstring should say so (see R12).
- Redaction inside exception *objects* beyond the rendered traceback text already handled by `_redact_exception`.

## 4. Redaction corpus

A shared, versioned fixture `tests/fixtures/redaction_corpus.py` that PRD-003 reuses. Each entry is `(input, must_not_contain)`. Minimum coverage:

| Shape | Example |
|---|---|
| Compound keys | `aws_secret_access_key`, `DB_PASSWORD`, `apiToken`, `client_secret`, `x-auth-token`, `authorization_header` |
| Quoted keys | `{"password":"S"}`, `{'token': 'S'}`, `password: "S"` (YAML) |
| Spacing variants | `password = S`, `password= S`, `password:S`, `password is S`, `password is: S` |
| URL userinfo | `https://u:S@h/`, `postgresql://u:S@h:5432/db`, `redis://:S@h`, `amqp://u:S@h` |
| Bytes | `b"password=S"`, `bytearray(b"token: S")` |
| Containers | nested dict → list → tuple → dict with the key at depth 4; a cycle |
| Objects | `SimpleNamespace(password="S")`, a dataclass, a pydantic model with a `str` field named `api_key` |
| Headers | `{"Authorization": "Bearer S"}`, `{"X-Api-Key": "S"}`, `{"Cookie": "session=S"}` |
| Negative | `"this field does not require a password"` is redacted (documented over-redaction); `{"password_policy": "8 chars"}` **is** redacted (contains keyword; accepted cost); `{"passport": "X"}` is **not** redacted |

Every entry's `must_not_contain` token is a random 20-character string so a test cannot pass by coincidence.

## 5. Requirements

### Key matching
- R1. Replace exact-set membership with keyword search: a key is sensitive if `_SENSITIVE_KEYWORD_RE.search(str(key))` matches, after normalising `-` and spaces to `_`. Applies in `filter()` (extra attrs), `_redact_args` (dict args) and `_redact_value` (dict keys, object fields).
- R2. Add to the keyword list: `private_key`, `client_secret`, `session`, `cookie`, `credential(s)`, `api[-_]?key`, `x[-_]?api[-_]?key`. Keep the list in one place; the existing `_SENSITIVE_KEYS` frozenset is removed.
- R3. `LOGRECORD_STDLIB_ATTRS` remains the allowlist for record attributes; R1 never applies to it.

### Value matching
- R4. `_SENSITIVE_ASSIGNMENT_RE` accepts optional quote characters and whitespace between keyword and delimiter and before the value: `(?i)\b(keyword)\b["']?\s*(?:is\s*)?[=:]\s*["']?\S+`. The replacement redacts the value only, preserving the key (`password=[REDACTED]`), so operators can still see *which* field was present.
- R5. New `_URL_CREDENTIAL_RE = r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^@\s/]+@"` → `\1[REDACTED]@`. Applied to every string value and to `record.msg` **before** the keyword check so a URL in the format string does not trigger whole-message redaction when it could be surgically scrubbed.
- R6. `bytes`/`bytearray` are decoded with `errors="replace"`, scanned with R4/R5, and if changed replaced by the redacted `str` (type change is acceptable; the alternative — re-encoding — can produce invalid bytes).
- R7. Objects: for `dataclasses.is_dataclass(value)` use `dataclasses.fields`; for pydantic `BaseModel` use `model_dump()`; for anything with `__dict__` and no custom `__repr__` on a builtin type, use `vars(value)`. The result is a redacted `dict` (not a copy of the object). `SecretStr` values are left as-is (already masked). Depth/cycle protection via the existing `visited` map. Any exception during object inspection falls back to `"[UNINSPECTABLE <type>]"` — never to the original object.
- R8. `_redact_value` keeps its single-dispatch shape; the new branches are added as small helpers so the function stays within the project's 50-line limit. The existing `spaghetti-ignore[high-complexity,excessive-returns]` justification is updated.

### Message handling
- R9. `filter()` ordering: (1) R5 URL scrub on `record.msg`; (2) existing whole-message keyword rule; (3) args; (4) extras; (5) exception text. The whole-message rule stays as-is — its over-redaction is documented and deliberate.

### Lifecycle
- R10. When `Logger.close()` removes the last `QueueHandler` from the stdlib logger it attaches a `logging.NullHandler()` so `lastResort` never engages. The attached handler is tracked so a later `Logger(config)` with the same name removes it before re-adding the `QueueHandler`.
- R11. `Logger._log` on a closed `Logger` (new `_closed` flag set in `close()`) returns without calling the stdlib logger at all. `bind()` clones share the flag by reference (store it in a one-element list or a small object) so closing the parent silences clones.

### Documentation
- R12. `PIISecretFilter` docstring and README/SDD §3.4 state precisely what is covered (§4 corpus shapes) and what is not (§3 non-goals). The word "PII" is kept for API stability but the first sentence says "secrets".

## 6. Acceptance criteria

- A1. Every corpus entry passes: the `must_not_contain` token does not appear in the formatted record of any destination (file and console both asserted).
- A2. `test_logger_filters.py` is a new file; the filter tests currently embedded in `test_logger.py` move there. Target ≥ 95 % coverage on `logger_filters.py`.
- A3. After `logger.close()`, `logger.error("password=S")` produces no stderr output, no file output, and no `lastResort` invocation (assert `logging.lastResort.handle` not called via monkeypatch).
- A4. `examples/profile_logger_load.py` "plain records" throughput within 5 % of the 1.1.0 baseline (record the baseline number in the PR description); "PII-heavy" may regress up to 30 % (it does more work now).
- A5. `boti check`, all examples, full CI gate pass.
- A6. SDD §3.4, §8 and the three READMEs updated.

## 7. Design notes

- Preserve-key redaction (R4) changes existing outputs from `[REDACTED]` for the whole token to `password=[REDACTED]`. Existing tests that assert the old form are updated; the change is called out in release notes.
- R7's `model_dump()` call on a pydantic model may be expensive for large models. Guard with a size cap (`_MAX_OBJECT_FIELDS = 200`; beyond that emit `"[UNINSPECTABLE: too many fields]"`).
- No new runtime dependency.

## 8. Risks

| Risk | Mitigation |
|---|---|
| Over-redaction of operational fields (`session_id`, `cookie_count`) | Accepted by policy; documented. Operators can rename fields. A later PRD may add an allowlist. |
| Type change for `bytes` values (R6) | Only when redaction occurred; documented. |
| Performance | A4 benchmark gate. |

## 9. Open questions

- Should `ResourceConfig`/`LoggerConfig` expose `extra_sensitive_keywords: tuple[str, ...]` in this PRD or the next? Recommendation: next; ship the fixed defaults first.
