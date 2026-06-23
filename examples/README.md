# Examples

Run examples from the repository root with:

```bash
# ── ManagedResource lifecycle ───────────────────────────────────────
python examples/simple_resource.py          # Lifecycle, close, GC warnings, restore state
python examples/async_resource.py            # Native async cleanup
python examples/managed_resource_pickle.py  # Pickle/unpickle with trusted_unpickle_scope

# ── Filesystem ──────────────────────────────────────────────────────
python examples/filesystem_resource.py       # fsspec-backed ManagedResource
python examples/filesystem_config.py         # FilesystemConfig (local, memory, S3, adapters)
python examples/filesystem_supported_backends.py  # Probe all 23 fsspec backends
python examples/filesystem_from_env.py       # FilesystemConfig.from_settings(), from_env_prefix()
python examples/filesystem_pyarrow.py        # PyArrow integration (Parquet on fsspec)

# ── Secure I/O ──────────────────────────────────────────────────────
python examples/secure_resource.py           # Sandboxed I/O, traversal rejection, extra paths

# ── Logging ─────────────────────────────────────────────────────────
python examples/logger.py                    # Log levels, PII redaction, default_logger()
python examples/logger_runtime.py            # Global QueueListener, custom destinations
python examples/profile_logger_load.py       # Logger performance under load
python examples/profile_pii_redaction.py     # PII filter throughput benchmarks

# ── Security & validation ───────────────────────────────────────────
python examples/security_extended.py         # Env bindings, identifiers, secure paths
python examples/profile_path_validation.py   # Path validation performance benchmarks

# ── Project / environment ───────────────────────────────────────────
python examples/project_environment.py       # Project root detection, dotenv loading
python examples/project_service_runtime.py   # Runtime env configuration

# ── Settings ────────────────────────────────────────────────────────
python examples/settings.py                  # SqlDatabaseSettings, load_prefixed_model()

# ── End-to-end ──────────────────────────────────────────────────────
python examples/end_to_end_pipeline.py       # Combined: ProjectService + SecureResource +
                                             #   Logger + FilesystemAdapter + security
```

## What each example covers

| Example | APIs demonstrated |
|---|---|
| `simple_resource.py` | `ManagedResource` — `__init__`, `_cleanup`, `close()`, `__enter__`/`__exit__`, idempotency, `suppress_errors`, `_restore_runtime_state()`, GC leak detection |
| `async_resource.py` | `ManagedResource` — `_acleanup`, `async with`, `aclose()` |
| `managed_resource_pickle.py` | `ManagedResource.__getstate__`/`__setstate__`, `allow_pickle`, `trusted_unpickle_scope()`, `_restore_runtime_state()`, `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE` |
| `filesystem_resource.py` | `ManagedResource` — `require_fs()`, `fs_factory`, `_owns_fs` |
| `filesystem_config.py` | `FilesystemConfig`, `create_filesystem()`, `FilesystemAdapter`, `storage_path`, `to_fsspec_options()`, S3/SSRF validation |
| `filesystem_supported_backends.py` | `_ALLOWED_FS_TYPES`, `FilesystemAdapter.get_filesystem()` for all 23 backends |
| `filesystem_from_env.py` | `FilesystemSettings`, `FilesystemConfig.from_settings()`, `from_env_prefix()`, env override precedence |
| `filesystem_pyarrow.py` | `FilesystemAdapter.get_pyarrow_filesystem()`, `pyarrow.parquet` read/write, `FSSpecHandler`, adapter caching |
| `secure_resource.py` | `SecureResource` — `write_text_secure()`, `read_text_secure()`, `get_secure_path()`, `open_secure()`, `allowed_paths`, traversal rejection, `extra_allowed_paths` |
| `logger.py` | `Logger` — `LoggerConfig`, all log levels, `set_level()`, PII redaction in `extra` dicts, `default_logger()` factory with LRU cache, exception logging |
| `logger_runtime.py` | `LoggerRuntime` — `ensure_listener()`, `add_destination()`, `stop_listener()`, `SafeRotatingFileHandler`, `PIISecretFilter`, multi-destination routing |
| `security_extended.py` | `validate_environment_bindings()`, `is_valid_env_var_name()`, `is_valid_identifier()`, `is_valid_dotted_identifier()`, `is_secure_path()` with edge cases |
| `settings.py` | `SqlDatabaseSettings`, `FilesystemSettings`, `load_prefixed_model()`, `load_dotenv_values()`, env override precedence |
| `project_environment.py` | `ProjectService.detect_project_root()`, `setup_environment()` |
| `project_service_runtime.py` | `ProjectService` — custom markers, runtime env files, env restore |
| `profile_logger_load.py` | Logger throughput (clean records, PII-heavy, concurrent, cache hits) |
| `profile_path_validation.py` | `is_secure_path()`, `is_valid_identifier()`, `is_valid_dotted_identifier()` performance |
| `profile_pii_redaction.py` | `PIISecretFilter.filter()` performance (shallow, deep nested, wide, args, clean) |
| `end_to_end_pipeline.py` | Combined: `ProjectService` + `SecureResource` + `Logger` + `FilesystemAdapter` + `is_secure_path` + `is_valid_identifier` + `validate_environment_bindings` + `ManagedResource` lifecycle |
