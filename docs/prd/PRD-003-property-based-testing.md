# PRD-003 — Property-based testing of the parsers

| | |
|---|---|
| Priority | P1 |
| Target | 1.2.0 |
| Owner | all maintainers |
| Modules | `tests/`, `pyproject.toml` (dev group), `.github/workflows/ci.yml` |
| Status | Draft |

## 1. Problem

Five review rounds found bugs in the same three places by hand: the SSRF host rules (`127.1`, IPv4-mapped IPv6, `\@` parser differentials, trailing dots), the secret filter (compound keys, quoted keys, URL userinfo), and path handling (unresolved `/var` vs `/private/var`, cwd-anchored relatives). Every one of these is a **parser** with an input space far larger than its example-based tests. Example tests prove the cases the author thought of; the bugs were the cases nobody thought of.

The state of the art for this class of code is property-based testing: generate inputs, assert invariants, let the framework shrink failures. boti has none.

## 2. Goals

- G1. Each of the three parsers has at least one property that encodes its *security contract*, not its implementation.
- G2. Properties run in CI deterministically (no flaky failures from randomness) within a 60 s budget total.
- G3. Failing examples are reproducible from CI output alone.
- G4. The redaction corpus (PRD-001 §4) and the property generators share one source of truth for keyword and shape lists.

## 3. Non-goals

- Fuzzing with coverage guidance (atheris/AFL). Valuable later; different infrastructure.
- Properties for the lifecycle barrier. Concurrency properties need a different tool (e.g. Hypothesis stateful testing is possible but out of scope here).

## 4. Requirements

### Infrastructure
- R1. Add `hypothesis>=6.100,<7` to the `dev` dependency group only. No runtime impact.
- R2. `tests/conftest.py` registers two Hypothesis profiles: `ci` (`derandomize=True`, `max_examples=200`, `deadline=None`, `database=None`, `print_blob=True`) and `dev` (`max_examples=1000`, default database). CI selects `ci` via `HYPOTHESIS_PROFILE`; the Makefile `test` target uses `dev`.
- R3. Property tests live beside the module they test (`tests/core/test_filesystem_security_props.py`, `test_logger_filters_props.py`, `test_security_props.py`) and carry the `@pytest.mark.property` marker so they can be selected or skipped.

### SSRF host rules (`filesystem_security.py`)
- R4. **Non-global addresses are always rejected, in every spelling.** Generate an `IPv4Address`/`IPv6Address` via `st.ip_addresses()`; filter to `not addr.is_global or addr.is_multicast`; derive spellings — dotted, integer (`int(addr)`), hex octets, short forms (`127.1`), IPv4-mapped IPv6 (`::ffff:a.b.c.d` and `::ffff:XXXX:XXXX`), bracketed, with trailing dot, mixed case, with and without port, with `http://`/`https://`/schemeless `//`. Property: `_reject_private_host(url)` raises `ValueError`.
- R5. **Global addresses with no reserved name are accepted.** Same generator, filter to `is_global and not is_multicast`, same spellings. Property: does not raise. (This guards against the filter becoming "reject everything", which would also pass R4.)
- R6. **Allowlist is exact.** For any private address `a`, allowlisting `str(a)` accepts `//a` and `//a:PORT` but allowlisting `str(a):PORT` accepts only `//a:PORT`.
- R7. **Parser-differential inputs never reach the host check.** Generate URLs containing any character from `_URL_AMBIGUOUS_CHARS_RE`'s class or a `user:pass@` section at any position; property: `ValueError` whose message mentions "ambiguous"/"userinfo", and `_is_private_ip` was not called (monkeypatch spy).
- R8. **Wrapper-chain depth.** Generate `fs_options` with `target_options` nesting depth `d ∈ [0, 12]` and a private endpoint at the innermost layer; property: `ValueError` for all `d` (rejected either for the endpoint or for depth), never `RecursionError`.
- R9. **`_host_authority` round-trips.** For any host string without the ambiguous characters and optional port, `urlparse(_host_authority(h, p)).hostname == h.lower().strip("[]")` and `.port == p`.

### Secret filter (`logger_filters.py`) — depends on PRD-001
- R10. **A secret under a sensitive key never survives.** Generate a 20-char random token `S` (alphabet excludes regex metacharacters that could appear in keys), a key by concatenating random prefix/suffix identifiers around a keyword from the shared keyword list (`db_` + `password` + `_v2`, case-randomised, `-`/`_`/space separators), and a container shape from `{dict, list, tuple, set, dataclass, SimpleNamespace, pydantic model, bytes-of-json, json string}` nested to depth `≤ 4`. Place `(key, S)` at a random leaf. Property: `S not in` the formatted output of a record whose `args` or `extra` contains the structure.
- R11. **Keyword-free values survive.** Same shapes, key chosen from identifiers that contain **no** keyword (`passport`, `tokyo`, `secretary` is a known false positive and excluded by the generator), value `S`. Property: `S in` output. Guards against the filter redacting everything.
- R12. **URL credentials never survive.** Generate `scheme://user:S@host[:port]/path` for schemes from `{http, https, postgresql, mysql, redis, amqp, s3, ftp}`; place as `msg`, as an arg, and inside a dict value. Property: `S not in` output and `host` **is** in output.
- R13. **Idempotence.** Filtering a record twice yields the same output as once.

### Env bindings and names (`security.py`, `models.py`)
- R14. **`validate_environment_bindings` accepts exactly the POSIX-safe set.** Generate keys from `st.text()`; property: accepted iff `fullmatch([A-Za-z_][A-Za-z0-9_]*)`; values accepted iff they contain none of `\x00 \n \r \t`.
- R15. **Identifier validators agree with the regex and with each other.** `is_valid_dotted_identifier(x) == all(is_valid_identifier(p) for p in x.split("."))` for non-empty `x`; `has_dunder_identifier` is `True` iff some regex token starts and ends with `__`.
- R16. **`LoggerConfig` names can't escape the log directory.** Generate `logger_name`/`log_file` from `st.text()`; property: either validation raises, or `Path(log_dir, f"{effective}.log").resolve().parent == Path(log_dir).resolve()`.

### Sandbox paths (`secure_io.py`) — depends on PRD-002
- R17. **Containment is decided by components, not strings.** Generate a root `R` and a sibling `R'` whose string form starts with `str(R)`; for random relative suffixes, property: `_sandbox_root_for(R'/suffix)` is `None` when only `R` is allowed.
- R18. **Relative paths anchor to the root.** For any relative path without `..` segments, `get_secure_path(p) == (project_root / p).resolve()` regardless of cwd (parametrised over cwd ∈ {root, /, tempdir}).

## 5. Acceptance criteria

- A1. All properties pass under the `ci` profile in ≤ 60 s wall-clock on `ubuntu-latest` (measured in the PR).
- A2. Each property has a one-paragraph docstring stating the contract in plain English (the properties are also documentation).
- A3. At least one property in each file is shown — in the PR description — to **fail** against the pre-PRD-001 filter or a deliberately broken parser (mutation check), proving the property has teeth.
- A4. `make test` runs the `dev` profile; `make verify` runs `ci`; both documented in the Makefile help.
- A5. SDD §8 gains a "Property-based tests" subsection listing the contracts (R4–R18 condensed).

## 6. Design notes

- Keep generators in `tests/strategies.py`; the keyword list is imported from `boti.core.logger_filters` so the property and the implementation cannot drift.
- `st.ip_addresses()` yields `ipaddress` objects; spelling derivation is a pure function in `tests/strategies.py` with its own small example tests.
- Hypothesis's `deadline` is disabled in CI to avoid flakiness from shared runners; per-example cost is bounded by `max_examples` instead.

## 7. Risks

| Risk | Mitigation |
|---|---|
| CI time creep as properties are added | `max_examples=200` in `ci`; budget asserted by a `--durations` check in the PR. |
| Generators encode the implementation's assumptions | R5/R11 negative properties; A3 mutation check. |
| `hypothesis` import in `conftest.py` breaks test collection when dev group isn't synced | Conftest imports lazily inside the profile-registration function and skips `property`-marked tests if unavailable. |
