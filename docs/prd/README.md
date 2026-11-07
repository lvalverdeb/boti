# boti — "State of the Art" programme

## Objective

Make boti the reference implementation for its category — *small, security-conscious Python runtime primitives* (sandboxed file access, SSRF-guarded storage configuration, redacting logger, lifecycle barrier) — such that a security engineer reviewing it finds nothing a mainstream alternative does better.

This is a statement about three things at once: the **code** (no known gap behind an off-the-shelf tool), the **evidence** (properties are tested, not just examples), and the **process** (releases are attested, dependencies are audited, vulnerabilities have a reporting path).

## Definition of done

The objective is met when every line below is true at a tagged release:

| # | Criterion | Verified by |
|---|---|---|
| D1 | No secret shape in the redaction corpus (PRD-001 §4) reaches a log destination, including after `Logger.close()` | `test_logger_filters.py` + Hypothesis property |
| D2 | On Linux ≥ 5.6, `open_secure` uses `openat2(RESOLVE_BENEATH \| RESOLVE_NO_SYMLINKS)`; elsewhere the component walk; both paths share one test suite | PRD-002 acceptance |
| D3 | Relative paths are anchored to `project_root`, never to `cwd` | PRD-002 acceptance |
| D4 | The three parsers (SSRF host rules, PII regexes, env-binding validator) have Hypothesis properties running in CI under 60 s | PRD-003 acceptance |
| D5 | Releases are published by trusted publishing with PEP 740 attestations; the token path is break-glass only | PRD-004 acceptance |
| D6 | `pip-audit` runs on every PR and weekly; a known-vulnerable pin fails CI | PRD-004 acceptance |
| D7 | Every `FilesystemConfig` field is settable from `from_env_prefix`; `LoggerConfig` has no dead fields | PRD-005 acceptance |
| D8 | `SECURITY.md`, a threat model, and a release checklist exist and match the code | PRD-006 acceptance |
| D9 | Coverage ≥ 90% overall and ≥ 80% for every module including `main.py` | CI gate |

## PRDs

| ID | Title | Priority | Target | Depends on | Status |
|---|---|---|---|---|---|
| [PRD-001](PRD-001-secret-redaction.md) | Secret redaction that fails closed | P0 | 1.1.1 | — | Done (1.1.1) |
| [PRD-002](PRD-002-sandbox-open-path.md) | Sandbox open path: `openat2`, root-anchored relative paths | P1 | 1.2.0 | — | Planned |
| [PRD-003](PRD-003-property-based-testing.md) | Property-based testing of the parsers | P1 | 1.2.0 | PRD-001 (shares corpus) | Planned |
| [PRD-004](PRD-004-supply-chain.md) | Supply chain: trusted publishing, attestations, audit | P1 | 1.2.0 | owner action on PyPI | Planned |
| [PRD-005](PRD-005-config-and-cli-parity.md) | Configuration surface parity and CLI coverage | P2 | 1.2.0 | — | Planned |
| [PRD-006](PRD-006-security-documentation.md) | Security documentation and disclosure | P2 | 1.2.0 | PRD-001, PRD-002 (documents them) | Planned |

## Sequencing

```
1.1.1  PRD-001                      (patch: the filter does not do what it says)
1.2.0  PRD-002 + PRD-003 + PRD-005  (minor: relative-path anchoring is a behaviour change)
       PRD-004 in parallel           (CI/PyPI only; no package change)
       PRD-006 last                  (documents the state after the above)
```

PRD-004's trusted-publisher registration is the only item not executable from the repository; everything else is a PR.

## Constraints that apply to every PRD

- Project standards from `CLAUDE.md`: functions ≤ 50 lines, ≤ 3 returns, ≤ 6 params, nesting ≤ 5; `spaghetti` and `fenceline` must not regress.
- `import boti` stays lazy (no `boti.core` import at module level).
- Runtime dependencies stay at `fsspec`, `pyarrow`, `pydantic`, `pydantic-settings`. No new runtime dependency is accepted by any PRD below; `hypothesis` is dev-only.
- The three READMEs stay line-for-line in sync.
- No PRD may weaken an existing security check to simplify its implementation.
