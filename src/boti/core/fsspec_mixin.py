"""
Lazy, thread-safe fsspec filesystem access.

Provides ``FsspecMixin``, a composable mixin that owns a resource's
``fs``/``fs_factory`` state and lazy, single-flight filesystem initialization.
Expects to be composed with something providing the ``LifecycleCore``
interface (``_state_lock``, ``_assert_open()``, ``_release_transient_state()``
hook) since filesystem release is wired into the close()/aclose() barrier.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

import fsspec

__all__ = ["FsspecMixin"]


class FsspecMixin:
    """
    Lazy, thread-safe fsspec filesystem access.

    Handles a resource's ``fs``/``fs_factory`` state and lazy, single-flight
    filesystem initialization, and releases the owned filesystem as part of
    the composed ``close()``/``aclose()`` barrier.
    """

    def __init__(
        self,
        *,
        fs: fsspec.AbstractFileSystem | None = None,
        fs_factory: Callable[[], fsspec.AbstractFileSystem] | None = None,
    ) -> None:
        self.fs = fs
        self._fs_factory = fs_factory
        self._owns_fs = self._fs_factory is not None
        # Single-flights fs_factory() calls without blocking _state_lock readers,
        # so a slow remote connect cannot stall closed/close() in other threads.
        self._fs_init_lock = threading.Lock()
        super().__init__()

    def _release_transient_state(self) -> None:
        """Drop the owned filesystem reference.

        Runs in close()/aclose() after _cleanup(), with _state_lock held so the
        release is atomic with the _is_closed transition.
        """
        if self._owns_fs:
            self.fs = None
        super()._release_transient_state()  # type: ignore[misc]

    def _ensure_fs(self) -> fsspec.AbstractFileSystem | None:
        """Lazy-loads the filesystem if a factory is provided.

        The factory runs *outside* ``_state_lock`` so a slow remote connect
        (potentially seconds of retries) cannot block ``closed``/``close()``
        in other threads.  ``_fs_init_lock`` keeps factory calls single-flight.
        """
        with self._state_lock:  # type: ignore[attr-defined]
            self._assert_open()  # type: ignore[attr-defined]
            if self.fs is not None:
                return self.fs
            if self._fs_factory is None:
                return None
            factory = self._fs_factory

        with self._fs_init_lock:
            with self._state_lock:  # type: ignore[attr-defined]
                self._assert_open()  # type: ignore[attr-defined]
                if self.fs is not None:
                    return self.fs

            fs_new = factory()
            if not isinstance(fs_new, fsspec.AbstractFileSystem):
                raise TypeError(
                    f"fs_factory() must return fsspec.AbstractFileSystem, got {type(fs_new)!r}"
                )
            with self._state_lock:  # type: ignore[attr-defined]
                # The resource may have been closed while the factory ran;
                # never publish a filesystem onto a closed resource.
                self._assert_open()  # type: ignore[attr-defined]
                self.fs = fs_new
                return self.fs

    def require_fs(self) -> fsspec.AbstractFileSystem:
        """Ensures a filesystem is available or raises RuntimeError."""
        with self._state_lock:  # type: ignore[attr-defined]
            if self._is_closed or self._closing:  # type: ignore[attr-defined]
                raise RuntimeError(f"{self.__class__.__name__} is closed")
        fs = self._ensure_fs()
        if fs is None:
            raise RuntimeError(
                f"{self.__class__.__name__}: filesystem is required but not configured"
            )
        return fs
