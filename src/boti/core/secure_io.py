"""
Sandboxed I/O resource for Boti Tools.

Provides the SecureResource class which enforces path sandboxing
to prevent path traversal attacks during file operations.
"""

from __future__ import annotations

import errno
import os
import stat
import tempfile
import warnings
from collections.abc import Callable
from pathlib import Path
from typing import IO, Any, ClassVar, NoReturn

__all__ = ["SecureResource"]

from boti.core import _openat2
from boti.core.managed_resource import ManagedResource
from boti.core.models import ResourceConfig
from boti.core.project import ProjectService

# open() mode letters → os.open() flags. "b"/"t" only affect the text layer
# that os.fdopen() adds, so they are stripped before the lookup.
_MODE_FLAGS: dict[str, int] = {
    "r": os.O_RDONLY,
    "r+": os.O_RDWR,
    "w": os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
    "w+": os.O_RDWR | os.O_CREAT | os.O_TRUNC,
    "a": os.O_WRONLY | os.O_CREAT | os.O_APPEND,
    "a+": os.O_RDWR | os.O_CREAT | os.O_APPEND,
    "x": os.O_WRONLY | os.O_CREAT | os.O_EXCL,
    "x+": os.O_RDWR | os.O_CREAT | os.O_EXCL,
}
# Same creation mode open() uses; the process umask narrows it.
_CREATE_MODE = 0o666
# Errno a NOFOLLOW open raises when the component is a symlink (ELOOP on
# Linux/macOS, EMLINK on some BSDs), plus EXDEV: openat2(RESOLVE_BENEATH)'s
# verdict for a resolution that would leave the sandbox root.
_SYMLINK_ERRNOS = frozenset({errno.ELOOP, getattr(errno, "EMLINK", errno.ELOOP)})
_ESCAPE_ERRNOS = _SYMLINK_ERRNOS | {errno.EXDEV}
# openat2 may report EAGAIN under RESOLVE_BENEATH when a concurrent rename made
# the resolution unsafe; it is safe to retry a few times.
_OPENAT2_ATTEMPTS = 3
# The component-wise open needs openat()-style dir_fd support, which Windows lacks.
_OPEN_BENEATH_SUPPORTED = os.name == "posix" and os.open in os.supports_dir_fd
# Test-only override of the strategy selection below (BOTI_OPEN_STRATEGY=walk);
# read once at import, unsupported for production.
_STRATEGY_ENV_VAR = "BOTI_OPEN_STRATEGY"


def _select_open_strategy() -> str:
    """Pick the strongest open path the host supports: openat2 → walk → open."""
    supported = ["open"]
    if _OPEN_BENEATH_SUPPORTED:
        supported.insert(0, "walk")
    if _openat2.available():
        supported.insert(0, "openat2")
    forced = os.environ.get(_STRATEGY_ENV_VAR)
    return forced if forced in supported else supported[0]


_OPEN_STRATEGY = _select_open_strategy()


def _open_flags(mode: str) -> int:
    key = mode.replace("b", "").replace("t", "")
    try:
        return _MODE_FLAGS[key]
    except KeyError:
        raise ValueError(f"invalid mode: {mode!r}") from None


def _open_directory_beneath(name: str | Path, dir_fd: int | None) -> int:
    """Open directory *name* relative to *dir_fd* without following a symlink.

    ``O_NOFOLLOW`` alone, then ``fstat``: with ``O_DIRECTORY`` added, macOS
    reports a symlink as ENOTDIR rather than ELOOP, which would make the
    swap indistinguishable from a benign error. ``O_NONBLOCK`` keeps a planted
    FIFO from hanging the open.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    fd = os.open(name, flags, dir_fd=dir_fd)
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise NotADirectoryError(errno.ENOTDIR, os.strerror(errno.ENOTDIR), str(name))
    except BaseException:
        os.close(fd)
        raise
    return fd


def _fdopen(fd: int, mode: str, **kwargs: Any) -> IO[Any]:
    try:
        return os.fdopen(fd, mode, **kwargs)
    except Exception:
        os.close(fd)
        raise


def _relative_components(root: Path, target: Path) -> tuple[list[str], str]:
    """*target* as components under *root*; a resolved path never carries ``..``.

    ``relative_to`` is lexical, so a ``..`` here means the caller bypassed
    resolution; refuse it (EXDEV, the same verdict openat2 gives an escape)
    rather than let the walk step above the root.
    """
    *directories, name = target.relative_to(root).parts or ("",)
    if not name:
        raise IsADirectoryError(errno.EISDIR, os.strerror(errno.EISDIR), str(target))
    if ".." in directories or name == "..":
        raise OSError(errno.EXDEV, os.strerror(errno.EXDEV), str(target))
    return directories, name


def _open_beneath(root: Path, target: Path, mode: str, **kwargs: Any) -> IO[Any]:
    """``walk`` strategy: open *target* under *root* without following any symlink.

    ``get_secure_path()`` resolves symlinks and checks the result, but a
    directory on that path can be swapped for a symlink between the check and
    the open. Walking from *root* one component at a time with ``O_NOFOLLOW``
    (via ``dir_fd``) makes such a swap fail with ELOOP instead of being
    followed out of the sandbox.
    """
    directories, name = _relative_components(root, target)
    dir_fd = _open_directory_beneath(root, None)
    try:
        for directory in directories:
            next_fd = _open_directory_beneath(directory, dir_fd)
            os.close(dir_fd)
            dir_fd = next_fd
        flags = _open_flags(mode) | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(name, flags, _CREATE_MODE, dir_fd=dir_fd)
    finally:
        os.close(dir_fd)
    return _fdopen(fd, mode, **kwargs)


def _open_beneath_openat2(root: Path, target: Path, mode: str, **kwargs: Any) -> IO[Any]:
    """``openat2`` strategy: the same guarantee as :func:`_open_beneath`, enforced by the kernel.

    One syscall resolves the whole relative path under the root descriptor
    with ``RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS``;
    an escape is ``EXDEV``, a symlink ``ELOOP`` — both mapped to
    ``PermissionError`` by the caller, like the walk's ``ELOOP``.
    """
    directories, name = _relative_components(root, target)
    relative = os.path.join(*directories, name)
    flags = _open_flags(mode) | os.O_NOFOLLOW | os.O_CLOEXEC
    root_fd = _open_directory_beneath(root, None)
    try:
        for attempt in range(1, _OPENAT2_ATTEMPTS + 1):
            try:
                fd = _openat2.open_beneath(root_fd, relative, flags, _CREATE_MODE)
                break
            except OSError as exc:
                if exc.errno != errno.EAGAIN or attempt == _OPENAT2_ATTEMPTS:
                    raise
    finally:
        os.close(root_fd)
    return _fdopen(fd, mode, **kwargs)


def _open_plain(root: Path, target: Path, mode: str, **kwargs: Any) -> IO[Any]:
    """``open`` strategy: no ``dir_fd`` support (Windows); check-then-open only."""
    _open_flags(mode)  # same mode validation (and error) as the other strategies
    return open(target, mode, **kwargs)


_OPENERS: dict[str, Callable[..., IO[Any]]] = {
    "openat2": _open_beneath_openat2,
    "walk": _open_beneath,
    "open": _open_plain,
}


class SecureResource(ManagedResource):
    """
    Enhanced resource that automatically enforces path sandboxing for all I/O.

    Ensures that all operations remain within the project root, the system
    temporary directory, or explicitly configured extra allowed paths to
    prevent path traversal. Relative paths are anchored to ``project_root``,
    never to the process working directory; ``~`` is not expanded.
    """

    # Diagnostic, read-only: which open path this host uses — "openat2"
    # (kernel-enforced, Linux >= 5.6), "walk" (O_NOFOLLOW component walk via
    # dir_fd; macOS/BSD/older Linux) or "open" (no dir_fd support; Windows).
    open_strategy: ClassVar[str] = _OPEN_STRATEGY

    def __init__(self, config: ResourceConfig | None = None, **kwargs: Any) -> None:
        """
        Initialize the SecureResource.
        Sets the project root for sandboxing context.
        """
        super().__init__(config=config, **kwargs)

        # super().__init__() has already attached the GC leak finalizer. If the
        # sandbox config is rejected here, there is no live resource to leak, so
        # detach it rather than let a half-built object warn when collected.
        try:
            self.project_root = self._resolve_project_root()
            configured_allowed_paths = self._resolve_extra_allowed_paths()
        except Exception:
            self._detach_finalizer()
            raise
        default_allowed_paths = [self.project_root, Path(tempfile.gettempdir()).resolve()]

        self.allowed_paths: list[Path] = []
        for candidate in [*default_allowed_paths, *configured_allowed_paths]:
            if candidate not in self.allowed_paths:
                self.allowed_paths.append(candidate)

    def _resolve_project_root(self) -> Path:
        """The configured root, else a *detected* one — never a guessed fallback.

        ``ProjectService.detect_project_root()`` falls back to the working
        directory or ``$HOME`` when no marker is found, which is fine for a log
        directory but would silently make one of those the sandbox.
        """
        if self.config.project_root is not None:
            return Path(self.config.project_root).resolve()
        root = ProjectService.find_project_root()
        if root is None:
            raise ValueError(
                "SecureResource could not detect a project root: no marker "
                "(pyproject.toml, .git, .env, .agent) was found above the working "
                "directory or the calling module. Pass ResourceConfig(project_root=...) "
                "explicitly; the sandbox is never anchored to a guessed directory."
            )
        return root

    def _resolve_extra_allowed_paths(self) -> list[Path]:
        """Resolve ``config.extra_allowed_paths``, rejecting symlinked entries."""
        resolved_paths: list[Path] = []
        for raw in self.config.extra_allowed_paths:
            unresolved = self._anchored(raw)
            # Check for symlink BEFORE resolving — Path.resolve() follows symlinks
            # so is_symlink() on the result is always False.
            if unresolved.is_symlink():
                raise ValueError(
                    f"extra_allowed_path must not be a symlink — it could silently widen "
                    f"the sandbox boundary to an unintended location: {raw!r}"
                )
            resolved = unresolved.resolve()
            if not resolved.exists():
                warnings.warn(
                    f"extra_allowed_path does not exist and will have no effect: {raw!r}",
                    UserWarning,
                    stacklevel=3,
                )
            resolved_paths.append(resolved)
        return resolved_paths

    def _anchored(self, path: str | Path) -> Path:
        """*path* as given if absolute, else joined to ``project_root`` (not cwd, not ``~``)."""
        candidate = Path(path)
        return candidate if candidate.is_absolute() else self.project_root / candidate

    def _sandbox_root_for(self, resolved: Path) -> Path | None:
        """The allowed root that contains *resolved*, or None if none does."""
        # is_relative_to is component-wise, so '.../root_evil' is not under '.../root'.
        return next((root for root in self.allowed_paths if resolved.is_relative_to(root)), None)

    def _deny(
        self,
        log_message: str,
        log_args: tuple[Any, ...],
        error_message: str,
        cause: BaseException | None = None,
    ) -> NoReturn:
        # Targets go in as lazy %-args, not interpolated into the format
        # string: PIISecretFilter blanks any *message* that mentions a sensitive
        # keyword, and the most common probe target is literally "/etc/passwd" —
        # which would turn this alert into "[REDACTED SENSITIVE DATA]".
        if self.logger is not None:
            self.logger.error("SECURITY VIOLATION: " + log_message, *log_args)
        raise PermissionError("Access denied: " + error_message) from cause

    def get_secure_path(self, path: str | Path) -> Path:
        """
        Validates and resolves a path within the configured sandbox roots.

        This is a *check*: it resolves symlinks and verifies the result lies
        under an allowed root. Only the ``open_secure`` family also protects
        the open itself against a symlink planted after the check (TOCTOU);
        prefer those over ``open(get_secure_path(p))``.

        Args:
            path: The path to secure. A relative path is anchored to
                ``project_root``, never to the working directory; ``~`` is
                not expanded.

        Returns:
            Path: The resolved absolute Path object.

        Raises:
            PermissionError: If the path is outside the configured sandbox roots,
                or cannot be resolved at all (e.g. contains a NUL byte, hits a
                filesystem error, or fails a symlink lookup). Resolution failures
                are treated as untrusted input and denied rather than left to
                bubble up as a raw OSError/ValueError.
        """
        return self._secure_path_and_root(path)[0]

    def _secure_path_and_root(self, path: str | Path) -> tuple[Path, Path]:
        """``get_secure_path`` plus the sandbox root the result lies under."""
        try:
            resolved = self._anchored(path).resolve()
        except (OSError, ValueError) as exc:
            self._deny(
                "Path could not be resolved. Target: %r, Error: %s",
                (path, exc),
                f"Path {path!r} could not be resolved ({exc}).",
                cause=exc,
            )
        root = self._sandbox_root_for(resolved)
        if root is None:
            self._deny(
                "Path traversal attempt detected. Target: %s (resolved: %s), Allowed Roots: %s",
                (path, resolved, self.allowed_paths),
                f"Path {path} is outside the configured sandbox roots.",
            )
        return resolved, root

    def open_secure(self, path: str | Path, mode: str = "r", **kwargs: Any) -> IO[Any]:
        """
        Opens a file securely, enforcing sandbox constraints.

        The open is anchored to the file's sandbox root (see ``open_strategy``):
        with ``openat2`` the kernel refuses any resolution that leaves the
        root or crosses a symlink; with the ``walk`` the file is opened
        component by component with ``O_NOFOLLOW``. Either way a symlink
        planted after validation is refused (``PermissionError``) rather than
        followed.

        Args:
            path: The path to the file (relative paths anchor to ``project_root``).
            mode: The file mode.
            **kwargs: Arguments for the open() function.

        Returns:
            File handle.
        """
        secure_path, root = self._secure_path_and_root(path)
        try:
            return _OPENERS[self.open_strategy](root, secure_path, mode, **kwargs)
        except OSError as exc:
            if exc.errno not in _ESCAPE_ERRNOS and exc.errno != errno.EAGAIN:
                raise
            self._deny(
                "Validated path escaped the sandbox or crossed a symlink at open. "
                "Target: %s (resolved: %s, errno: %s)",
                (path, secure_path, errno.errorcode.get(exc.errno or 0, exc.errno)),
                f"Path {path} escapes the sandbox or changed to a symlink after validation.",
                cause=exc,
            )

    def write_text_secure(self, path: str | Path, content: str, encoding: str = "utf-8") -> None:
        """Writes text content to a file securely."""
        with self.open_secure(path, "w", encoding=encoding) as handle:
            handle.write(content)

    def read_text_secure(self, path: str | Path, encoding: str = "utf-8") -> str:
        """Reads text content from a file securely."""
        with self.open_secure(path, "r", encoding=encoding) as handle:
            return str(handle.read())
