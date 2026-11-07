# PRD-005 — Configuration surface parity and CLI coverage

| | |
|---|---|
| Priority | P2 |
| Target | 1.2.0 |
| Owner | core maintainers |
| Modules | `src/boti/core/settings.py`, `src/boti/core/models.py`, `src/boti/main.py`, `examples/README.md`, `tests/` |
| Status | Done — shipped in 1.2.0 |

## 1. Problem

Small, independent gaps where the configuration surface says one thing and does another, or where a surface has no test at all. Each is minor; together they are the kind of thing a reviewer finds in ten minutes and that undermines the "reference implementation" claim.

1. **Env vars silently ignored.** `FilesystemConfig` has `fs_connect_timeout` and `fs_read_timeout`; `FilesystemSettings` (`settings.py:122-133`) does not. `FilesystemConfig.from_env_prefix("P_")` with `P_FS_CONNECT_TIMEOUT=3` / `P_FS_READ_TIMEOUT=7` yields `10.0` / `30.0` — probed on `ebed01f`. An operator tuning timeouts via env has no way to know it did nothing.
2. **Dead fields.** `LoggerConfig.verbose` and `.debug` (`models.py:31-32`) are read nowhere. `ResourceConfig` has its own `verbose`/`debug` that *are* used. Two fields that validate but do nothing are a documentation lie.
3. **CLI untested.** `main.py` is at 25 % coverage, `__main__.py` at 0 %. The stale hard-coded module list shipped for several releases because nothing exercised `boti check` end to end.
4. **Examples index incomplete.** `examples/README.md` omits `etl_async_pipeline.py`, `etl_concurrent_multiple.py`, `etl_concurrent_threads.py`, `etl_multiprocessing_pool.py`.
5. **Config knobs with no env path.** `ResourceConfig` cannot be built from a prefix the way `FilesystemConfig` can; `AgentConfig` neither. Not a bug, but an asymmetry users notice.

## 2. Goals

- G1. Every field on `FilesystemConfig` is settable through `from_env_prefix`, and a test enumerates the two models' fields to prove they cannot drift again.
- G2. `LoggerConfig` has no field that nothing reads.
- G3. The CLI has end-to-end tests for every subcommand and flag, through `main()` with `argv`, asserting exit codes and key output lines.
- G4. The examples index is complete and a test keeps it so.
- G5. A `from_env_prefix` classmethod exists on `ResourceConfig` with the same semantics as `FilesystemConfig`'s (stretch; see R9).

## 3. Non-goals

- A general "settings framework". `load_prefixed_model` already is one; this PRD only makes the models that exist consistent with it.
- CLI feature additions beyond what exists (`info`, `check`, `--version`).

## 4. Requirements

### Settings parity
- R1. Add `fs_connect_timeout: float | None = 10.0` and `fs_read_timeout: float | None = 30.0` to `FilesystemSettings` with the same descriptions as `FilesystemConfig`. Env values `""`, `none`, `null` (case-insensitive) map to `None` ("disable timeout") — document in the field description.
- R2. New test `test_filesystem_settings_covers_every_config_field`: `set(FilesystemSettings.model_fields) == set(FilesystemConfig.model_fields)`. The assertion message lists the difference. Any future field added to one without the other fails CI.
- R3. `from_env_prefix` test for the two timeouts (positive values, `none`, invalid string → `ValueError` naming the env var).

### LoggerConfig
- R4. Remove `verbose` and `debug` from `LoggerConfig`. `LoggerConfig` keeps `extra="forbid"`? — No: it currently allows extras implicitly (pydantic default `ignore`). Set `extra="forbid"` so a caller still passing `verbose=True` gets a clear error rather than silence. Release note under "behaviour changes".
- R5. Grep-verify no usage in `src/`, `examples/`, READMEs, SDD; update SDD §3.1 field list.

### CLI tests
- R6. New `tests/test_cli.py` (rename/absorb `tests/test_main.py`): parametrised over `["info"]`, `["check"]`, `["--version"]`, `[]` (usage error, exit 2), `["bogus"]` (exit 2). Calls `main_module.main()` with `monkeypatch.setattr(sys, "argv", …)`, catches `SystemExit`, asserts code and that stdout contains the version string / `All checks passed.` / every public-API symbol for `info`.
- R7. `python -m boti --version` is exercised once via `subprocess.run([sys.executable, "-m", "boti", "--version"])` to cover `__main__.py`.
- R8. `boti info` additionally prints `Open strategy:` (PRD-002 R8) and the installed optional extras (`s3fs: installed/missing`), and `boti check` reports a missing extra as `[info]`, not an error. Tests cover both.

### ResourceConfig from env (stretch)
- R9. `ResourceConfig.from_env_prefix(prefix, *, env_file=None, **overrides)` using `load_prefixed_model` on a new `ResourceSettings` model mirroring the plain-data fields (`verbose`, `debug`, `skip_logger`, `allow_pickle`, `project_root`, `extra_allowed_paths` as JSON list). `logger` is excluded (not env-representable). Same field-parity test as R2.

### Examples index
- R10. `examples/README.md` lists the four `etl_*` examples with one-line descriptions matching their module docstrings.
- R11. New test `tests/test_examples_index.py`: every `examples/*.py` basename appears in `examples/README.md` and vice-versa (the `comm -3` check from the review, as a test).

## 5. Acceptance criteria

- A1. `FilesystemConfig.from_env_prefix("P_")` with `P_FS_CONNECT_TIMEOUT=3` returns `fs_connect_timeout == 3.0`; with `P_FS_READ_TIMEOUT=none` returns `None`.
- A2. R2 and R11 tests exist and pass; a deliberately removed field / unlisted example (shown in the PR) makes them fail.
- A3. `main.py` ≥ 85 % and `__main__.py` 100 % coverage; overall coverage does not drop.
- A4. `LoggerConfig(verbose=True)` raises a pydantic `ValidationError`.
- A5. SDD §3.1, §5.1, §8 and `examples/README.md` updated; READMEs ×3 updated if they mention `LoggerConfig` fields (grep first).

## 6. Design notes

- R1's `none`/`null` parsing belongs in a `field_validator(mode="before")` on `FilesystemSettings`, not in `load_prefixed_model` (which is generic).
- R6 should not shell out except for R7; in-process `main()` keeps the tests fast and coverage-attributable.

## 7. Risks

| Risk | Mitigation |
|---|---|
| R4 breaks a caller passing `verbose=` to `LoggerConfig` | It never did anything; the error is the fix. Minor bump + release note. |
| R9 scope creep | Marked stretch; may be split out if 1.2.0 is otherwise ready. |
