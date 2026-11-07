# Releasing boti

A release is a tag `vX.Y.Z` on `main`, a GitHub release from that tag, and the
`publish.yml` workflow that builds, publishes through PyPI trusted publishing
and verifies the attestations. Nothing is uploaded by hand. Anyone with write
access to the repository and the `pypi` environment can cut one by following
this list; every step is verifiable.

## Checklist

1. **Decide the version.** Patch for fixes with no behaviour change, minor
   for behaviour changes or new surface (list them under **Changed** in bold),
   major for removals.
2. **Bump the version** in two places. boti is a member of a parent uv
   workspace, so `uv lock` run inside this checkout rewrites the *parent's*
   lock, not this repository's:
   - `pyproject.toml` → `version = "X.Y.Z"`;
   - `uv.lock` → the `version = "…"` line directly under `name = "boti"`.
     For a version-only bump edit that line by hand. If dependencies changed,
     regenerate the lock in a standalone copy and copy it back:
     `tmp=$(mktemp -d) && git archive HEAD | tar -x -C "$tmp" && (cd "$tmp" && uv lock) && cp "$tmp/uv.lock" uv.lock`.
3. **Changelog.** Rename `## [Unreleased]` in `CHANGELOG.md` to
   `## [X.Y.Z] - YYYY-MM-DD` and add a fresh empty `[Unreleased]` above it.
   Every behaviour change is under **Changed** in bold; security fixes under
   **Security**.
4. **Threat model.** If a security component changed, update
   `docs/THREAT_MODEL.md` and its revision line.
5. **Verify locally** — the same gate CI runs: `make verify` (lint, format,
   mypy, tests with coverage under the Hypothesis `ci` profile).
6. **Run the examples** that touch what changed (`python examples/<name>.py`);
   `tests/test_examples_index.py` keeps the index complete.
7. **SDD §8** — update the test counts and the file-level table.
8. **Commit** the bump (`build: bump to X.Y.Z`) and **push** `main`.
9. **Watch CI** — `gh run watch` until the test, lint, type-check and audit
   jobs are green on Linux and macOS.
10. **Tag and release.** Tag the CI-green commit and create the release with
    notes copied from the changelog section:
    ```bash
    git tag -a vX.Y.Z <sha> -m "boti X.Y.Z" && git push origin vX.Y.Z
    gh release create vX.Y.Z --verify-tag --title "boti X.Y.Z" --notes-file <(sed -n '/^## \[X.Y.Z\]/,/^## \[/p' CHANGELOG.md | sed '$d')
    ```
    Do **not** use `gh release create --target <sha>` for a commit that is not
    the branch tip — the API answers 422; tag first, then release from the tag.
11. **Approve the `pypi` environment** when the workflow pauses for review
    (required reviewers are set on the environment).
12. **Confirm `publish.yml` succeeded end to end**, including the
    *Verify the published attestations* step. The run summary links the PyPI
    file page; it must show *Publisher: GitHub* and an attestation.
13. **Post-release smoke test** from a clean environment:
    ```bash
    uv venv /tmp/boti-smoke && VIRTUAL_ENV=/tmp/boti-smoke uv pip install "boti==X.Y.Z"
    /tmp/boti-smoke/bin/python -c "import boti; print(boti.__name__)"
    /tmp/boti-smoke/bin/boti check
    uvx pypi-attestations verify pypi --repository https://github.com/lvalverdeb/boti pypi:boti-X.Y.Z-py3-none-any.whl
    ```
14. **Mark the PRDs** that shipped as done in `docs/prd/README.md`.

## When the release job fails

| Symptom | Cause | Fix |
|---|---|---|
| `invalid-publisher: valid token, but no corresponding publisher` | No trusted publisher is registered on PyPI for this repository/workflow/environment. | On PyPI → project *boti* → *Publishing*: owner `lvalverdeb`, repository `boti`, workflow `publish.yml`, environment `pypi`. Re-run the job (the release is still published on GitHub; nothing reached PyPI). |
| `gh release create --target <sha>` → HTTP 422 | GitHub refuses to create a tag on a non-tip commit through the release API. | Create and push the tag with `git`, then `gh release create vX.Y.Z --verify-tag`. |
| `uv sync --locked` fails: *lockfile needs to be updated* | `uv.lock` was not regenerated after a dependency change, or the `boti` version line in it was not bumped. | Step 2 above; commit the lock. |
| *tag vX.Y.Z does not match pyproject version* | The tag was cut before the bump commit, or on the wrong commit. | Delete the GitHub release and the tag (`gh release delete`, `git push --delete origin vX.Y.Z`), tag the right commit. |
| *attestation verification failed* after a successful upload | PyPI has not yet served the provenance (the step retries for two minutes), or the upload was made with an API token (no attestation). | Re-run the step; if the upload came from `publish-breakglass`, the release has no attestation — say so in the release notes. |

## Appendix — break-glass token publish

Use only when GitHub Actions or PyPI's OIDC exchange is unavailable and the
release cannot wait. This path produces artifacts **without** attestations.

1. Confirm the tag and GitHub release exist and CI is green on the tagged
   commit.
2. Put a project-scoped PyPI token in `.env` as `UV_PUBLISH_TOKEN`
   (`.env` is git-ignored).
3. `BOTI_BREAKGLASS=1 make publish-breakglass` — runs the full gate and the
   build, then uploads. Without `BOTI_BREAKGLASS=1` the target refuses.
4. Note in the release notes that the artifacts were token-published and
   carry no attestation.
5. **Rotate the token** afterwards (PyPI → account → API tokens): revoke the
   one used, create a new project-scoped one only when next needed.

Token hygiene: the token in `.env`, when one exists, must be scoped to the
`boti` project only. Record its creation date here when rotating and rotate
at least every 12 months.

- Current token created: _not recorded — rotate and record on first use of
  this appendix._
