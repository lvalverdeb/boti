# boti threat model

Revision: 2026-11-07 (built against 1.2.0). Changes go through pull-request
review; each release tag references the revision it was built against.

This document says, for every security component, which attacker it assumes,
what it defends, what it deliberately does **not** defend, and which tests pin
each claim. It is written for people deciding whether to depend on boti. The
mechanisms themselves are described in [`SDD.md`](../SDD.md) §3 and §6; this
document does not repeat them.

Test references have the form `tests/<file>::<test>` (class-qualified where
needed) and are checked by `tests/test_threat_model_refs.py`: a reference to
a test that does not exist fails CI. Properties are the Hypothesis tests in
`tests/core/*_props.py`.

## Common assumptions

- boti runs as one OS user. A process or user with the same uid can read the
  same files, replace the same binaries and attach the same debuggers; no
  component defends against that.
- The Python interpreter, the standard library, the kernel and the four
  runtime dependencies are trusted. Supply-chain integrity of boti itself is
  addressed by the release process ([`RELEASING.md`](../RELEASING.md)), not
  by code.
- Configuration (`ResourceConfig`, `FilesystemConfig`, `LoggerConfig`) may be
  built from untrusted input — environment variables, dotenv files, request
  parameters. Every component treats its configuration as hostile.

## 1. Path sandbox (`SecureResource`)

| | |
|---|---|
| Assets | Files outside the configured roots (`project_root`, the system temp directory, `extra_allowed_paths`). |
| Trust boundary | The path argument of `get_secure_path`, `open_secure`, `read_text_secure`, `write_text_secure`; the `extra_allowed_paths` configuration. |
| Attacker | Supplies any path string, including relative paths, `..`, NUL bytes and symlinks; may have write access *inside* a root and can race the resource (create or replace a symlink between validation and open). |

**Defended**

- Paths that resolve outside every root, including through `..`, symlinks
  inside a root pointing outside, and sibling directories whose name shares a
  prefix with a root (`/root` vs `/root_evil`) —
  `tests/core/test_security.py::test_secure_resource_sandboxing`,
  `tests/core/test_security.py::TestSandboxBypassTechniques::test_symlink_inside_root_escaping_to_outside_target_is_blocked`,
  `tests/core/test_security.py::TestSandboxBypassTechniques::test_sibling_directory_prefix_confusion_is_blocked`,
  `tests/core/test_security.py::TestSandboxBypassTechniques::test_relative_traversal_escaping_root_via_dotdot_is_blocked`,
  property `tests/core/test_security_props.py::test_containment_is_decided_by_path_components_not_string_prefix`.
- Unresolvable paths (NUL byte, filesystem error) are denied, not raised as
  `ValueError`/`OSError` —
  `tests/core/test_security.py::TestGetSecurePathFailsClosed::test_null_byte_raises_permission_error_not_value_error`,
  `tests/core/test_security.py::TestGetSecurePathFailsClosed::test_null_byte_blocked_through_open_secure`.
- Time-of-check/time-of-use: a symlink planted anywhere on a validated path
  between validation and open is refused rather than followed, by
  `openat2(RESOLVE_BENEATH|RESOLVE_NO_SYMLINKS|RESOLVE_NO_MAGICLINKS)` on
  Linux ≥ 5.6 and by an `O_NOFOLLOW` component walk elsewhere on POSIX; a
  `..` component at open time (only possible if resolution was bypassed) is
  refused by both —
  `tests/core/test_security.py::TestOpenSecure::test_symlink_planted_after_validation_is_refused`,
  `tests/core/test_security.py::TestOpenSecure::test_symlink_as_final_component_is_refused_on_write`,
  `tests/core/test_security.py::TestOpenSecure::test_dotdot_escape_past_the_root_is_refused_at_open`,
  `tests/core/test_security.py::TestOpenSecure::test_proc_magic_link_under_root_is_refused`,
  `tests/core/test_security.py::test_selected_open_strategy_matches_the_platform`.
- Relative paths anchor to `project_root`, never to the working directory, and
  `~` is not expanded —
  `tests/core/test_security.py::TestPathAnchoring::test_relative_path_reads_the_root_file_not_the_cwd_file`,
  `tests/core/test_security.py::TestPathAnchoring::test_tilde_is_not_expanded`,
  property `tests/core/test_security_props.py::test_relative_paths_anchor_to_the_root_regardless_of_cwd`.
- The root is never guessed: no explicit root and no project marker is an
  error at construction —
  `tests/core/test_security.py::test_secure_resource_refuses_to_sandbox_a_guessed_root`.
- `extra_allowed_paths` entries that are symlinks are rejected at
  construction — `tests/core/test_security.py::test_secure_resource_extra_allowed`.

**Explicitly not defended**

- *Ancestors of a root.* Like any `chroot`-style boundary, the directories
  above `project_root` are trusted; an attacker who can replace `project_root`
  itself with a symlink owns the sandbox.
- *The `open` strategy* (platforms without `dir_fd`, i.e. Windows): only the
  check-then-open is available, so the TOCTOU window is not closed.
  `SecureResource.open_strategy` reports which strategy is in effect.
- *`get_secure_path` used as a gate for a plain `open()`.* It is a check; only
  the `open_secure` family defends the open.
- *The system temp directory* is always an allowed root, by design; code that
  must not touch it needs a different tool.
- *Remote `fsspec` filesystems* reached through `ManagedResource.fs` are not
  path-sandboxed at all; they are virtual or remote and outside this
  component.

**Residual risk.** A process sharing the uid; an attacker with write access
to an ancestor of a root.

## 2. SSRF guard (`FilesystemConfig` / `filesystem_security`)

| | |
|---|---|
| Assets | Internal network services: cloud metadata endpoints, loopback and RFC-1918 hosts, anything a storage client could be pointed at. |
| Trust boundary | Every `FilesystemConfig` field and `fs_options` key that names a network target: `fs_endpoint`, an http(s) `fs_path`, `endpoint_url`/`endpoint_override`/`endpoint`, `client_kwargs.endpoint_url`/`base_url`, `host`/`port`, and any of these under a wrapper backend's `target_options`. |
| Attacker | Controls configuration values and can spell an address any way a client library would parse. |

**Defended**

- Every literal address that is not globally routable, in every spelling a
  client accepts: dotted, decimal, hex octets, `inet_aton` short forms,
  trailing dot, IPv4-mapped IPv6, bracketed IPv6, with or without scheme or
  port; reserved names (`localhost`, `*.localhost`, cloud metadata names) —
  `tests/core/test_filesystem.py::test_filesystem_config_rejects_private_ip_endpoints`,
  `tests/core/test_filesystem.py::test_is_private_ip_blocks_bypass_forms`,
  `tests/core/test_filesystem.py::test_filesystem_config_rejects_bypass_endpoints`,
  properties `tests/core/test_filesystem_security_props.py::test_non_global_addresses_are_rejected_in_every_spelling` and
  `tests/core/test_filesystem_security_props.py::test_global_addresses_are_accepted_in_every_spelling`.
- Parser-differential URLs (backslash, whitespace, control characters) and
  userinfo sections are refused before any host is extracted, so the host
  boti checks is the host every client connects to —
  `tests/core/test_filesystem.py::test_filesystem_config_rejects_parser_differential_endpoints`,
  property `tests/core/test_filesystem_security_props.py::test_parser_differential_inputs_never_reach_the_host_check`.
- Every endpoint-bearing field and option, including wrapper chains to a
  bounded depth and bare `host`/`port` pairs —
  `tests/core/test_filesystem.py::test_filesystem_config_rejects_private_endpoint_in_fs_options`,
  `tests/core/test_filesystem.py::test_wrapper_backend_chain_is_checked_at_every_level`,
  `tests/core/test_filesystem.py::test_wrapper_backend_chain_depth_is_bounded`,
  `tests/core/test_filesystem.py::test_bare_host_option_is_ssrf_checked`,
  `tests/core/test_filesystem.py::test_http_client_kwargs_base_url_is_ssrf_checked`,
  property `tests/core/test_filesystem_security_props.py::test_wrapper_chains_are_rejected_at_any_depth_without_recursion`.
- Validation on assignment: mutating a field after construction re-runs the
  guard — `tests/core/test_filesystem.py::test_filesystem_config_validates_on_assignment`.
- Backend allowlist: only listed `fsspec` backends can be instantiated from
  configuration — `tests/core/test_filesystem.py::test_filesystem_config_rejects_unlisted_backend`,
  `tests/core/test_filesystem.py::test_wrapper_backend_target_protocol_must_be_allowlisted`.
- The operator allowlist is exact per host and per `host:port` —
  `tests/core/test_filesystem.py::test_filesystem_config_accepts_allowlisted_private_endpoint`,
  property `tests/core/test_filesystem_security_props.py::test_allowlist_is_exact`.

**Explicitly not defended**

- *DNS.* Hostnames are not resolved (resolution would be slow and
  non-deterministic), so a public name that resolves to a private address
  (`nip.io`, DNS rebinding, a compromised resolver) passes. Egress control at
  the network layer is the operator's responsibility.
- *Redirects.* A public endpoint that answers with a redirect to a private
  address is followed by the client library, not inspected by boti.
- *`ENDPOINT_ALLOWLIST` at runtime.* An attacker who can call
  `add_endpoint_to_allowlist()` or mutate the set has, by definition, code
  execution; the allowlist is a startup-time operator control.
- *Backends constructed directly* with `fsspec.filesystem(...)` bypass the
  guard entirely; it applies to `FilesystemConfig`.

**Residual risk.** Hostname-based SSRF where egress is unrestricted.

## 3. Secret filter (`PIISecretFilter`)

| | |
|---|---|
| Assets | Credentials that would otherwise be written to log files or the console. |
| Trust boundary | Everything a caller logs: format string, `%`-args, `extra` fields, bound context, exception text. |
| Attacker | Not an attacker in the usual sense: the threat is a developer logging a structure that happens to contain a secret, and a later reader of the log. |

**Defended**

- A value under a key whose normalised name contains a credential keyword, in
  dicts, lists, tuples, sets, dataclasses, pydantic models, plain objects,
  JSON text and bytes, nested to any depth with cycles —
  `tests/core/test_logger_filters.py::test_corpus_secret_never_survives_as_arg`,
  `tests/core/test_logger_filters.py::test_corpus_secret_never_survives_as_extra`,
  `tests/core/test_logger_filters.py::test_corpus_through_real_logger_file_and_console`,
  property `tests/core/test_logger_filters_props.py::test_a_secret_under_a_sensitive_key_never_survives`.
- `key=value`, `key: value`, `"key": "value"` (including backslash-escaped
  quotes), `key is value` and `Bearer …` values in text; the keyword may be a
  suffix of a longer name (`db_password`, `accessToken`) —
  `tests/core/test_logger_filters.py::test_assignment_value_runs_to_a_separator_not_whitespace`,
  `tests/core/test_logger_filters.py::test_bearer_value_is_redacted_whole`,
  `tests/core/test_logger_filters.py::test_compound_keyword_assignment_in_message_is_redacted`.
- URL credentials (`scheme://user:secret@host`) in messages, args and values,
  keeping the host — property
  `tests/core/test_logger_filters_props.py::test_url_credentials_never_survive_but_the_host_does`.
- A keyword in the format string blanks the whole message, because the
  secret may arrive as a later `%s` arg —
  `tests/core/test_logger_filters.py::test_keyword_in_message_redacts_whole_message`.
- Exception text (`exc_info`, pre-formatted `exc_text`) —
  `tests/core/test_logger_filters.py::test_secrets_in_exception_tracebacks_are_redacted`,
  `tests/core/test_logger_filters.py::test_preformatted_exc_text_is_scrubbed`.
- The same object appearing twice in one record; objects that explode on
  inspection or exceed the field cap are replaced, never passed through —
  `tests/core/test_logger_filters.py::test_same_plain_object_twice_in_one_record_is_redacted_both_times`,
  `tests/core/test_logger_filters.py::test_object_that_explodes_on_inspection_is_replaced`,
  `tests/core/test_logger_filters.py::test_object_with_too_many_fields_is_replaced_not_passed_through`.
- After `Logger.close()` nothing is emitted, not even through the stdlib's
  `lastResort` handler —
  `tests/core/test_logger_filters.py::test_nothing_is_emitted_after_close`.
- Idempotence and a keyword-free negative corpus guard against the filter
  degrading into "redact everything" —
  `tests/core/test_logger_filters.py::test_corpus_benign_value_survives`,
  property `tests/core/test_logger_filters_props.py::test_a_value_under_a_keyword_free_key_survives`,
  property `tests/core/test_logger_filters_props.py::test_filtering_is_idempotent`.

**Explicitly not defended**

- *Secrets with no keyword context.* A bare access-key id or a JWT logged
  under `value=` or as a positional arg is not recognised; there is no
  entropy-based detection, by design (false positives would blank ordinary
  logs).
- *Objects that hide state* behind `__slots__`, properties or a custom
  `__repr__` with no `__dict__` or `model_dump()`.
- *Keys under a text wrapper with a space before a suffix* (`"password v2": …`
  inside a JSON string): inside text the key is read as `keyword[\w-]*`.
- *Handlers that bypass the queue.* The filter is attached to boti's
  `QueueHandler`; a handler added directly to the stdlib logger by other
  code does not pass through it.
- *Over-redaction.* The filter errs toward removing too much ("does not
  require a password" is blanked). That is a usability cost, not a security
  gap.

**Residual risk.** Context-free secrets; third-party handlers.

## 4. Pickle gate (`PickleSecurityMixin`)

| | |
|---|---|
| Assets | Arbitrary code execution via `pickle.loads`. |
| Trust boundary | `ManagedResource.__setstate__`, reached by `pickle.loads` on bytes from a queue, a file, or another process. |
| Attacker | Can place bytes where a consumer will unpickle them. |

**Defended**

- Unpickling a `ManagedResource` is refused unless the producer opted in with
  `allow_pickle=True` *and* the consumer is inside `trusted_unpickle_scope()`
  (thread-local, does not leak to sibling threads or child processes) or has
  set `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE` for the process —
  `tests/core/test_lifecycle.py::test_managed_resource_pickle_requires_explicit_opt_in`,
  `tests/core/test_lifecycle.py::test_managed_resource_unpickle_requires_trusted_scope`,
  `tests/core/test_lifecycle.py::test_cross_thread_trusted_unpickle_scope_isolation`.
- The process-wide opt-in is logged loudly once per process —
  `tests/core/test_lifecycle.py::test_trusted_unpickle_active_emits_startup_warning`.
- Runtime-only state (locks, logger, filesystem handles, factories) is never
  serialised —
  `tests/core/test_lifecycle.py::test_managed_resource_pickle_strips_runtime_only_logger_and_fs_factory`,
  `tests/core/test_lifecycle_pickle.py::test_getstate_strips_lifecyclecore_locks_and_events`.

**Explicitly not defended**

- *A malicious payload inside a trusted scope.* `pickle` executes what it
  loads; the gate controls *when* unpickling is allowed, not *what* is
  loaded. Bytes from an untrusted producer must never be unpickled, scope or
  no scope.
- *Other classes.* The gate guards `ManagedResource` (and
  `PicklableLifecycleCoreMixin` users apply their own); it does nothing for a
  pickle that does not contain one.

**Residual risk.** Trusted scopes entered for untrusted data.

## 5. Log-file hardening (`SafeRotatingFileHandler`, `LoggerConfig`)

| | |
|---|---|
| Assets | Files the log directory's owner did not intend to write; log contents. |
| Trust boundary | `LoggerConfig.log_dir`, `logger_name`, `log_file`; the log directory's contents at open time. |
| Attacker | Can create files and symlinks in the log directory (shared or world-writable directories). |

**Defended**

- Log files are opened with `O_NOFOLLOW` and verified as regular files, so a
  planted symlink is refused rather than written through —
  `tests/core/test_logger.py::test_logger_toctou_symlink_attack_rejected`,
  `tests/core/test_logger.py::test_safe_rotating_file_handler_rejects_symlink_swap`.
- Files are created `0600` regardless of umask —
  `tests/core/test_logger.py::test_logger_uses_restrictive_permissions_on_posix`.
- Names cannot escape the log directory —
  `tests/core/test_logger.py::test_logger_rejects_path_like_log_file_names`,
  `tests/core/test_logger.py::test_logger_rejects_unsafe_logger_name`,
  property `tests/core/test_security_props.py::test_logger_names_cannot_escape_the_log_directory`.
- A closed handler stays closed; in-flight records cannot re-create the file —
  `tests/core/test_logger.py::test_closed_file_handler_drops_records_still_in_flight`.

**Explicitly not defended**

- *The directory itself.* If `log_dir` cannot be created the logger falls
  back to stderr; a `log_dir` that is a symlink to elsewhere is followed
  (directory-level trust, as with the sandbox root).
- *Confidentiality of log contents* beyond `0600` and the secret filter.

## 6. Lifecycle barrier (`LifecycleCore`)

| | |
|---|---|
| Assets | Resources (file handles, connections, filesystems) that must be released exactly once; invariants of code running after `close()`. |
| Trust boundary | Concurrent callers of `close()`/`aclose()`, context exits, garbage collection. |
| Attacker | Not adversarial: the threat is a race or a reentrant call. |

**Defended**

- `close()` is a barrier: the first caller runs cleanup, concurrent callers
  wait for it, a reentrant call from the closer's own cleanup returns —
  `tests/core/test_lifecycle.py::test_concurrent_close_waits_for_cleanup`,
  `tests/core/test_lifecycle.py::test_reentrant_close_from_cleanup_does_not_deadlock`,
  `tests/core/test_lifecycle.py::test_managed_resource_close_idempotency`.
- A filesystem is never attached to a closed resource —
  `tests/core/test_lifecycle.py::test_close_during_fs_factory_discards_filesystem`,
  `tests/core/test_lifecycle.py::test_closed_property_responsive_while_fs_factory_blocks`.
- A live object reclaimed by GC without `close()` is reported —
  `tests/core/test_lifecycle.py::test_gc_finalizer_fires_when_not_closed`.

**Explicitly not defended**

- *Cleanup hooks that misbehave.* A `_cleanup()` that blocks forever blocks
  every concurrent closer; the barrier has no timeout by design.
- *Subclasses that override `__enter__`* and do not call `close()` on their
  own exit path (the exits are `@final`; the enters are not).

## How to argue with this document

The threat model is versioned with the code. To challenge a claim, open a
pull request against this file (or a private report if the challenge is a
working bypass — see [`SECURITY.md`](../SECURITY.md)). A claim without a
test reference is a bug in the document; a test reference that no longer
exists fails CI; a change to a "not defended" entry is a design decision
and needs an entry in `CHANGELOG.md`.
