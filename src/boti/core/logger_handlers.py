from __future__ import annotations

import errno
import os
import stat

__all__ = ["SafeRotatingFileHandler"]
from logging.handlers import RotatingFileHandler
from typing import Any


class SafeRotatingFileHandler(RotatingFileHandler):
    """Rotating file handler that securely reopens log files on POSIX."""

    def _open(self) -> Any:
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            return super()._open()

        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
        try:
            fd = os.open(self.baseFilename, flags, 0o600)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError(f"log_file must not be a symlink: {self.baseFilename}") from exc
            raise

        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"log_file must be a regular file: {self.baseFilename}")
            os.fchmod(fd, 0o600)

            if "b" in self.mode:
                return os.fdopen(fd, self.mode)
            return os.fdopen(
                fd,
                self.mode,
                encoding=self.encoding,
                errors=self.errors,
            )
        except Exception:
            os.close(fd)
            raise
