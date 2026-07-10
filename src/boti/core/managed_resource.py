"""
Lifecycle management base for Boti resources.

Provides the ManagedResource abstract base class to standardize 
initialization, cleanup, and context management across the toolkit.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import pickle
import threading
import warnings
import weakref
from collections.abc import Callable
from typing import Any, ClassVar, Self, final

__all__ = ["ManagedResource"]

import fsspec

from boti.core.logger import Logger
from boti.core.models import ResourceConfig
from boti.core.project import ProjectService

# Module-level fallback for staticmethods with no instance logger available.
# Debug level only — this is an expected/best-effort probe.
_module_log = logging.getLogger(__name__)


class ManagedResource:
    """
    Lifecycle management base class for resources.
    
    Handles both synchronous and asynchronous cleanup, logging integration,
    thread-safe state management, and provides a consistent context manager interface.
    """

    _TRUSTED_UNPICKLE_ENV = "BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE"
    # Thread-local storage for trusted_unpickle_scope so the scope is
    # confined to the calling thread and does not bleed into sibling threads.
    _thread_local: threading.local = threading.local()
    # Process-wide dedupe for the trusted-unpickle startup warning: worker
    # processes that enable the env var create many resources, and repeating
    # the same SECURITY message on every init drowns out real log content.
    _trusted_unpickle_warning_emitted: ClassVar[bool] = False

    def __init__(
        self,
        config: ResourceConfig | None = None,
        *,
        fs: fsspec.AbstractFileSystem | None = None,
        fs_factory: Callable[[], fsspec.AbstractFileSystem] | None = None,
        **config_overrides: Any,
    ) -> None:
        if config is None:
            config = ResourceConfig(**config_overrides)
        elif config_overrides:
            unexpected_keys = ", ".join(sorted(config_overrides))
            raise TypeError(
                f"Unexpected config override(s) for {self.__class__.__name__}: {unexpected_keys}"
            )
        
        self.config = config
        self.verbose = config.verbose
        self.debug = config.debug
        self._skip_logger = config.skip_logger
        self._is_closed = False
        self._closing = False

        # Filesystem
        self.fs = fs
        self._fs_factory = fs_factory
        self._owns_fs = self._fs_factory is not None

        self._state_lock = threading.RLock()
        # Single-flights fs_factory() calls without blocking _state_lock readers,
        # so a slow remote connect cannot stall closed/close() in other threads.
        self._fs_init_lock = threading.Lock()
        self._aclose_lock = asyncio.Lock()
        # Set once cleanup has fully finished; concurrent close() callers wait
        # on it so close() acts as a barrier rather than returning early.
        self._closed_event = threading.Event()
        self._closing_thread: int | None = None
        # Task identity of the aclose() closer: a reentrant aclose() from
        # within _acleanup runs in the same task, while a sibling task on the
        # same event loop (same OS thread) must still hit the barrier.
        self._closing_task: asyncio.Task[Any] | None = None
        self._configure_logger()
        self._attach_finalizer()
        self._warn_if_trusted_unpickle_active()

    def _configure_logger(self) -> None:
        """Restore the configured logger or create a default one for runtime use."""
        if self._skip_logger:
            self.logger = None
            return
        if self.config.logger is None:
            log_base_dir = self.config.project_root or ProjectService.detect_project_root()
            self.logger = Logger.default_logger(
                logger_name=self.__class__.__name__,
                base_dir=log_base_dir,
            )
            level = (
                Logger.DEBUG if self.debug
                else (Logger.INFO if self.verbose else Logger.WARNING)
            )
            self.logger.set_level(level)
        else:
            self.logger = self.config.logger

    def _attach_finalizer(self) -> None:
        """Install leak warnings only for live resources."""
        if self._is_closed:
            self._finalizer = None
            return
        self._finalizer = weakref.finalize(
            self,
            ManagedResource._finalize_callback,
            self.logger,
            self.__class__.__name__
        )

    @staticmethod
    def _is_pickleable_state(value: Any) -> bool:
        try:
            pickle.dumps(value)
        except Exception:
            _module_log.debug("pickle.dumps() failed in _is_pickleable_state", exc_info=True)
            return False
        return True

    @classmethod
    @contextlib.contextmanager
    def trusted_unpickle_scope(cls) -> Any:
        """Temporarily enable ManagedResource unpickling for the current thread only.

        Uses thread-local state so that concurrent threads are not affected.
        The process-wide environment variable ``BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE``
        remains supported for worker processes that enable it at startup, but
        ``trusted_unpickle_scope`` no longer mutates ``os.environ``.
        """
        previous = getattr(cls._thread_local, "trusted_unpickle", False)
        cls._thread_local.trusted_unpickle = True
        try:
            yield
        finally:
            cls._thread_local.trusted_unpickle = previous

    @classmethod
    def _trusted_unpickle_enabled(cls) -> bool:
        # Thread-local scope takes precedence; fall back to the process-wide env var
        # for worker processes that set it globally at startup.
        if getattr(cls._thread_local, "trusted_unpickle", False):
            return True
        value = os.environ.get(cls._TRUSTED_UNPICKLE_ENV, "")
        return value.lower() in {"1", "true", "yes"}

    def _warn_if_trusted_unpickle_active(self) -> None:
        """Emit a loud warning at resource initialization if the trusted-unpickle env var is set.

        This env var enables ManagedResource deserialization for distributed workflows.
        Accidentally enabling it in a public-facing service would allow RCE via a crafted
        pickle payload.  The warning is emitted once per process: worker processes that
        enable the mode at startup create many resources, and repeating it on every init
        would bury real log content.
        """
        if not self._trusted_unpickle_enabled():
            return
        if ManagedResource._trusted_unpickle_warning_emitted:
            return
        # Benign race: two concurrent first inits may both warn; that is acceptable.
        ManagedResource._trusted_unpickle_warning_emitted = True
        msg = (
            f"[SECURITY] {self.__class__.__name__}: "
            f"{self._TRUSTED_UNPICKLE_ENV} is ENABLED on this process. "
            "Trusted-unpickle mode allows ManagedResource deserialization. "
            "Ensure this process is only accessible from trusted internal workers — "
            "never expose it to public or untrusted networks."
        )
        warnings.warn(msg, RuntimeWarning, stacklevel=4)
        if self.logger is not None:
            with contextlib.suppress(Exception):
                self.logger.warning(msg)

    def __getstate__(self) -> dict[str, Any]:
        """Drop runtime-only state so subclasses remain pickleable."""
        if not self.config.allow_pickle:
            raise TypeError(
                f"Pickle serialization is disabled for {self.__class__.__name__}. "
                "Set allow_pickle=True only for trusted distributed workflows."
            )

        try:
            state = self.__dict__.copy()
        except AttributeError:
            raise TypeError(
                f"Cannot pickle {self.__class__.__name__}: subclasses using __slots__ "
                "must override __getstate__/__setstate__."
            ) from None
        state.pop("_state_lock", None)
        state.pop("_fs_init_lock", None)
        state.pop("_aclose_lock", None)
        state.pop("_closed_event", None)
        state.pop("_closing_thread", None)
        state.pop("_closing_task", None)
        state.pop("_finalizer", None)
        state.pop("logger", None)

        if self._is_pickleable_state(state):
            return state

        config = state.get("config")
        if isinstance(config, ResourceConfig) and config.logger is not None:
            state["config"] = config.model_copy(update={"logger": None})
            if self._is_pickleable_state(state):
                return state

        if state.get("_owns_fs") or state.get("fs") is not None:
            state["fs"] = None
        if self._is_pickleable_state(state):
            return state

        if state.get("_fs_factory") is not None:
            state["_fs_factory"] = None
            state["_owns_fs"] = False

        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        """Rebuild transient runtime state after unpickling."""
        config = state.get("config")
        if not isinstance(config, ResourceConfig) or not config.allow_pickle:
            raise pickle.UnpicklingError(
                "ManagedResource pickle payloads must opt into trusted serialization."
            )
        if not self._trusted_unpickle_enabled():
            raise pickle.UnpicklingError(
                "ManagedResource unpickling is disabled by default. "
                f"Set {self._TRUSTED_UNPICKLE_ENV}=1 only in trusted runtimes."
            )

        self.__dict__.update(state)
        self._state_lock = threading.RLock()
        self._fs_init_lock = threading.Lock()
        self._aclose_lock = asyncio.Lock()
        self._closed_event = threading.Event()
        self._closing = False
        self._closing_thread = None
        self._closing_task = None
        self._configure_logger()
        self._restore_runtime_state()
        self._attach_finalizer()

    def _restore_runtime_state(self) -> None:
        """Hook for subclasses to rebuild transient runtime dependencies after unpickling."""
        pass

    @staticmethod
    def _finalize_callback(logger: Any, class_name: str) -> None:
        """Called by weakref.finalize when object is GC'd without explicit close."""
        try:
            if logger is not None:
                logger.warning(
                    f"{class_name} was garbage collected without being closed. "
                    f"Resources may have leaked. Please use context managers or call .close()."
                )
            else:
                # Fallback to standard warning system if logger is already torn down
                warnings.warn(
                    f"{class_name} was garbage collected without being closed.",
                    ResourceWarning,
                    stacklevel=2
                )
        except Exception:
            _module_log.debug("Exception in _finalize_callback (harmless during shutdown)")
            # Never raise during interpreter shutdown or GC

    @property
    def closed(self) -> bool:
        """Determines if the resource has been cleaned up."""
        with self._state_lock:
            return self._is_closed

    def _cleanup(self) -> None:
        """Synchronous cleanup hook for subclasses. Override to release non-async resources."""
        pass

    def _release_fs(self) -> None:
        """Drop the owned filesystem reference.

        Runs in close()/aclose() after _cleanup(), with _state_lock held so the
        release is atomic with the _is_closed transition.
        """
        if self._owns_fs:
            self.fs = None

    async def _acleanup(self) -> None:
        """Asynchronous cleanup hook for subclasses. Override to release async resources."""
        pass

    def _assert_open(self) -> None:
        """Raises RuntimeError if the resource is closed."""
        with self._state_lock:
            if self._is_closed or self._closing:
                raise RuntimeError(f"{self.__class__.__name__} is closed")

    def _ensure_fs(self) -> fsspec.AbstractFileSystem | None:
        """Lazy-loads the filesystem if a factory is provided.

        The factory runs *outside* ``_state_lock`` so a slow remote connect
        (potentially seconds of retries) cannot block ``closed``/``close()``
        in other threads.  ``_fs_init_lock`` keeps factory calls single-flight.
        """
        with self._state_lock:
            self._assert_open()
            if self.fs is not None:
                return self.fs
            if self._fs_factory is None:
                return None
            factory = self._fs_factory

        with self._fs_init_lock:
            with self._state_lock:
                self._assert_open()
                if self.fs is not None:
                    return self.fs

            fs_new = factory()
            if not isinstance(fs_new, fsspec.AbstractFileSystem):
                raise TypeError(
                    f"fs_factory() must return fsspec.AbstractFileSystem, "
                    f"got {type(fs_new)!r}"
                )
            with self._state_lock:
                # The resource may have been closed while the factory ran;
                # never publish a filesystem onto a closed resource.
                self._assert_open()
                self.fs = fs_new
                return self.fs

    def require_fs(self) -> fsspec.AbstractFileSystem:
        """Ensures a filesystem is available or raises RuntimeError."""
        with self._state_lock:
            if self._is_closed or self._closing:
                raise RuntimeError(f"{self.__class__.__name__} is closed")
        fs = self._ensure_fs()
        if fs is None:
            raise RuntimeError(
                f"{self.__class__.__name__}: "
                "filesystem is required but not configured"
            )
        return fs

    def _detach_finalizer(self) -> None:
        """Safely detaches the GC finalizer to prevent duplicate warnings."""
        finalizer = getattr(self, "_finalizer", None)
        if finalizer is not None and finalizer.alive:
            with contextlib.suppress(Exception):
                finalizer.detach()

    @final
    def close(self, *, suppress_errors: bool = False) -> None:
        """Synchronously release managed resources.

        Thread-safe and idempotent.  The first caller runs ``_cleanup()``;
        concurrent callers from other threads block until that cleanup has
        finished, so ``close()`` acts as a barrier — when it returns, cleanup
        is done.  A reentrant call from within ``_cleanup()`` (same thread)
        returns immediately instead of deadlocking.  The state lock is held
        only for the brief state-transition checks; the actual cleanup runs
        outside the lock so subclass hooks can freely call other methods.
        """
        with self._state_lock:
            if self._is_closed:
                return
            if self._closing:
                if self._closing_thread == threading.get_ident():
                    return
                waiter = self._closed_event
            else:
                self._closing = True
                self._closing_thread = threading.get_ident()
                waiter = None
        if waiter is not None:
            waiter.wait()
            return
        try:
            self._cleanup()
        except Exception:
            if self.logger is not None:
                self.logger.error(
                    f"Error during {self.__class__.__name__}._cleanup()",
                    exc_info=self.debug,
                )
            if not suppress_errors:
                raise
        finally:
            with self._state_lock:
                self._is_closed = True
                self._closing = False
                self._closing_thread = None
                self._release_fs()
            self._closed_event.set()
            self._detach_finalizer()

    async def aclose(self, *, suppress_errors: bool = False) -> None:
        """Asynchronously release managed resources.

        The async lock serialises concurrent async closes within the same event
        loop.  A reentrant ``aclose()`` from within ``_acleanup()`` (same task)
        returns before touching the non-reentrant async lock instead of
        deadlocking on it; sibling tasks still queue on the lock and observe
        the barrier.  If a sync ``close()`` from another thread is already in
        flight, this call waits off-loop for it to finish, mirroring the
        barrier semantics of ``close()``.  ``_detach_finalizer`` is called
        inside the ``finally`` block so it runs even when cleanup raises.
        """
        with self._state_lock:
            if self._is_closed:
                return
            if (
                self._closing
                and self._closing_task is not None
                and self._closing_task is asyncio.current_task()
            ):
                # Reentrant aclose() from within a cleanup hook: _aclose_lock
                # is already held by this task and asyncio locks are not
                # reentrant, so acquiring it again would deadlock.
                return

        async with self._aclose_lock:
            with self._state_lock:
                if self._is_closed:
                    return
                if self._closing:
                    if self._closing_thread == threading.get_ident():
                        return
                    waiter = self._closed_event
                else:
                    self._closing = True
                    self._closing_thread = threading.get_ident()
                    self._closing_task = asyncio.current_task()
                    waiter = None
            if waiter is not None:
                await asyncio.to_thread(waiter.wait)
                return

            try:
                if type(self)._acleanup is not ManagedResource._acleanup:
                    await self._acleanup()
                else:
                    await asyncio.to_thread(self._cleanup_adopting_closer_thread)
            except Exception:
                if self.logger is not None:
                    self.logger.error(
                        f"Error during {self.__class__.__name__}._acleanup()",
                        exc_info=self.debug,
                    )
                if not suppress_errors:
                    raise
            finally:
                with self._state_lock:
                    self._is_closed = True
                    self._closing = False
                    self._closing_thread = None
                    self._closing_task = None
                    self._release_fs()
                self._closed_event.set()
                self._detach_finalizer()

    def _cleanup_adopting_closer_thread(self) -> None:
        """Run ``_cleanup()`` in a ``to_thread`` worker, adopting closer identity.

        Without this, a reentrant ``close()`` from inside the hook would not
        match ``_closing_thread`` and would deadlock waiting on its own barrier.
        """
        with self._state_lock:
            self._closing_thread = threading.get_ident()
        self._cleanup()

    def __enter__(self) -> Self:
        """Enter the resource context.

        Subclasses may override to return a different object (e.g. a session
        yielding its client) but must call ``_assert_open()`` first.
        ``__exit__`` remains final, so ``close()`` always runs regardless of
        what ``__enter__`` returned.
        """
        self._assert_open()
        return self

    @final
    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        # Suppress cleanup errors only when an exception is already propagating,
        # so the original exception is not replaced by a cleanup failure.
        self.close(suppress_errors=exc_type is not None)

    async def __aenter__(self) -> Self:
        self._assert_open()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        # Same rationale as __exit__: preserve the original exception.
        await self.aclose(suppress_errors=exc_type is not None)
