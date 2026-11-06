from __future__ import annotations

import errno
import logging
import os
import stat

__all__ = ["SafeRotatingFileHandler"]
from logging.handlers import RotatingFileHandler
from typing import Any

# Owner read/write only. Used for both the initial open() and the fchmod()
# right after it, so a future edit to one can't silently drift from the
# other and loosen a security-relevant permission.
_SECURE_FILE_MODE = 0o600


# Must subclass RotatingFileHandler: RotatingFileHandler/FileHandler internals
# (doRollover, __init__) call self._open() polymorphically and expect the
# full Handler interface (baseFilename, mode, encoding, errors), so this
# can't be a plain function or @dataclass — logging dispatches by
# inheritance, not duck typing.
# spaghetti-ignore[lazy-class]: see above
class SafeRotatingFileHandler(RotatingFileHandler):
    """Rotating file handler that securely reopens log files on POSIX.

    Also stays closed: the stdlib ``FileHandler.emit`` re-opens the file when
    ``stream`` is ``None`` and the mode is append, so a record still in flight
    on the listener thread when ``Logger.close()`` ran would re-create the
    file and leak the descriptor. After ``close()`` records are dropped.
    """

    _released = False

    def close(self) -> None:
        self._released = True
        super().close()

    def emit(self, record: logging.LogRecord) -> None:
        if self._released:
            return
        super().emit(record)

    def _open(self) -> Any:
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            return super()._open()

        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
        try:
            fd = os.open(self.baseFilename, flags, _SECURE_FILE_MODE)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise ValueError(f"log_file must not be a symlink: {self.baseFilename}") from exc
            raise

        try:
            metadata = os.fstat(fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError(f"log_file must be a regular file: {self.baseFilename}")
            os.fchmod(fd, _SECURE_FILE_MODE)

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
