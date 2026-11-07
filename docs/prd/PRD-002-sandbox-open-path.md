# PRD-002 — Sandbox open path: `openat2`, root-anchored relative paths

| | |
|---|---|
| Priority | P1 |
| Target | 1.2.0 (minor — behaviour change in R5) |
| Owner | secure_io maintainers |
| Modules | `src/boti/core/secure_io.py`, `src/boti/core/security.py` |
| Status | Draft |

## 1. Problem

Two gaps remain between `SecureResource` and the best available practice.

**Resolution engine.** 1.1.0 closed the check-then-open race by walking the validated path component by component with `O_NOFOLLOW` via `dir_fd` (`_open_beneath`). That is the right discipline, but it is implemented in userspace: one `openat` + `fstat` per component, and the final `O_NOFOLLOW` open. Linux ≥ 5.6 provides `openat2(2)` with `RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS`, which performs the same guarantee atomically in the kernel, including against `..` and `/proc` magic links, in a single syscall. The state of the art on Linux is to use it; boti's walk is the right **fallback**, not the primary.

**Relative paths.** `_secure_path_and_root` calls `Path(path).resolve()`, which anchors a relative path to the **process working directory**, not to `project_root`. Probed on `ebed01f` with `project_root=<tmp>` and `cwd=/`:

```
read_text_secure("README.md") -> PermissionError: Path README.md is outside the configured sandbox roots
```

Worse, when cwd happens to lie inside *another* allowed root (the system temp dir is always allowed), a relative path silently reads a different file than the caller meant. The README's own example only works because it sets `project_root=Path.cwd()`. `extra_allowed_paths` has the same cwd dependence.

## 2. Goals

- G1. On Linux ≥ 5.6, every `open_secure` goes through `openat2` with `RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS`; the component walk is used only where `openat2` is unavailable (older kernels, macOS, BSD); `open()` only where `dir_fd` is unsupported (Windows).
- G2. Relative paths passed to `get_secure_path`, `open_secure`, `*_text_secure` and `extra_allowed_paths` are resolved against `project_root`, never cwd.
- G3. The three strategies share **one** behavioural test suite; which strategy ran is observable for tests but not a public API.
- G4. No regression in the existing bypass tests (`TestSandboxBypassTechniques`, `TestOpenSecure`).

## 3. Non-goals

- Replacing `get_secure_path`'s `resolve()`-based *check* with an fd-based check for callers who only want a `Path`. The check remains advisory; `open_secure` is the enforced path. (Documented in R9.)
- Sandboxing `fsspec` filesystems obtained via `ManagedResource.fs`. Those are remote/virtual and out of scope for a POSIX path sandbox.
- Landlock / seccomp process-level confinement.

## 4. Requirements

### `openat2` strategy
- R1. New private module `src/boti/core/_openat2.py` (no intra-package deps) exposing `available() -> bool` and `open_beneath(dir_fd: int, path: str, flags: int, mode: int) -> int`. Implemented with `ctypes` against `libc.syscall` using `SYS_openat2 = 437` (identical on x86_64, aarch64, riscv64, s390x, ppc64le since the syscall was added to the unified table) and `struct open_how { u64 flags; u64 mode; u64 resolve; }`; `size = 24`. `resolve = RESOLVE_BENEATH (0x08) | RESOLVE_NO_SYMLINKS (0x04) | RESOLVE_NO_MAGICLINKS (0x02)`.
- R2. `available()` is computed once per process: Linux, and a probe `openat2(AT_FDCWD, ".", O_RDONLY|O_DIRECTORY)` succeeds (not `ENOSYS`, not `EINVAL`). Result cached; failure → `False`, never an exception.
- R3. `EXDEV` (escape attempt under `RESOLVE_BENEATH`) and `ELOOP` (symlink under `RESOLVE_NO_SYMLINKS`) are both mapped to the existing `PermissionError("... changed to a symlink after validation")` path — message generalised to "... escapes the sandbox or crosses a symlink".
- R4. `EAGAIN` from `openat2` (documented as possible under `RESOLVE_BENEATH` during concurrent renames) is retried up to 3 times, then surfaced as `PermissionError`.

### Path anchoring
- R5. `_secure_path_and_root(path)`: if `Path(path)` is not absolute, it is joined to `self.project_root` **before** `resolve()`. `~` expansion is **not** performed (a sandboxed API must not resolve to the home directory implicitly).
- R6. `_resolve_extra_allowed_paths`: relative entries are joined to `project_root` the same way; the existing symlink rejection and non-existence warning are unchanged.
- R7. README (×3) and SDD §3.3 state the anchoring rule; the README example drops `project_root=Path.cwd()` in favour of an explicit path and a relative file name, demonstrating the new behaviour.

### Strategy selection and observability
- R8. `secure_io` chooses at import time: `_OPEN_STRATEGY in {"openat2", "walk", "open"}`; selection order `openat2` → `walk` (POSIX with `dir_fd`) → `open`. The chosen strategy is exposed as `SecureResource.open_strategy` (read-only class attribute, documented as diagnostic) and printed by `boti info`.
- R9. `get_secure_path` docstring states that it is a *check*, that TOCTOU protection exists only on `open_secure`-family calls, and recommends them.

### Tests
- R10. `TestOpenSecure` is parametrised over every strategy that can run on the host; the `openat2` strategy is additionally exercised in CI on `ubuntu-latest` (kernel ≥ 5.15) and **asserted** to be the selected strategy there, so a silent fallback fails CI.
- R11. New tests: relative path anchored to `project_root` (cwd outside root → reads root file); relative path with `..` that resolves inside root is allowed, outside is denied; `~` is not expanded; `extra_allowed_paths=["data"]` is `project_root/data`.
- R12. `openat2`-specific: a `..` escape attempt returns `PermissionError` (kernel `EXDEV`); a `/proc/self/fd/N` magic link under root is refused.

## 5. Acceptance criteria

- A1. On `ubuntu-latest` CI, a test asserts `SecureResource.open_strategy == "openat2"`; on macOS CI (new matrix entry, see PRD-004 R9) it asserts `"walk"`.
- A2. All existing `test_security.py` tests pass unchanged except the two whose expectations change under R5 (documented in the PR).
- A3. `examples/secure_resource.py` gains a relative-path demonstration and still runs.
- A4. SDD §3.3/§6.1 describe the three strategies and the anchoring rule; the `openat2` struct layout and resolve flags are documented inline in `_openat2.py` with a reference to `openat2(2)`.
- A5. Full CI gate; `spaghetti` reports no new warnings (the ctypes module is small and flat).

## 6. Design notes

- `ctypes` is stdlib; no new dependency. `libc.syscall` is variadic — declare `argtypes` explicitly (`c_long, c_int, c_char_p, c_void_p, c_size_t`) to avoid 32/64-bit truncation on `size_t`.
- The `walk` strategy stays exactly as shipped in 1.1.0 (it is the macOS/BSD path and has its own regression tests).
- `mode` for `O_CREAT` remains `0o666` (umask applies) in all strategies.

## 7. Risks

| Risk | Mitigation |
|---|---|
| seccomp profiles (Docker default allows `openat2`; some hardened profiles do not) return `EPERM` | `available()` probe treats any error as unavailable and falls back. |
| Behaviour change in R5 breaks callers relying on cwd anchoring | Minor version bump; release note; the old behaviour was indistinguishable from a bug when cwd ≠ root. |
| `ctypes` unavailable (some embedded Pythons) | Import guarded; falls back to `walk`. |

## 8. Open questions

- Should `open_strategy` be forced via an environment variable for testing (`BOTI_OPEN_STRATEGY=walk`)? Recommendation: yes, test-only, read once at import, documented as unsupported for production.
