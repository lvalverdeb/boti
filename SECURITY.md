# Security policy

## Supported versions

| Version | Supported |
|---|---|
| latest minor (1.2.x) | yes — fixes ship as patch releases |
| previous minor (1.1.x) | security fixes only, for 90 days after the next minor's release |
| older | no |

## Reporting a vulnerability

Please do **not** open a public issue for a suspected vulnerability.

1. **Preferred:** GitHub private vulnerability reporting —
   <https://github.com/lvalverdeb/boti/security/advisories/new>.
2. **Fallback:** email the maintainer listed in `pyproject.toml`
   (`lvalverdeb@gmail.com`) with `[boti security]` in the subject.

What to expect:

- acknowledgement within **72 hours**;
- a fix or a documented decision within **90 days** (coordinated disclosure
  by default; sooner if the issue is being exploited, longer only by
  agreement with the reporter);
- credit in the changelog and the GitHub advisory unless you ask otherwise;
- a CVE requested through GitHub Security Advisories when the issue warrants
  one.

## Scope

boti's security claims are stated, per component, in
[`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md), together with what each
component explicitly does **not** defend against. A report is in scope when
it contradicts a stated claim:

- a **path sandbox escape** — `SecureResource` reading or writing outside its
  allowed roots, including through a symlink or a race between validation
  and open;
- an **SSRF guard bypass** — a `FilesystemConfig` that reaches a private,
  reserved or loopback address through any field, spelling or wrapper chain;
- a **secret reaching a log destination** — a value under a credential-like
  key, a `key=value` assignment, a URL credential, or anything in the
  redaction corpus, surviving `PIISecretFilter`, including after
  `Logger.close()`;
- a **pickle gate bypass** — a `ManagedResource` unpickled without
  `allow_pickle` or outside a trusted scope. (A malicious payload executed
  *inside* a trusted scope is not a bypass: the gate exists so that scope is
  never entered for untrusted data.)
- a **log-file hardening failure** — a log written through a symlink, with
  permissions wider than `0600`, or to a path outside the log directory.

Out of scope: anything the threat model lists as *not defended* (for example
DNS rebinding, secrets with no keyword context, remote `fsspec` filesystems),
and findings that require the attacker to already run code as the same OS
user.

## Verifying a release

Every release is published from GitHub Actions through PyPI trusted
publishing and carries a PEP 740 attestation:

```bash
uvx pypi-attestations verify pypi --repository https://github.com/lvalverdeb/boti pypi:boti-<version>-py3-none-any.whl
```

If the command fails for a published file, treat that as a report-worthy
finding.
