# Changelog

All notable changes to boti are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[Semantic Versioning](https://semver.org/). Behaviour changes are in **bold**
under *Changed*.

## [Unreleased]

## [1.2.0] - 2026-11-07

### Added
- `SecureResource` opens files through `openat2(RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS)` on Linux ≥ 5.6, enforced by the kernel in one syscall; the `O_NOFOLLOW` component walk remains the fallback on macOS, BSD and older kernels. The strategy in effect is the read-only `SecureResource.open_strategy` and is printed by `boti info`. CI asserts `openat2` on Linux and `walk` on macOS.
- Property-based tests (Hypothesis, dev dependency only) for the SSRF host rules, the secret filter, the env-binding/identifier/logger-name validators and sandbox path containment and anchoring. Profiles `ci` (deterministic) and `dev`; `property` marker.
- `boti info` reports the installed optional extras and the sandbox open strategy; `boti check` reports a missing extra as `[info]`, never an error. `main(argv=None)` for in-process testing.
- `FilesystemSettings.fs_connect_timeout` / `fs_read_timeout`, so `{PREFIX}FS_CONNECT_TIMEOUT` / `FS_READ_TIMEOUT` reach `FilesystemConfig` (`none`/`null` disable a timeout). A test pins field parity between the two models.
- `SECURITY.md`, `docs/THREAT_MODEL.md`, `RELEASING.md` and this changelog. The changelog and security policy ship in the sdist.
- Supply chain: `publish.yml` verifies the tag against `pyproject.toml`, builds with `SOURCE_DATE_EPOCH`, publishes with PEP 740 attestations and verifies them on PyPI; `audit.yml` runs `pip-audit` on the locked runtime and dev requirements on every PR and weekly; Dependabot keeps action pins current; CI runs the test job on macOS as well as Linux.

### Changed
- **Relative paths passed to `SecureResource` (`get_secure_path`, the `open_secure` family, `extra_allowed_paths`) are anchored to `project_root`, never to the process working directory; `~` is not expanded.** Code that relied on cwd anchoring must pass absolute paths or set `project_root` accordingly.
- **`LoggerConfig` no longer accepts `verbose` or `debug`** (they were read nowhere) and rejects unknown fields; use `log_level`.
- **`make publish` is gone.** Releases go through `publish.yml`; `make publish-breakglass` is the token fallback and refuses without `BOTI_BREAKGLASS=1`.
- `load_prefixed_model` defers coercion to the model's own validators when a value is neither a plain scalar nor JSON, and names the offending variable(s) on rejection. `FilesystemConfig.from_settings` passes explicitly set `None` values through.
- A `..` component reaching `open_secure` after validation (only possible if resolution was bypassed) is refused by every strategy.

### Fixed
- `PIISecretFilter`: a keyword that is a suffix of a longer name in text (`db_password=…`, `accessToken=…`, `X-Api-Key-2: …`) was not redacted; JSON embedded in JSON (`\"password\": \"…\"`) was not redacted; `credential`/`session`/`cookie` at an assignment in text were not redacted; the same plain object appearing twice in one record leaked on its second occurrence; re-filtering `{"password": [REDACTED]}` produced `[REDACTED]]`. All found by the new properties.
- `LoggerConfig` accepted a `log_file` containing a NUL byte and failed at first use.
- `examples/README.md` omitted the four `etl_*` examples; a test now keeps the index complete.

### Security
- Secret-filter leaks listed under *Fixed*.
- Dependency floors raised past two advisories the new audit found on its first run: `fsspec>=2026.6.0` (PYSEC-2026-4217 — unsandboxed Jinja2 rendering in `ReferenceFileSystem`, remote code execution from an attacker-controlled Kerchunk document; boti never instantiates that backend, but an installer could resolve the vulnerable version) and `pydantic-settings>=2.14.2` (CVE-2026-58203 — symlink escape and size-cap bypass in `NestedSecretsSettingsSource`, which boti does not use). Lock moved to fsspec 2026.9.0 and pydantic-settings 2.16.2.

## [1.1.1] - 2026-11-07

### Fixed
- **`PIISecretFilter` leaked secrets in common shapes.** Keys are matched by keyword containment after normalising case and separators (`db_password`, `AWS_SECRET_ACCESS_KEY`, `apiToken`, `X-Api-Key`, `session`, `cookie`, `credential`); quoted keys (`{"password":"…"}`, YAML) are redacted; the value of a `key=value` / `key: value` / `key is value` assignment is replaced while the key survives as context; `Bearer …` values are taken whole; URL credentials become `scheme://[REDACTED]@host`; `bytes`/`bytearray` are scanned; dataclasses and pydantic models are rendered as redacted dicts; other objects with a `__dict__` are inspected; uninspectable objects are replaced, never passed through. `SecretStr`-like values are left alone.
- **Nothing is emitted after `Logger.close()`**, including through `logging.lastResort`, which previously printed raw, unredacted text to stderr. `bind()` clones are silenced with their parent.

### Changed
- An unquoted secret value runs to the next separator (`,` `;` `}` `]` or newline), so the remainder of a free-text line after `password=` is over-redacted rather than under-redacted.

### Security
- The two filter fixes above.

## [1.1.0] - 2026-11-07

### Changed
- **`SecureResource()` with no `project_root` and no project marker raises `ValueError`** instead of sandboxing to the working directory or `$HOME`.
- **`FilesystemConfig` rejects more SSRF vectors in `fs_options`:** a wrapper backend's `target_protocol` must be an allowlisted backend and its `target_options` are checked recursively; bare `host`/`port` and `client_kwargs.base_url` are checked against the private-IP rules.
- **`FilesystemConfig` validates on assignment.**
- **`s3fs` is an optional extra** (`pip install "boti[s3]"`); `ipykernel` is no longer a dependency (41 packages instead of 67).
- `src/boti` is no longer a project-root marker.

### Fixed
- Two `Logger`s sharing a name but writing to different directories no longer receive each other's records (routing by sink).
- A closed file handler stays closed; a record in flight during `Logger.close()` no longer re-creates the log file.
- `SecureResource.open_secure` opens component by component with `O_NOFOLLOW` from the sandbox root, so a symlink planted after validation is refused rather than followed.
- `FilesystemAdapter` no longer retries permanent errors.
- `ProjectService` root detection no longer reads source context for every stack frame and ignores boti's own frames.
- `boti check` discovers all `boti.core` modules.

### Security
- Check-then-open race in `SecureResource` closed; wrapper-chain, bare-host and `base_url` SSRF vectors closed.
- GitHub Actions pinned by commit SHA; `publish.yml` runs the same gate as CI.

## [1.0.11] - 2026-11-06

### Added
- `Logger.close()` — explicit release of a logger's destinations and registry entry.
- `LoggerRuntime.remove_destinations(logger_name)`.

### Changed
- `Logger.default_logger()` keys its registry on `(log_dir, logger_name, log_file)` and applies the latest requested level to the one shared instance; the 256-entry cap is gone.
- `LoggerRuntime` destinations live in a name-keyed registry on the runtime.

### Fixed
- `stop_listener()` followed by `ensure_listener()` no longer strands loggers created before the stop.
- `Logger.set_level()` keeps `Logger.log_level` in sync.

## [1.0.10] - 2026-11-06

### Changed
- **`fs_endpoint` and `fs_options` endpoints containing userinfo, backslashes or control characters are rejected**; pass credentials via `fs_key` / `fs_secret` / `fs_token`.
- **http(s) `fs_path` values and `fs_options` endpoints pointing at private or reserved hosts are rejected** unless allowlisted.

### Fixed
- Console log lines no longer end with a spurious `| asctime='…'` tail.
- Dotted logger names no longer print child records twice or into the parent's file.
- `import boti` is lazy again (~2.5 ms instead of ~130 ms).
- `LifecycleCore.__aexit__` is `@final`.
- A `SecureResource` whose config was rejected no longer warns about being garbage-collected unclosed.

### Security
- SSRF guard closed against CGNAT, IPv6 ULA, `0.0.0.0`, multicast, IPv4-mapped IPv6, legacy IPv4 spellings (`127.1`, `0x7f.0.0.1`, `2130706433`), trailing dots and `*.localhost`; applied to every endpoint-bearing field; parser-differential URLs refused.
- `PIISecretFilter` scrubs exception text.

[Unreleased]: https://github.com/lvalverdeb/boti/compare/v1.2.0...HEAD
[1.2.0]: https://github.com/lvalverdeb/boti/compare/v1.1.1...v1.2.0
[1.1.1]: https://github.com/lvalverdeb/boti/compare/v1.1.0...v1.1.1
[1.1.0]: https://github.com/lvalverdeb/boti/compare/v1.0.11...v1.1.0
[1.0.11]: https://github.com/lvalverdeb/boti/compare/v1.0.10...v1.0.11
[1.0.10]: https://github.com/lvalverdeb/boti/releases/tag/v1.0.10
