# Software Design Document — Boti

> Runtime primitives for reliable Python data pipelines, secure file access,
> and notebook-to-production workflows.

---

## 1. Design Philosophy

Boti is built on four Pythonic pillars:

| Principle | Manifestation |
|-----------|--------------|
| **Explicit over implicit** | Every public API surface is declared in `__all__`. Configuration is always typed (Pydantic V2), never hidden in raw dicts. Dependency injection (via constructor parameters or factories) replaces global state. |
| **Composition over inheritance** | `ManagedResource` provides lifecycle via mix-in, not deep hierarchies. `FilesystemAdapter` composes fsspec + retry rather than subclassing. Behavior is added by wrapping, not extending. |
| **Protocols over ABCs** | Where possible, duck typing replaces abstract base classes. `Logger.bind()` returns a shallow clone via `copy.copy`, not a `LoggerAdapter` proxy — preserving the full `Logger` protocol. |
| **Fail fast, fail safe** | Path-traversal attacks are blocked at config time. Unpickling is off by default. Log files reject symlinks at open time. Warnings are loud, not silent. |

---

## 2. Architectural Overview

```
boti/
├── __init__.py          # Lazy facade: boti.Logger → boti.core.Logger
├── main.py              # CLI stub
├── py.typed             # PEP 561 marker
└── core/
    ├── __init__.py      # Explicit re-exports + __all__ (15 public symbols)
    ├── models.py        # Pydantic V2: LoggerConfig, ResourceConfig
    ├── settings.py      # Env-backed settings: SqlDatabaseSettings, FilesystemSettings, load_prefixed_model()
    ├── project.py       # ProjectService: root detection, .env loading
    ├── security.py      # Pure functions: is_secure_path, identifier validation
    ├── managed_resource.py  # ManagedResource: lifecycle base (sync/async context managers)
    ├── secure_io.py     # SecureResource: sandboxed file I/O (extends ManagedResource)
    ├── filesystem.py    # FilesystemConfig, FilesystemAdapter: fsspec + retry + SSRF guard
    └── logger*.py       # Logger, PIISecretFilter, SafeRotatingFileHandler, LoggerRuntime
```

### 2.1 Module Responsibilities

| Module | Responsibility | Zero business logic with |
|--------|---------------|-------------------------|
| `models.py` | Data contracts only | I/O, filesystem access |
| `settings.py` | Env-to-model mapping | Application workflow |
| `security.py` | Pure validation functions | State, I/O |
| `project.py` | File-system-aware root detection | Data processing |
| `managed_resource.py` | Lifecycle orchestration | Domain logic |
| `secure_io.py` | Sandboxed file ops | Data transformation |
| `filesystem.py` | Storage abstraction + SSRF guard | Business rules |
| `logger*.py` | Structured logging pipeline | Application semantics |

### 2.2 Dependency Graph

```
logger_runtime.py       # No intra-package deps
logger_filters.py       # No intra-package deps
logger_handlers.py      # No intra-package deps
security.py             # No intra-package deps
settings.py ──────────→ security.py
models.py               # No intra-package deps
project.py ───────────→ security.py, settings.py
logger.py ────────────→ models.py, project.py, logger_*.py
managed_resource.py ──→ models.py, project.py, logger.py
secure_io.py ─────────→ managed_resource.py, models.py, project.py, security.py
filesystem.py ────────→ settings.py
```

All arrows flow **down**: lower modules never import from higher ones. No circular imports.

---

## 3. Core Abstractions

### 3.1 Data Contracts (Pydantic V2)

Every configuration surface is a Pydantic `BaseModel`:

- **`LoggerConfig`** — validates logger names against `^[A-Za-z0-9_.-]+$`, rejects path traversal in `log_dir`, enforces base-name constraints on `log_file`.
- **`ResourceConfig`** — carries `allow_pickle` (gated), `extra_allowed_paths` (sandbox overrides), optional pre-configured `logger`.
- **`FilesystemConfig`** — validates `fs_type` against an allowlist of 20+ known backends, blocks private IPs for S3 endpoints (SSRF guard), normalises aliases via `from_settings` factory.
- **`SqlDatabaseSettings`** — provides sane connection-pool defaults (5/10/30/1800).
- **`FilesystemSettings`** — `BaseModel` (not `BaseSettings`), designed to be loaded via `load_prefixed_model(prefix="ETL_")`.

```python
# Pythonic: explicit model construction with field validation at the boundary.
config = FilesystemConfig(fs_type="s3", fs_path="my-bucket")
# ValidationError: fs_endpoint 'http://169.254.169.254' blocked (SSRF)
```

### 3.2 Resource Lifecycle — `ManagedResource`

The base class for any resource that needs setup/teardown:

```
         ┌──────────────────┐
         │  ManagedResource  │
         │  - config         │
         │  - fs             │
         │  - logger         │
         └──────┬───────────┘
                │ extends
         ┌──────┴───────────┐
         │  SecureResource   │
         │  + allowed_paths  │
         │  + get_secure_*   │
         └──────────────────┘
```

**Key decisions:**

- **Thread safety via `threading.RLock()`** — reentrant lock so `close()` can call `_cleanup()` holding the lock once, and `_cleanup()` can call other locked methods without deadlocking.
- **`@final` on context manager methods** — prevents subclasses from overriding `__enter__`/`__exit__`/`__aexit__`, which would bypass cleanup guarantees.
- **`weakref.finalize`** — emits a loud `ResourceWarning` if GC reclaims the object without explicit `close()`. Catches resource leaks at development time without blocking interpreter shutdown.
- **`__getstate__`/`__setstate__`** — opt-in pickle serialisation guarded by `config.allow_pickle`. Strips transient state (locks, finalizer, logger) before serialisation; rebuilds on deserialisation.
- **`trusted_unpickle_scope`** — thread-local context manager that enables transient unpickling without mutating `os.environ`, avoiding accidental leakage to child processes.

```python
# Pythonic: context manager ensures cleanup, even if an exception occurs.
with ManagedResource(config=ResourceConfig(allow_pickle=True)) as res:
    data = res.fs.ls("/some/path")
```

### 3.3 Sandboxed I/O — `SecureResource`

Extends `ManagedResource` with path-sandboxing:

- **`get_secure_path(path)`** — resolves the path and checks it lies within `allowed_paths` (project root + tempdir + configured extras). Raises `PermissionError` on violation.
- **`open_secure` / `write_text_secure` / `read_text_secure`** — convenience wrappers that call `get_secure_path` first.
- **Symlink rejection** — `extra_allowed_paths` entries that are symlinks are rejected at construction time, not at access time.

### 3.4 Logging — `Logger`

Wraps the stdlib `logging` module with structured output, PII redaction, and non-blocking queue dispatch:

```
User code              Logger               QueueHandler        QueueListener
    │                    │                      │                    │
    ├─ info("msg") ────→┤                      │                    │
    │                    ├─ _log() ────────────→┤                    │
    │                    │   (wraps in          │                    │
    │                    │    LoggerAdapter     │                    │
    │                    │    if extras)        │                    │
    │                    │                      ├─ put(record) ────→┤
    │                    │                      │                    ├─ handle(record)
    │                    │                      │                    │   ├─→ SafeRotatingFileHandler
    │                    │                      │                    │   └─→ StreamHandler(stdout)
```

**Key decisions:**

- **`_log` as single dispatch point** — all 5 log methods (`debug`/`info`/`warning`/`error`/`critical`) delegate to `_log`, which handles `extra` merging, `stacklevel` correction, and `LoggerAdapter` wrapping in one place. Eliminates duplication and ensures consistent behaviour.
- **`copy.copy(self)` in `bind()`** — preserves the full `Logger` protocol (`.set_level()`, `.bind()`, all log methods) instead of returning a `LoggerAdapter` with a narrower interface. Pythonic: prefer the object that supports all operations over one that requires the caller to know what's missing.
- **`self._extra` dict** — bound context stored directly on the instance rather than through the stdlib's `LoggerAdapter` proxy. Merged with per-call `extra` in `_log`. Zero overhead when no extras exist.
- **`LoggerRuntime` as global dispatch centre** — single `QueueListener` shared by all `Logger` instances. Destinations are added by key `(logger_name, "__console__")` to prevent duplicates. `atexit` registration ensures flush on shutdown.

```python
# Pythonic: contextual logging with bound extra data.
log = Logger.default_logger().bind(request_id="abc-123")
log.info("processing order")        # extra silently attached
log.info("order complete", extra={"elapsed_ms": 42})  # merged
```

**PII Redaction (`PIISecretFilter`):**

- Filters `LogRecord.msg` and all structured `extra` fields for sensitive patterns (`password=...`, `token=...`, etc.).
- Mutates `record.args` (positional) and `record.__dict__` (keyword/extra) in-place.
- Returns `True` from `filter()` — never suppresses messages, only redacts content.

**Safe File I/O (`SafeRotatingFileHandler`):**

- Overrides `_open()` to use `os.open(path, O_NOFOLLOW, 0o600)` — prevents symlink-planting attacks.
- Validates the opened fd is a regular file via `fstat`.
- `fchmod` ensures 0600 permissions even if `umask` is lax.

---

## 4. Filesystem Abstraction

### 4.1 Configuration Stack

```
Environment vars      load_prefixed_model()      FilesystemSettings
   (ETL_*)           ───────────────────────→    (BaseModel)
                                                     │
                                           FilesystemConfig.from_settings()
                                                     │
                                              create_filesystem()
                                                     │
                                           fsspec.AbstractFileSystem
                                                     │
                                           FilesystemAdapter (cached + retry)
```

**SSRF Protection — `FilesystemConfig.validate_fs_endpoint`:**

- Requires `http`/`https` scheme — rejects `file://`, `ftp://`, etc.
- Blocks private/reserved IPs (RFC-1918, loopback, link-local, AWS IMDS `169.254.169.254`) unless the endpoint is explicitly allowlisted via `add_endpoint_to_allowlist()`.
- Blocks reserved hostnames (`localhost`, `metadata.google.internal`, etc.) regardless of DNS resolution.

**Key decision: no DNS resolution in validation.** Checking whether a hostname resolves to a private IP is avoided intentionally — DNS lookups during config validation would add latency, fail unreliably in offline environments, and create a side channel. Blocking well-known hostnames statically covers the common attack vectors; DNS rebinding attacks are the operator's responsibility via network egress controls.

### 4.2 Retry Adapter — `FilesystemAdapter`

- Wraps `create_filesystem()` with exponential backoff (1s → 2s → 4s).
- Caches the filesystem instance behind a reentrant lock.
- `invalidate()` clears the cache and calls `fs.invalidate_cache()` if available.
- Supports both fsspec (`get_filesystem()`) and PyArrow (`get_pyarrow_filesystem()`) via normalised S3 kwargs.

**Caveat — credential expiry:** The cached `s3fs` instance may use STS temporary credentials (default 1-hour TTL). A long-running pipeline holding the cached client past expiry will receive 403 errors. Mitigations under consideration:

1. **TTL-based cache** — attach a timestamp to the cached instance; refresh automatically after a configurable interval.
2. **On-error invalidation** — if `get_filesystem()` encounters an auth failure, invalidate the cache and rebuild once.
3. **Explicit refresh** — the caller calls `adapter.invalidate()` periodically.

The current implementation does none of these — it assumes long-lived credentials (IAM user keys or instance profiles). For STS workflows, call `adapter.invalidate()` before the token expires.

```python
# Pythonic: lazy initialisation on first access, transparent caching.
adapter = FilesystemAdapter(config)
fs = adapter.get_filesystem()        # connects on first call
fs2 = adapter.get_filesystem()       # returns cached instance
```

---

## 5. Settings & Environment

### 5.1 `load_prefixed_model(model_cls, prefix)`

Loads any Pydantic `BaseModel` from environment variables with a given prefix:

```python
settings = load_prefixed_model(FilesystemSettings, "ETL_", env_file=".env.etl")
```

**How it works:**
1. Reads `.env` file (if provided) via `DotEnvSettingsSource` — preserves casing.
2. Merges with `os.environ` (later takes precedence).
3. For each field in `model_cls`, looks up `{PREFIX}{FIELD_NAME_UPPER}`.
4. Uses `TypeAdapter` for type coercion — tries Python coercion first, then JSON parsing for structured values (lists, dicts).
5. Calls `model_cls.model_validate(payload)` — runs Pydantic validators.

**Key decision — runtime prefix, not `BaseSettings`:** Explicit prefix parameter means the same model can be loaded from different env namespaces without redefining the model class. `FilesystemSettings` is a plain `BaseModel`, not `BaseSettings`, so it has no static env-binding — keeping it composable and testable.

### 5.2 `ProjectService`

Heuristic project-root detection:

1. Checks current `os.getcwd()`.
2. Checks `$PWD`.
3. Inspects the call stack (`inspect.stack()[1:8]`) for caller file paths — excluding synthetic frames (`<string>`, `ipykernel_*`).
4. For each candidate, walks up parents looking for markers (`pyproject.toml`, `.git`, `.env`, `.agent`, `src/boti`).
5. Falls back with a warning if no marker is found.

`setup_environment()` loads `.env` files into `os.environ` after verifying `is_secure_path(target, [project_root])` — preventing accidental loading of env files outside the project.

```python
# Pythonic: optional sophistication with sensible defaults.
root = ProjectService.detect_project_root()
# Falls back to cwd with a warning if no marker found.
```

---

## 6. Security Model

### 6.1 Path Sandbox

```python
def is_secure_path(target: str | Path, allowed_dirs: Iterable[str | Path]) -> bool:
    target_path = Path(target).resolve()
    for allowed in allowed_dirs:
        if target_path.is_relative_to(Path(allowed).resolve()):
            return True
    return False
```

Uses `Path.resolve()` (follows symlinks, normalises `..`) and `is_relative_to()` (Python 3.9+). No manual string concatenation or regex — exploits must defeat Python's own path resolution.

**Caveat — symlink targets escape the sandbox:** If a data directory is mounted via a symlink inside the project root (e.g. `~/project/data -> /mnt/ebs_volume`), `resolve()` evaluates to `/mnt/ebs_volume/file`, which is no longer relative to `~/project`. The operator must add the symlink target to `extra_allowed_paths`:

```python
SecureResource(config=ResourceConfig(
    extra_allowed_paths=["/mnt/ebs_volume"],
))
```

This is correct behaviour — allowing symlinks to implicitly widen the sandbox would be a security hole. The requirement to explicitly allowlist the target is intentional.

### 6.2 Unpickling Guard

- `ManagedResource.__getstate__` raises `TypeError` unless `config.allow_pickle=True`.
- `__setstate__` raises `pickle.UnpicklingError` unless `trusted_unpickle_scope()` is active or `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE=1`.
- Thread-local scope prevents the trusted-unpickle flag from leaking into child processes.

**Limitation:** `trusted_unpickle_scope` only guards against accidental deserialisation (a developer who forgot to disable `allow_pickle`). It does **not** make pickle safe against malicious payloads — `__reduce__` and `__reduce_ex__` are arbitrary code execution by design. The name "trusted" means "you trust the runtime environment AND the data source." If this pipeline ever handles user-submitted or external files, pickle must be replaced with a safe serialisation format (msgpack, JSON, or cloudpickle with a restricted reducer).

### 6.3 Log File Hardening

- `SafeRotatingFileHandler._open()` uses `os.open(path, O_NOFOLLOW, 0o600)` — atomic, follows no symlinks.
- `fstat` validates the opened fd is a regular file (not a FIFO, device, or socket).
- `fchmod` enforces 0600 even if `umask` allows broader permissions.

---

## 7. Pythonic Patterns Used

| Pattern | Where | Why |
|---------|-------|-----|
| `__all__` | Every module | Explicit public API; `from boti import *` is safe. |
| `@final` on public methods | `ManagedResource.__exit__`, `close` | Prevents subclass overrides that could break cleanup guarantees. `__enter__` is intentionally overridable so session-style resources can yield a wrapped object (e.g. a client) while `__exit__` still guarantees `close()`. |
| `contextlib.contextmanager` | `trusted_unpickle_scope` | Decorator-based context manager instead of writing a class with `__enter__`/`__exit__`. |
| `weakref.finalize` | `ManagedResource._attach_finalizer` | GC-integrated resource-leak detection without modifying `__del__`. |
| `copy.copy(self)` in `bind()` | `Logger.bind()` | Creates a shallow clone without calling `__init__`, preserving the full protocol. |
| `TypeAdapter` for coercion | `load_prefixed_model` | Single-dispatch type coercion instead of per-field if/else chains. |
| `@staticmethod` for pure functions | `security.py` (5 funcs), `ProjectService._search_ancestors` | No state = static method; testable in isolation. |
| `ConfigDict(extra="forbid")` | `ResourceConfig` | Prevents silent ignoring of misspelled configuration keys. |
| `field_validator` chain | `FilesystemConfig` (3 validators) | Declarative per-field validation with clear error messages. |
| `property` for derived state | `ManagedResource.closed`, `RunSummary.total_rows` | Computed attributes that look like simple fields. |
| `threading.RLock` over `Lock` | `ManagedResource._state_lock` | Reentrant lock so helper methods can be called from cleanup paths. |
| `@classmethod` factory | `Logger.default_logger()`, `FilesystemConfig.from_env_prefix()` | Named constructors reduce cognitive load vs. complex `__init__` signatures. |
| `StrEnum` for constants | `logger.py` uses `logging.DEBUG` etc. via `Logger.DEBUG = logging.DEBUG` | Avoids magic integers without tying to stdlib constants at call sites. |

---

## 8. Test Coverage

**File-level mapping (107 tests, all pass, ruff clean, mypy strict):**

| Test file | What it covers |
|-----------|----------------|
| `test_logger.py` | Logger init, log levels, `bind()`, extras merging, stacklevel, log file creation, stderr fallback |
| `test_lifecycle.py` | `ManagedResource` init, sync/async close, reentrancy, context managers, pickling, finalizer warning |
| `test_security.py` | `SecureResource` sandbox enforcement, path traversal rejection, extra_allowed_paths, env file loading |
| `test_filesystem.py` | `FilesystemConfig` validation, SSRF endpoint blocking, `FilesystemAdapter` caching, `_normalize_s3_fsspec_options` |
| `test_settings.py` | `load_prefixed_model`, `load_dotenv_values`, prefix validation, type coercion |
| `test_top_level_api.py` | `boti.*` (lazy facade) resolves all 5 public symbols |
| `test_package_boundaries.py` | `boti.core` does not transitively import unwanted submodules |

---

## 9. Key Architectural Decisions

### 9.1 Why not `structlog`?

Boti's `Logger` wraps the stdlib `logging` module rather than replacing it with `structlog`. Rationale:

- **Zero adoption barrier** — consumers already know `logger.info("msg")`. No processor pipeline to configure.
- **Transitive compatibility** — libraries that use `logging.getLogger()` see the same queue, same PII redaction, same format.
- **Escape hatch** — `Logger._core` is a standard `logging.Logger`. If you need raw access, it's there.

### 9.2 Why lazy re-exports for `boti.__init__`?

The `boti` top-level package uses `__getattr__` to lazily import from `boti.core`. This is justified here because:

- **`boti.core` is the implementation** — it has 9 submodules, 6 of which have intra-package imports. Eagerly importing all of them at `import boti` would load fsspec, pyarrow, logging infrastructure, and path introspection — all before the user's `main()` starts.
- **The lazy boundary is coarse, not per-symbol** — a single `import boti.core` inside `__getattr__` loads the whole `boti.core.__init__` (which uses explicit imports for compile-time resolvability). The laziness is in the top-level facade only, not in the core module.

### 9.3 Why two `__init__` files?

- **`boti/__init__.py`** — public, curated facade. Lazy-loads only 5 symbols. This is what end-users `import boti` from. Type checkers see `__getattr__` and cannot resolve symbols — acceptable because this is the outermost shell with minimal usage (users mostly `from boti.core import ...`).
- **`boti/core/__init__.py`** — implementation facade. Explicit imports of all 15 symbols with `__all__`. Type checkers can resolve everything. This is what importers of `boti.core` see.

The two-level split ensures `import boti` is fast (only `boti/__init__.py` + stdlib) while `from boti.core import Logger` is fully type-checkable.

### 9.4 Why `copy.copy` over `__init__` with a skip flag?

`Logger.bind()` could use `Logger.__init__(self.config, _skip_setup=True, _extra=...)` — but:

- `__init__` accepts `LoggerConfig`, not kwargs. Passing `_extra` as a non-field parameter would require a private kwarg, which is the same "not-pythonic" concern.
- `copy.copy(self)` is the standard Python idiom for "clone this object's data, don't re-run setup." It's used in `collections.namedtuple._replace`, `dataclasses.replace`, and `copyreg` recipes.
- The bind clone intentionally shares `_core` (same `logging.Logger` instance, same handlers, same level) — no need to duplicate infrastructure.

---

## 10. Error Handling Strategy

| Layer | Strategy | Example |
|-------|----------|---------|
| Configuration | Pydantic `ValidationError` with field-level messages | `fs_endpoint blocked (SSRF)` |
| Security | `PermissionError` / `ValueError` | `log_file must not be a symlink` |
| Lifecycle | `RuntimeError` on double-close | `ManagedResource is closed` |
| Cleanup | `suppress_errors=True` by default in context managers | Cleanup error doesn't mask the original exception |
| Logging | `warnings.warn` for recoverable failures | `log_dir not writable, falling back to stderr` |

No bare `except:` anywhere. No silently swallowed exceptions outside the logger finalizer (which must never raise during GC).
