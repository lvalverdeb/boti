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
| **Fail fast, fail safe** | Symlinked sandbox roots and traversal in `log_dir` are rejected at config time; path traversal is rejected at access time. Unpickling is off by default. Log files reject symlinks at open time. Warnings are loud, not silent. |

---

## 2. Architectural Overview

```
boti/
├── __init__.py          # Lazy facade: boti.Logger → boti.core.Logger (5 curated names)
├── __main__.py          # `python -m boti`
├── main.py              # CLI: `boti info`, `boti check`
├── py.typed             # PEP 561 marker
└── core/
    ├── __init__.py          # Explicit re-exports + __all__ (18 public symbols)
    ├── models.py            # Pydantic V2: LoggerConfig, ResourceConfig
    ├── settings.py          # Env-backed settings: SqlDatabaseSettings, FilesystemSettings, load_prefixed_model()
    ├── project.py           # ProjectService: root detection, .env loading
    ├── security.py          # Pure functions: is_secure_path, identifier/env-binding validation
    │
    │   # Lifecycle — three composable mixins and the classes built from them
    ├── lifecycle.py         # LifecycleCore: close()/aclose() barrier, context managers, GC finalizer
    ├── fsspec_mixin.py      # FsspecMixin: lazy, single-flight fs / fs_factory ownership
    ├── pickle_security.py   # PickleSecurityMixin: allow_pickle gate, trusted_unpickle_scope()
    ├── lifecycle_pickle.py  # PicklableLifecycleCoreMixin: strip/rebuild LifecycleCore state for bare composers
    ├── managed_resource.py  # ManagedResource = PickleSecurityMixin + FsspecMixin + LifecycleCore + logger
    ├── secure_io.py         # SecureResource: sandboxed file I/O (extends ManagedResource)
    ├── agent.py             # Agent / AgentConfig: LifecycleCore + logger, no fs or pickle concerns
    │
    │   # Filesystem
    ├── filesystem.py            # FilesystemConfig, FilesystemAdapter, create_filesystem(), endpoint allowlist
    ├── filesystem_options.py    # Per-backend fsspec/pyarrow option builders, S3 alias normalisation
    ├── filesystem_security.py   # SSRF host check: private/reserved IP and hostname rules
    │
    │   # Logging
    ├── logger.py            # Logger: default_logger() cache, bind(), _log dispatch
    ├── logger_filters.py    # PIISecretFilter
    ├── logger_handlers.py   # SafeRotatingFileHandler
    └── logger_runtime.py    # LoggerRuntime: shared QueueListener, destination registry
```

### 2.1 Module Responsibilities

| Module | Responsibility | Zero business logic with |
|--------|---------------|-------------------------|
| `models.py` | Data contracts only | I/O, filesystem access |
| `settings.py` | Env-to-model mapping | Application workflow |
| `security.py` | Pure validation functions | State, I/O |
| `project.py` | File-system-aware root detection | Data processing |
| `lifecycle.py` | Close barrier + context-manager protocol | Filesystems, pickling, logging setup |
| `fsspec_mixin.py` | Lazy filesystem ownership | Lifecycle state transitions |
| `pickle_security.py` | Pickle gating and state stripping | Filesystem, cleanup |
| `managed_resource.py` | Composes the three mixins; owns the logger | Domain logic |
| `secure_io.py` | Sandboxed file ops | Data transformation |
| `agent.py` | Lifecycle + logger for AI agents | Tool/LLM semantics |
| `filesystem*.py` | Storage abstraction + SSRF guard | Business rules |
| `logger*.py` | Structured logging pipeline | Application semantics |

### 2.2 Dependency Graph

```
logger_runtime.py         # No intra-package deps
logger_filters.py         # No intra-package deps
logger_handlers.py        # No intra-package deps
security.py               # No intra-package deps
models.py                 # No intra-package deps
lifecycle.py              # No intra-package deps
lifecycle_pickle.py       # No intra-package deps
fsspec_mixin.py           # No intra-package deps (expects LifecycleCore's interface at runtime)
filesystem_security.py    # No intra-package deps
settings.py ────────────→ security.py
project.py ─────────────→ security.py, settings.py
pickle_security.py ─────→ models.py
logger.py ──────────────→ models.py, project.py, logger_*.py
agent.py ───────────────→ lifecycle.py, logger.py
managed_resource.py ────→ lifecycle.py, fsspec_mixin.py, pickle_security.py, models.py, project.py, logger.py
secure_io.py ───────────→ managed_resource.py, models.py, project.py, security.py
filesystem_options.py ──→ filesystem.py (TYPE_CHECKING only — no runtime import)
filesystem.py ──────────→ filesystem_options.py, filesystem_security.py, settings.py
```

All arrows flow **down**: lower modules never import from higher ones at runtime. The one upward reference (`filesystem_options` → `filesystem`) is annotation-only under `TYPE_CHECKING`, so there are no runtime circular imports.

---

## 3. Core Abstractions

### 3.1 Data Contracts (Pydantic V2)

Every configuration surface is a Pydantic `BaseModel`:

- **`LoggerConfig`** — validates logger names against `^[A-Za-z0-9_.-]+$`, rejects path traversal in `log_dir`, enforces base-name constraints on `log_file`.
- **`ResourceConfig`** — carries `allow_pickle` (gated), `extra_allowed_paths` (sandbox overrides), optional pre-configured `logger`.
- **`FilesystemConfig`** — validates `fs_type` against an allowlist of 23 known backends, applies the SSRF guard (§4.1) to every field that can name a network endpoint, normalises aliases via `from_settings` factory.
- **`SqlDatabaseSettings`** — provides sane connection-pool defaults (5/10/30/1800).
- **`FilesystemSettings`** — `BaseModel` (not `BaseSettings`), designed to be loaded via `load_prefixed_model(prefix="ETL_")`.
- **`AgentConfig`** — the logging subset of `ResourceConfig` (`verbose`, `debug`, `logger`, `skip_logger`), `extra="forbid"`.

```python
# Pythonic: explicit model construction with field validation at the boundary.
config = FilesystemConfig(fs_type="s3", fs_path="my-bucket")                       # ok
config = FilesystemConfig(fs_type="s3", fs_path="my-bucket",
                          fs_endpoint="http://169.254.169.254")                    # ValidationError (SSRF)
```

### 3.2 Resource Lifecycle — `ManagedResource`

`ManagedResource` is not a monolith: it is the composition of three independent mixins, each usable on its own, plus logger wiring.

```
   ┌─────────────────────┐  ┌───────────────────┐  ┌────────────────────────┐
   │ PickleSecurityMixin │  │   FsspecMixin     │  │     LifecycleCore      │
   │ allow_pickle gate   │  │ fs / fs_factory   │  │ close()/aclose() barrier│
   │ __getstate__ strips │  │ single-flight init│  │ __enter__/__exit__ etc. │
   │ trusted_unpickle_   │  │ release on close  │  │ weakref.finalize leak   │
   │   scope()           │  │                   │  │   warning              │
   └──────────┬──────────┘  └─────────┬─────────┘  └───────────┬────────────┘
              └─────────────┬─────────┴────────────────────────┘
                   ┌────────┴─────────┐          ┌──────────────────┐
                   │  ManagedResource │          │      Agent       │  (LifecycleCore + logger only;
                   │  + config/logger │          │  + config/logger │   no fs, no pickle gate)
                   └────────┬─────────┘          └──────────────────┘
                            │ extends
                   ┌────────┴─────────┐
                   │  SecureResource  │
                   │  + allowed_paths │
                   │  + get_secure_*  │
                   └──────────────────┘
```

`LifecycleCore` is the piece most consumers actually want; `Agent` proves it stands alone. `PicklableLifecycleCoreMixin` exists for orchestrators that compose bare `LifecycleCore` but still need to pickle (it strips and rebuilds the locks without applying the security gate — the wrapped `ManagedResource`s apply their own).

**Key decisions (`LifecycleCore`):**

- **Cleanup runs *outside* the state lock.** `_state_lock` (an `RLock`) guards only the brief state transitions; `_cleanup()`/`_acleanup()` run unlocked so hooks can freely call `closed`, `require_fs()` or anything else that takes the lock. `close()` is a **barrier**: the first caller runs cleanup, concurrent callers block on `_closed_event` until it finishes, a reentrant call from inside the closer's own cleanup returns immediately. The reentrancy test uses thread identity *and* `asyncio` task identity — a sibling task on the same event loop shares the closer's OS thread but is not reentrant. A sync `close()` on the closer's own event-loop thread raises rather than deadlocking the loop.
- **Subclass hooks, not overrides.** Leaf classes implement `_cleanup()` / `_acleanup()`; mixins that own transient state implement `_release_transient_state()`, which runs under the lock atomically with the `_is_closed` transition. The default `_acleanup()` runs `_cleanup()` in a `to_thread` worker that adopts closer identity.
- **`@final` on `close`, `__exit__` and `__aexit__`** — subclasses cannot bypass the barrier or the "suppress cleanup errors only while another exception propagates" rule. `__enter__`/`__aenter__` are intentionally overridable so a session-style resource can yield a wrapped object; the exits still guarantee `close()`.
- **`weakref.finalize`** — if GC reclaims a live object without `close()`, the finalizer reports the leak through the resource's logger, or via `ResourceWarning` when no logger is configured. The finalizer is attached only to live objects, detached on close, and detached again if a subclass's `__init__` rejects its config after `LifecycleCore.__init__` has run (so a rejected `SecureResource` does not warn when collected).

**Key decisions (`PickleSecurityMixin`):**

- **`__getstate__`/`__setstate__`** — opt-in pickle serialisation guarded by `config.allow_pickle`. Strips transient state (locks, events, finalizer, logger) and then *progressively degrades* the remaining state (drop the config logger → drop an owned fs → drop the fs factory), probing picklability after each step, so a resource pickles with the least loss that still works. `__setstate__` rebuilds the locks, logger and finalizer and calls the `_restore_runtime_state()` hook.
- **`trusted_unpickle_scope`** — thread-local context manager that enables transient unpickling without mutating `os.environ`, so it neither leaks to sibling threads nor to child processes. The process-wide `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE` env var remains for worker processes; its presence is logged loudly once per process.

**Key decisions (`FsspecMixin`):**

- **Single-flight lazy init.** `fs_factory()` runs under a dedicated `_fs_init_lock`, *not* `_state_lock`, so a slow remote connect cannot stall `closed`/`close()` in other threads. The result is published only if the resource is still open — a filesystem is never attached to a closed resource.

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
- **Violation logging survives PII redaction** — the offending path is passed as a lazy `%`-argument, never interpolated into the message. `PIISecretFilter` blanks any *message* that mentions a sensitive keyword, and the most common probe target is literally `/etc/passwd`; interpolating it would turn the alert into `[REDACTED SENSITIVE DATA]`.

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
- **`LoggerRuntime` as global dispatch centre** — single `QueueListener` shared by all `Logger` instances. Destinations live in a registry **keyed by sink** `(logger_name, log_dir)`, not on the listener. `Logger._log` stamps every record with its sink (`extra["_boti_sink"]`), and the listener dispatches on that stamp: a dict lookup per record, not a scan of every handler in the process. Routing is therefore structural — a child logger's records are never looked up under the parent's sink, and two `Logger`s that share a stdlib name but write to different directories never receive each other's records. Records from a plain `logging.getLogger(name)` carry no stamp and route to `(name, "")`, which is where `add_destination()` registers when no sink is given. `stop_listener()`/`ensure_listener()` cycles keep every destination. `(logger_name, destination_id)` keys prevent attaching the same destination twice. `atexit` registration ensures flush on shutdown.
- **One stdlib logger per name, one level per name — but destinations per sink.** Every `Logger` (and `bind()` clone) with a given `logger_name` wraps the same `logging.getLogger(name)`, so level is a property of the name; the stdlib logger cannot tell those `Logger`s apart, which is exactly why records carry a sink stamp (above). `default_logger()` keys its registry on `(log_dir, logger_name, log_file)` — deliberately *not* level — and returns the one instance, applying the latest requested level to it. Two objects with independent levels for one name were never possible; the registry no longer pretends otherwise.
- **Explicit release, no hidden cap.** The `default_logger()` registry is unbounded by design, mirroring the stdlib's own per-name registry: one `Logger` per destination set. `Logger.close()` closes that `Logger`'s destinations (file handles included), drops the registry entry, and detaches the shared queue handler only once no other `Logger` with that name remains; a later `default_logger()` with the same arguments rebuilds a fresh one. Only applications that create many short-lived named loggers need it.

```python
# Pythonic: contextual logging with bound extra data.
log = Logger.default_logger().bind(request_id="abc-123")
log.info("processing order")        # extra silently attached
log.info("order complete", extra={"elapsed_ms": 42})  # merged
```

**PII Redaction (`PIISecretFilter`):**

- Attached to the `QueueHandler`, so it runs once per record *before* `QueueHandler.prepare()` bakes the rendered text into `msg`.
- **Format string (`record.msg`)** — replaced wholesale by `[REDACTED SENSITIVE DATA]` if it *mentions* a sensitive keyword anywhere, even with no value adjacent. Deliberately coarse: the secret may arrive as a later `%s` arg (`"token for %s is %s"`), which an adjacency test would miss. Over-redaction is the safe failure direction. Consequence for callers: put untrusted strings in `%`-args, not in the format string (see §3.3).
- **Args and `extra` fields** — deep-traversed (dict/list/set/tuple/frozenset, with cycle detection); values under sensitive keys are replaced, string values matching `key=value`/`key: value`/`key is value` are replaced.
- **Exception text** — `exc_info` is rendered, scrubbed with the assignment pattern (only the `key=value` spans are replaced, the traceback survives), pinned on `exc_text`, and `exc_info` cleared so no formatter re-renders the original.
- Mutates the record in place; returns `True` from `filter()` — never suppresses messages, only redacts content.

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

**SSRF Protection — `FilesystemConfig`:**

The guard (`filesystem_security._is_private_ip` + the `_reject_*` helpers in `filesystem.py`) is applied to every field that can name a network endpoint, not just `fs_endpoint`:

| Field | Rule |
|---|---|
| `fs_endpoint` | must be `http`/`https`; host checked |
| `fs_path` when `fs_type` is `http`/`https` | the path *is* the target URL; host checked |
| `fs_options["endpoint_url" / "endpoint_override" / "endpoint"]` and `fs_options["client_kwargs"]["endpoint_url"]` | with a scheme: same as `fs_endpoint`; bare `host[:port]` (pyarrow / MinIO style) is accepted and its host checked |

What "host checked" means:

- **Parser-differential rejection first.** A URL containing a backslash, whitespace, a control character, or a `user:pass@` section is refused before any host check. `urlparse` reads `http://127.0.0.1\@s3.amazonaws.com` as host `s3.amazonaws.com`; libcurl (pyarrow's S3 client) connects to `127.0.0.1`. Credentials belong in `fs_key`/`fs_secret`/`fs_token`.
- **Any non-global literal IP is blocked** — RFC-1918, loopback, link-local (incl. AWS IMDS), CGNAT `100.64/10`, IPv6 ULA (incl. `fd00:ec2::254`), unspecified, multicast, reserved. IPv4-mapped IPv6 (`::ffff:169.254.169.254`) is unwrapped first; legacy IPv4 spellings that `getaddrinfo` accepts (`127.1`, `0x7f.0.0.1`, `2130706433`) are parsed via `inet_aton`.
- **Reserved hostnames** (`localhost`, `*.localhost`, `metadata.google.internal`, …) are blocked with or without a trailing dot.
- **Operator allowlist** — `add_endpoint_to_allowlist("host[:port]")` or `from_env_prefix(..., trust_env_endpoint=True)` exempts a host. `ENDPOINT_ALLOWLIST` is a process-wide mutable set; populate it at startup, before the first config is built.

**Key decision: no DNS resolution in validation.** Checking whether a hostname resolves to a private IP is avoided intentionally — DNS lookups during config validation would add latency, fail unreliably in offline environments, and create a side channel. Blocking well-known hostnames statically covers the common attack vectors; DNS rebinding attacks are the operator's responsibility via network egress controls.

### 4.2 Retry Adapter — `FilesystemAdapter`

- Wraps `create_filesystem()` with exponential backoff: defaults `max_attempts=3`, `retry_base_delay=0.5` → sleeps 0.5 s then 1.0 s before the third attempt. Retries on `OSError`/`TimeoutError`/`ConnectionError` (note: `FileNotFoundError` and `PermissionError` are `OSError`s, so a bad local path also burns the backoff before failing).
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
5. Falls back with a `UserWarning` if no marker is found: to the first candidate (normally `cwd`), or — if every candidate is already at the filesystem root — to `Path.home()`. A `SecureResource` built without `project_root` in that state would sandbox to the home directory; set `project_root` explicitly in any deployment where markers may be absent.

`setup_environment()` loads `.env` files into `os.environ` after verifying `is_secure_path(target, [project_root])` — preventing accidental loading of env files outside the project. Existing `os.environ` keys win unless `override=True`, matching python-dotenv.

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
| `__all__` | Every public module | Explicit public API; `from boti import *` is safe. The two private helper modules (`filesystem_options.py`, `filesystem_security.py`) and the CLI (`main.py`, `__main__.py`) export nothing and carry none. |
| `@final` on public methods | `LifecycleCore.close`, `__exit__`, `__aexit__` | Prevents subclass overrides that could break cleanup guarantees. `__enter__`/`__aenter__` are intentionally overridable so session-style resources can yield a wrapped object (e.g. a client) while the exits still guarantee `close()`. |
| Mixin composition | `ManagedResource(PickleSecurityMixin, FsspecMixin, LifecycleCore)` | Each concern is independently usable and testable; `Agent` takes only `LifecycleCore`. |
| Template-method hooks | `_cleanup`, `_acleanup`, `_release_transient_state`, `_restore_runtime_state` | Subclasses extend behaviour without touching the (final) barrier logic. |
| `contextlib.contextmanager` | `trusted_unpickle_scope` | Decorator-based context manager instead of writing a class with `__enter__`/`__exit__`. |
| `weakref.finalize` | `LifecycleCore._attach_finalizer` | GC-integrated resource-leak detection without modifying `__del__`. |
| `copy.copy(self)` in `bind()` | `Logger.bind()` | Creates a shallow clone without calling `__init__`, preserving the full protocol. |
| `TypeAdapter` for coercion | `load_prefixed_model` | Single-dispatch type coercion instead of per-field if/else chains. |
| Pure module-level functions | `security.py` (6 funcs), `filesystem_security._is_private_ip` | No state, no class; testable in isolation. `ProjectService` uses `@staticmethod` for the same reason. |
| `ConfigDict(extra="forbid")` | `ResourceConfig`, `AgentConfig` | Prevents silent ignoring of misspelled configuration keys. |
| `field_validator` + `model_validator` | `FilesystemConfig` (3 field validators, 1 model validator for cross-field SSRF checks) | Declarative validation with clear error messages. |
| `property` for derived state | `LifecycleCore.closed`, `FilesystemConfig.storage_path`, `Logger.core` | Computed attributes that look like simple fields. |
| `threading.RLock` over `Lock` | `LifecycleCore._state_lock` | Reentrant so `closed`/`_assert_open()` can be called from inside locked transitions. |
| `@classmethod` factory | `Logger.default_logger()`, `FilesystemConfig.from_env_prefix()` | Named constructors reduce cognitive load vs. complex `__init__` signatures. |
| Dict dispatch over `if`/`elif` | `_FSSPEC_OPTIONS_BUILDERS` keyed by `fs_type` | Backend-specific option building is a table lookup, not a chain. |

---

## 8. Test Coverage

**File-level mapping (213 tests, all pass, ruff clean, mypy strict):**

| Test file | Tests | What it covers |
|-----------|------:|----------------|
| `test_filesystem.py` | 91 | `FilesystemConfig` validation, SSRF guard (private/reserved/mapped/legacy IPs, hostnames, parser-differential URLs, `fs_path` and `fs_options` routes, allowlist), `FilesystemAdapter` caching/retry, `_normalize_s3_fsspec_options`, pyarrow kwargs |
| `test_lifecycle.py` | 36 | `ManagedResource` init, sync/async close barrier, reentrancy across threads and tasks, context managers, pickling, finalizer warning |
| `test_security.py` | 30 | `SecureResource` sandbox enforcement, traversal rejection, `extra_allowed_paths` (incl. symlink rejection without a phantom GC warning), violation log survives PII filter, env-file loading, `security.py` validators |
| `test_logger.py` | 33 | Logger init, levels, `bind()`, extras merging and text rendering, stacklevel attribution (plain and adapter paths), PII redaction (msg/args/extra/`exc_info`, cycles), multi-handler `asctime` isolation, exact-name destination routing, shared-level registry, sink routing (same name / different dirs), `close()` release, raw-stdlib routing, listener restart, log-file hardening (permissions, symlink TOCTOU), stderr fallback |
| `test_agent.py` | 11 | `Agent` sync/async context, `aclose` fallback to sync cleanup, idempotent close, config rejection, logger injection and verbosity, GC finalizer, use-after-close |
| `test_lifecycle_pickle.py` | 5 | `PicklableLifecycleCoreMixin` round-trip: state preserved, locks/events rebuilt, closed state survives without re-running cleanup |
| `test_top_level_api.py` | 4 | `boti.*` facade resolves the 5 curated names; `import boti` does **not** load `boti.core` (checked in a fresh interpreter); `boti.core` attribute resolves lazily; unknown names raise `AttributeError` |
| `test_settings.py` | 2 | `load_prefixed_model`, `load_dotenv_values`, prefix validation, type coercion |
| `test_package_boundaries.py` | 1 | `boti.core` does not import `boti_data` |

---

## 9. Key Architectural Decisions

### 9.1 Why not `structlog`?

Boti's `Logger` wraps the stdlib `logging` module rather than replacing it with `structlog`. Rationale:

- **Zero adoption barrier** — consumers already know `logger.info("msg")`. No processor pipeline to configure.
- **Transitive compatibility** — libraries that use `logging.getLogger()` see the same queue, same PII redaction, same format.
- **Escape hatch** — `Logger._core` is a standard `logging.Logger`. If you need raw access, it's there.

### 9.2 Why lazy re-exports for `boti.__init__`?

The `boti` top-level package uses `__getattr__` to lazily import from `boti.core`. This is justified here because:

- **`boti.core` is the implementation** — it has 18 submodules. Eagerly importing them at `import boti` would load fsspec, pyarrow, pydantic, the logging runtime and path introspection — all before the user's `main()` starts. Measured: ~130 ms eager vs. ~2.5 ms lazy.
- **The lazy boundary is coarse, not per-symbol** — a single `import boti.core` inside `__getattr__` loads the whole `boti.core.__init__` (which uses explicit imports for compile-time resolvability). The laziness is in the top-level facade only, not in the core module.
- **`boti/__init__.py` must therefore never import `boti.core` at module level** — `test_import_boti_does_not_load_core` enforces this in a fresh interpreter. `__getattr__` resolves `boti.core` itself on first access and raises `AttributeError` for anything outside `__all__`, so the facade behaves like an ordinary module.

### 9.3 Why two `__init__` files?

- **`boti/__init__.py`** — public, curated facade. Lazy-loads only 5 symbols. This is what end-users `import boti` from. Type checkers see `__getattr__` and cannot resolve symbols — acceptable because this is the outermost shell with minimal usage (users mostly `from boti.core import ...`).
- **`boti/core/__init__.py`** — implementation facade. Explicit imports of all 18 symbols with `__all__`. Type checkers can resolve everything. This is what importers of `boti.core` see.

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
| Lifecycle | Double `close()`/`aclose()` is a silent no-op; `RuntimeError` on **use after close** (`_assert_open()`), on a sync `close()` that would deadlock an in-flight `aclose()` on the same loop, and on `require_fs()` with no filesystem configured | `ManagedResource is closed` |
| Cleanup | `close(suppress_errors=...)` defaults to `False`. The context managers pass `suppress_errors=exc_type is not None`: a cleanup error is swallowed (and logged) only while a user exception is already propagating, and raised on a clean body | Cleanup error doesn't mask the original exception, but is never hidden on its own |
| Logging | `warnings.warn` for recoverable failures | `log_dir not writable, falling back to stderr` |

No bare `except:` anywhere. The `except Exception` sites either re-raise (`close`, `aclose`, `SafeRotatingFileHandler._open`, `SecureResource.__init__`), or are explicitly best-effort and documented as such: the GC finalizer callback (must never raise during GC), the `pickle.dumps` picklability probe (logs at debug), and `_detach_finalizer`.
