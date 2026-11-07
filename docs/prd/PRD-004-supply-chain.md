# PRD-004 — Supply chain: trusted publishing, attestations, audit

| | |
|---|---|
| Priority | P1 |
| Target | 1.2.0 (CI/PyPI only; no package code change) |
| Owner | repository owner (PyPI settings) + CI maintainers |
| Files | `.github/workflows/*.yml`, `Makefile`, `pyproject.toml`, `.github/dependabot.yml` |
| Status | Done — shipped in 1.2.0 |

## 1. Problem

Releases 1.0.10, 1.0.11 and 1.1.0 were all published from a laptop with a long-lived API token read from `.env`. The release workflow's trusted-publishing job has failed on every tag with `invalid-publisher` because no publisher is registered on PyPI. Consequences:

- The artifacts on PyPI carry **no provenance**: nothing links `boti-1.1.0.tar.gz` to commit `ebed01f` or to the CI run that tested it.
- A token that can publish any version of `boti` lives on a developer machine.
- Nothing audits the four runtime dependencies (or the dev tree) for known vulnerabilities; `pip-audit` cannot even be run locally on the maintainer's machine (ensurepip crash under uv's interpreter, observed 2026-11-07).
- Action SHAs were pinned in 1.1.0 but nothing keeps them current.
- There is no `SECURITY.md`; a reporter has no channel (covered by PRD-006, referenced here because trusted publishing and disclosure usually land together).

The 2026 baseline for a security-relevant Python library is: trusted publishing (OIDC), PEP 740 attestations on every artifact, a vulnerability scan on every PR, pinned and auto-updated actions, and a documented break-glass path.

## 2. Goals

- G1. Every release artifact on PyPI is published by GitHub Actions via OIDC and carries a PEP 740 attestation verifiable with `pypi-attestations`/`sigstore`.
- G2. The token path still exists but is **break-glass**: it requires an explicit, logged step and is documented as such.
- G3. A known-vulnerable dependency pin fails CI on the PR that introduces it and on a weekly schedule.
- G4. Action SHAs are updated by automation with the version comment kept in sync.
- G5. The published sdist/wheel are reproducible from the tag: same inputs → byte-identical outputs (stretch; see R12).

## 3. Non-goals

- SLSA level 3 build isolation (would require a reusable workflow in a separate repo).
- Signing commits/tags (recommended; owner's call; not required for the objective).
- Mirroring to a private index.

## 4. Requirements

### Trusted publishing (owner action + workflow)
- R1. Register a trusted publisher on PyPI for project `boti`: owner `lvalverdeb`, repository `boti`, workflow `publish.yml`, environment `pypi`. **This is the only step not executable from the repository.**
- R2. The `pypi` GitHub environment is created with **required reviewers** = the owner, so a publish needs an explicit approval click even on a tag push. Protection rule: deployment branches limited to tags matching `v*`.
- R3. `publish.yml` keeps `permissions: id-token: write` scoped to the publish job only (already true) and adds `contents: read` at workflow level and `attestations: write` on the publish job.
- R4. `pypa/gh-action-pypi-publish` is invoked with `attestations: true` (default in v1.11+; make it explicit) so PEP 740 attestations are generated and uploaded.
- R5. The release job verifies the built artifacts' version matches the tag (`uv run python -c "import tomllib…"` vs `${GITHUB_REF_NAME#v}`) and fails otherwise. This closes the "tag v1.1.0 but pyproject says 1.0.11" class of error.
- R6. After publish, a post-step runs `uvx pypi-attestations verify pypi …` (pinned) against the just-published files and fails the workflow if verification fails. The workflow summary links the attestation.

### Break-glass token path
- R7. `make publish` is renamed `make publish-breakglass`; it prints a 3-line warning naming the preferred path, requires `BOTI_BREAKGLASS=1` in addition to the token, and is documented in `RELEASING.md` (PRD-006) as the fallback when GitHub or PyPI OIDC is down. `publish-test` (TestPyPI) keeps the token path without ceremony.
- R8. The PyPI API token is rotated after R1 is confirmed working, scoped to the `boti` project only, and its creation date noted in `RELEASING.md` with a 12-month rotation reminder.

### Vulnerability audit
- R9. New workflow `audit.yml`: runs on `pull_request`, `push` to `main`, and `schedule` weekly. Steps: checkout (pinned), setup-uv (pinned), `uv export --no-hashes --format requirements-txt > req.txt` for (a) runtime only and (b) `--group dev`, then `pypa/gh-action-pip-audit` (pinned) on each. Runtime findings fail the job; dev findings fail only on `schedule` (so a PR isn't blocked by a ruff CVE, but it surfaces weekly). Known-accepted findings are listed with `--ignore-vuln` and a comment carrying the justification and an expiry date.
- R10. The same workflow adds a macOS matrix entry to `ci.yml`'s test job (`macos-latest`) so PRD-002's `walk` strategy and the Darwin `ENOTDIR` path are exercised where they apply. (Placed here because it is the same CI-surface change.)

### Pin maintenance
- R11. `.github/dependabot.yml` with `package-ecosystem: github-actions`, weekly, grouped, and `commit-message.prefix: "ci"`. Dependabot preserves the `# vX.Y.Z` comment when the pin is a SHA. `uv` dependency updates are **not** delegated to Dependabot (it does not understand `uv.lock`); a monthly `make upgrade-check` target runs `uv lock --upgrade --dry-run` and prints the diff for a human.

### Reproducibility (stretch)
- R12. `uv build` is run with `SOURCE_DATE_EPOCH` set to the tag's commit timestamp in `publish.yml`; `RELEASING.md` documents how to rebuild locally and compare hashes. Acceptance is "documented and attempted", not "guaranteed", because setuptools' reproducibility has known caveats.

## 5. Acceptance criteria

- A1. A release from a `v*` tag succeeds through `publish.yml` with **no** manual upload; the PyPI file page shows "Publisher: GitHub" and an attestation.
- A2. `uvx pypi-attestations verify pypi --repository https://github.com/lvalverdeb/boti pypi:boti-<ver>-py3-none-any.whl` succeeds from a clean machine.
- A3. A test PR that pins a dependency with a known advisory (e.g. an old `pyarrow`) fails `audit.yml`; the PR is closed, not merged — the run link is attached to the PRD's tracking issue.
- A4. `make publish` no longer exists; `make publish-breakglass` refuses without `BOTI_BREAKGLASS=1`.
- A5. Dependabot opens its first grouped actions PR within a week of merge, and the SHA comments are intact.
- A6. `ci.yml` has a macOS job that passes; total CI wall-clock stays under 6 minutes.
- A7. SDD gains a short §11 "Release and supply chain" describing the flow; `RELEASING.md` exists (PRD-006 owns its full content).

## 6. Design notes

- All new actions pinned by SHA with version comments, matching the existing convention.
- `audit.yml` must not need `id-token`; it has `contents: read` only.
- The weekly schedule should avoid the top of the hour (`cron: "37 6 * * 1"`) to dodge GitHub's congested slots.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Owner delays R1; releases stall | The break-glass path remains functional; the objective is simply not met until R1 happens. |
| `pip-audit` false positives block PRs | `--ignore-vuln` with justification + expiry; dev-group findings don't block PRs. |
| macOS runners are slower/flakier | Tests only (no lint/typecheck duplication); `fail-fast: false`. |

## 8. Open questions

- Should the `pypi` environment's required reviewer be enforced for TestPyPI too? Recommendation: no — TestPyPI is for rehearsal.
