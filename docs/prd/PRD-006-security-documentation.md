# PRD-006 — Security documentation and disclosure

| | |
|---|---|
| Priority | P2 |
| Target | 1.2.0 (lands last; documents the state after PRD-001…005) |
| Owner | repository owner |
| Files | `SECURITY.md`, `docs/THREAT_MODEL.md`, `RELEASING.md`, `SDD.md`, `README*.md`, `CHANGELOG.md` |
| Status | Draft |

## 1. Problem

boti's security properties exist only as code comments, SDD §6, and this session's review transcripts. There is:

- no **reporting channel** — a researcher who finds a sandbox bypass has nowhere to send it but a public issue;
- no **threat model** — the SDD says what each check does, not what attacker it assumes, so readers cannot tell deliberate non-goals (DNS rebinding, entropy-based secret detection, `fsspec` remote paths) from oversights;
- no **release procedure** — three releases were cut by one person following memory, and the trusted-publishing failure went unnoticed for two of them;
- no **changelog** — behaviour changes (fail-closed sandbox root, new SSRF rejections) live only in GitHub release notes, which are not in the sdist.

"State of the art" is partly a process claim; these four documents are the process.

## 2. Goals

- G1. A vulnerability reporter can find, within one click from the README, how to report privately and what response to expect.
- G2. A reader can tell, for every security check, which attacker it defends against and which it explicitly does not.
- G3. A release can be cut by someone other than the owner by following a checklist, and every step is verifiable.
- G4. Every behaviour change since 1.0.0 is recorded in a changelog shipped in the sdist.

## 3. Non-goals

- A bug bounty.
- A formal security audit (recommended once PRD-001…004 land; out of scope to *require* here).
- CVE issuance process beyond "we will request one via GitHub Security Advisories when warranted".

## 4. Requirements

### `SECURITY.md`
- R1. Supported versions table (latest minor only; patch releases for the previous minor for 90 days after a new minor).
- R2. Reporting: GitHub private vulnerability reporting enabled on the repository (**owner setting**) and named as the channel; an email fallback; a 72-hour acknowledgement target and 90-day coordinated-disclosure default.
- R3. Scope statement linking to `docs/THREAT_MODEL.md`: what counts as a vulnerability (sandbox escape, SSRF guard bypass, secret reaching a destination, pickle gate bypass that is *not* "the payload was malicious") and what does not (anything listed as a non-goal in the threat model).
- R4. Linked from the README (×3, one line, line-for-line sync preserved) and from `pyproject.toml` `[project.urls] Security`.

### `docs/THREAT_MODEL.md`
- R5. One section per security component — Path sandbox, SSRF guard, Secret filter, Pickle gate, Log-file hardening, Lifecycle barrier — each with: **assets**, **trust boundary**, **attacker capabilities assumed**, **defended**, **explicitly not defended** (with the reason), **residual risk**, **tests that pin the property** (file::test names, and the PRD-003 property names).
- R6. Content must be derived from the code at the time of writing and cross-checked against SDD §6; where the SDD and the threat model would disagree, the SDD is corrected in the same PR.
- R7. The "not defended" lists must include at minimum: DNS rebinding and hostname-based SSRF; secrets without keyword context; pickle payloads from untrusted producers; `fsspec` remote filesystems obtained via `ManagedResource.fs`; ancestors of the sandbox root; processes sharing the OS user; attacker with write access to `ENDPOINT_ALLOWLIST` at runtime.
- R8. A "How to argue with this document" closing section: the threat model is versioned, changes go through PR review, and each release's tag references the threat-model revision it was built against.

### `RELEASING.md`
- R9. Numbered checklist: version bump (`pyproject.toml` + `uv.lock` via standalone `uv lock` — the workspace-nesting caveat documented), `CHANGELOG.md` entry, `make verify`, examples run, SDD §8 counts, commit, push, watch CI, tag `vX.Y.Z` with release notes copied from the changelog, confirm `publish.yml` succeeded **including attestation verification** (PRD-004 R6), post-release smoke test (`uv venv` + `uv pip install boti==X.Y.Z` + `python -c "import boti"` + `boti check`), and the break-glass procedure as a clearly separated appendix.
- R10. A "what to do when the release job fails" section covering the three failures already seen in this project: `invalid-publisher`, `--target <sha>` 422, stale `uv.lock`.

### `CHANGELOG.md`
- R11. Keep-a-Changelog format, `Unreleased` section at top, entries back-filled for 1.0.10, 1.0.11 and 1.1.0 from the existing GitHub release notes and commit messages, each with **Added / Changed / Fixed / Security** headings; behaviour changes under **Changed** in bold.
- R12. Included in the sdist via `[tool.setuptools] include-package-data` / `MANIFEST.in` as appropriate; a test asserts `CHANGELOG.md` is present in `uv build`'s sdist listing (cheap: `tarfile` on `dist/*.tar.gz` in a CI step, not in pytest).
- R13. CI check (`ci.yml` lint job): if `src/` changed in a PR, `CHANGELOG.md` must also have changed, unless the PR carries the `no-changelog` label. Implemented as a small shell step, not an action.

### SDD alignment
- R14. SDD §6 gains a pointer to the threat model and drops any prose the threat model now owns; SDD §11 (from PRD-004) points to `RELEASING.md`. The SDD stays the design document; it stops being the only security document.

## 5. Acceptance criteria

- A1. `SECURITY.md`, `docs/THREAT_MODEL.md`, `RELEASING.md`, `CHANGELOG.md` exist; GitHub's "Security" tab shows the policy; private vulnerability reporting is enabled (screenshot or `gh api repos/lvalverdeb/boti --jq .security_and_analysis` in the PR).
- A2. Every "defended" claim in the threat model names at least one test or property that exercises it, and each named test exists (a script in the PR greps them).
- A3. A maintainer who is not the owner performs a TestPyPI release following `RELEASING.md` without asking a question; gaps found are fixed in the same PR.
- A4. The 1.2.0 sdist contains `CHANGELOG.md`; the R13 check is green on the PR that adds it and red on a deliberately changelog-less test PR (closed, linked).
- A5. README ×3 remain line-for-line in sync after R4.

## 6. Design notes

- Threat model prose should be short and tabular; it is read by people deciding whether to depend on boti, not by the people who wrote it.
- Avoid duplicating SDD text; link to section anchors.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Documents drift from code | A2's test-name cross-check; R13's changelog gate; threat-model revision referenced from each tag. |
| Owner-only settings (private reporting) delay A1 | Listed up front as the one non-repository step, like PRD-004 R1. |
