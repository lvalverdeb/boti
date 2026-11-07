"""``openat2(2)`` binding: kernel-enforced sandboxed path resolution (Linux >= 5.6).

``openat2`` resolves a path relative to a directory descriptor with resolution
restrictions enforced *inside the kernel*, atomically, in one syscall::

    int openat2(int dirfd, const char *pathname, struct open_how *how, size_t size);

    struct open_how {            /* 24 bytes; extensible, hence the size argument */
        u64 flags;               /* O_* flags, as for open(2)                      */
        u64 mode;                /* file mode; must be 0 unless O_CREAT/O_TMPFILE  */
        u64 resolve;             /* RESOLVE_* restrictions                          */
    };

With ``RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS`` the
resolution may not leave *dirfd* (``..`` past it or an absolute path fails with
``EXDEV``), may not follow any symlink (``ELOOP``) and may not follow ``/proc``
magic links. That is exactly the guarantee ``secure_io``'s userspace component
walk provides; this module lets the kernel provide it instead where available.
The walk remains the fallback for older kernels, macOS and the BSDs.

No intra-package dependencies: this file is importable on every platform and
simply reports ``available() is False`` where the syscall does not exist.
"""

from __future__ import annotations

import errno
import functools
import os
import sys
from typing import Any

try:
    import ctypes
except ImportError:  # pragma: no cover - some embedded interpreters
    ctypes = None  # type: ignore[assignment]

__all__ = ["available", "open_beneath"]

# Identical on every 64-bit architecture in the unified syscall table
# (x86_64, aarch64, riscv64, s390x, ppc64le): openat2 was added after the tables
# were unified, so it never got a per-arch number.
SYS_OPENAT2 = 437
AT_FDCWD = -100

RESOLVE_NO_MAGICLINKS = 0x02
RESOLVE_NO_SYMLINKS = 0x04
RESOLVE_BENEATH = 0x08
_RESOLVE = RESOLVE_BENEATH | RESOLVE_NO_SYMLINKS | RESOLVE_NO_MAGICLINKS

_OPEN_HOW_SIZE = 24

if ctypes is not None:

    class _OpenHow(ctypes.Structure):
        _fields_ = [
            ("flags", ctypes.c_uint64),
            ("mode", ctypes.c_uint64),
            ("resolve", ctypes.c_uint64),
        ]

    assert ctypes.sizeof(_OpenHow) == _OPEN_HOW_SIZE


@functools.cache
def _syscall() -> Any:
    """libc's ``syscall(2)`` with explicit argtypes, or None off Linux."""
    if ctypes is None or sys.platform != "linux":
        return None
    try:
        fn = ctypes.CDLL(None, use_errno=True).syscall
    except (OSError, AttributeError):
        return None
    fn.restype = ctypes.c_long
    # syscall() is variadic; without explicit argtypes ctypes would pass
    # ``size`` as a C int, which is wrong for size_t on LP64.
    fn.argtypes = [ctypes.c_long, ctypes.c_int, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_size_t]
    return fn


def open_beneath(dir_fd: int, path: str | bytes, flags: int, mode: int = 0) -> int:
    """``openat2(dir_fd, path, {flags, mode, RESOLVE_BENEATH|NO_SYMLINKS|NO_MAGICLINKS})``.

    Returns the new descriptor or raises ``OSError`` with the kernel's errno:
    ``EXDEV`` for an escape attempt, ``ELOOP`` for a symlink anywhere on the
    path, ``EAGAIN`` if a concurrent rename made the result unsafe (the caller
    may retry), ``ENOSYS``/``EINVAL`` where the syscall is unsupported.
    """
    fn = _syscall()
    if fn is None:
        raise OSError(errno.ENOSYS, "openat2 is not available on this platform")
    # The kernel rejects a non-zero mode (EINVAL) unless the open can create.
    effective_mode = mode if flags & (os.O_CREAT | getattr(os, "O_TMPFILE", 0)) else 0
    how = _OpenHow(flags, effective_mode, _RESOLVE)
    fd = fn(SYS_OPENAT2, dir_fd, os.fsencode(path), ctypes.addressof(how), _OPEN_HOW_SIZE)
    if fd < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), os.fsdecode(path))
    return int(fd)


@functools.cache
def available() -> bool:
    """True once per process if a probe ``openat2`` of ``"."`` succeeds.

    Any failure — not Linux, kernel < 5.6 (``ENOSYS``), a seccomp profile
    that filters the syscall (``EPERM``), ``EINVAL`` from an unexpected
    struct layout — means "not available"; never an exception.
    """
    try:
        fd = open_beneath(AT_FDCWD, ".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError:
        return False
    os.close(fd)
    return True
