"""
Lifecycle management base for Boti resources.

Provides the ManagedResource abstract base class to standardize 
initialization, cleanup, and context management across the toolkit.
"""

from __future__ import annotations
import abc
import asyncio
import contextlib
import os
import pickle
import threading
import weakref
import warnings
from typing import Any, Callable, Optional, Self, final

__all__ = ["ManagedResource"]

import fsspec
from boti.core.logger import Logger
from boti.core.models import ResourceConfig
from boti.core.project import ProjectService


class ManagedResource(abc.ABC):
    """
    Base class for resources requiring standardized lifecycle management.
    
    Handles both synchronous and asynchronous cleanup, logging integration,
    thread-safe state management, and provides a consistent context manager interface.
    """

    _TRUSTED_UNPICKLE_ENV = "BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE"

    def __init__(
        self,
        config: Optional[ResourceConfig] = None,
        *,
        fs: Optional[fsspec.AbstractFileSystem] = None,
        fs_factory: Optional[Callable[[], fsspec.AbstractFileSystem]] = None,
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
        self._is_closed = False
        self._closing = False

        # Filesystem
        self.fs = fs
        self._fs_factory = fs_factory
        self._owns_fs = self._fs_factory is not None

        self._state_lock = threading.RLock()
        self._aclose_lock = asyncio.Lock()
        self._configure_logger()
        self._attach_finalizer()
        self._warn_if_trusted_unpickle_active()

    def _configure_logger(self) -> None:
        """Restore the configured logger or create a default one for runtime use."""
        if self.config.logger is None:
            log_base_dir = self.config.project_root or ProjectService.detect_project_root()
            self.logger = Logger.default_logger(
                logger_name=self.__class__.__name__,
                base_dir=log_base_dir,
            )
            level = Logger.DEBUG if self.debug else (Logger.INFO if self.verbose else Logger.WARNING)
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
            return False
        return True

    @classmethod
    @contextlib.contextmanager
    def trusted_unpickle_scope(cls) -> Any:
        """Temporarily enable ManagedResource unpickling for trusted runtimes."""
        previous = os.environ.get(cls._TRUSTED_UNPICKLE_ENV)
        os.environ[cls._TRUSTED_UNPICKLE_ENV] = "1"
        try:
            yield
        finally:
            if previous is None:
                os.environ.pop(cls._TRUSTED_UNPICKLE_ENV, None)
            else:
                os.environ[cls._TRUSTED_UNPICKLE_ENV] = previous

    @classmethod
    def _trusted_unpickle_enabled(cls) -> bool:
        value = os.environ.get(cls._TRUSTED_UNPICKLE_ENV, "")
        return value.lower() in {"1", "true", "yes"}

    def _warn_if_trusted_unpickle_active(self) -> None:
        """Emit a loud warning at resource initialization if the trusted-unpickle env var is set.

        This env var enables ManagedResource deserialization for distributed workflows.
        Accidentally enabling it in a public-facing service would allow RCE via a crafted
        pickle payload.  The warning makes the active mode visible in every log.
        """
        if self._trusted_unpickle_enabled():
            msg = (
                f"[SECURITY] {self.__class__.__name__}: "
                f"{self._TRUSTED_UNPICKLE_ENV} is ENABLED on this process. "
                "Trusted-unpickle mode allows ManagedResource deserialization. "
                "Ensure this process is only accessible from trusted internal workers — "
                "never expose it to public or untrusted networks."
            )
            warnings.warn(msg, RuntimeWarning, stacklevel=4)
            try:
                self.logger.warning(msg)
            except Exception:
                pass

    def __getstate__(self) -> dict[str, Any]:
        """Drop runtime-only state so subclasses remain pickleable."""
        if not self.config.allow_pickle:
            raise TypeError(
                f"Pickle serialization is disabled for {self.__class__.__name__}. "
                "Set allow_pickle=True only for trusted distributed workflows."
            )

        state = self.__dict__.copy()
        state.pop("_state_lock", None)
        state.pop("_aclose_lock", None)
        state.pop("_finalizer", None)
        state.pop("logger", None)

        if self._is_pickleable_state(state):
            return state

        config = state.get("config")
        if isinstance(config, ResourceConfig) and config.logger is not None:
            state["config"] = config.model_copy(update={"logger": None})
            if self._is_pickleable_state(state):
                return state

        if state.get("_owns_fs"):
            state["fs"] = None
        elif state.get("fs") is not None:
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
        self._aclose_lock = asyncio.Lock()
        self._closing = False
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
            # Never raise during interpreter shutdown or GC
            pass

    @property
    def closed(self) -> bool:
        """Determines if the resource has been cleaned up."""
        with self._state_lock:
            return self._is_closed

    def _cleanup(self) -> None:
        """Synchronous cleanup hook for subclasses. Override to release non-async resources."""
        pass

    async def _acleanup(self) -> None:
        """Asynchronous cleanup hook for subclasses. Override to release async resources."""
        pass

    def _assert_open(self) -> None:
        """Raises RuntimeError if the resource is closed."""
        with self._state_lock:
            if self._is_closed or self._closing:
                raise RuntimeError(f"{self.__class__.__name__} is closed")

    def _ensure_fs(self) -> Optional[fsspec.AbstractFileSystem]:
        """Lazy-loads the filesystem if a factory is provided."""
        with self._state_lock:
            self._assert_open()
            if self.fs is not None:
                return self.fs
            if self._fs_factory is None:
                return None
            
            fs_new = self._fs_factory()
            if not isinstance(fs_new, fsspec.AbstractFileSystem):
                raise TypeError(f"fs_factory() must return fsspec.AbstractFileSystem, got {type(fs_new)!r}")
            self.fs = fs_new
            return self.fs

    def require_fs(self) -> fsspec.AbstractFileSystem:
        """Ensures a filesystem is available or raises RuntimeError."""
        with self._state_lock:
            if self._is_closed or self._closing:
                raise RuntimeError(f"{self.__class__.__name__} is closed")
        fs = self._ensure_fs()
        if fs is None:
            raise RuntimeError(f"{self.__class__.__name__}: filesystem is required but not configured")
        return fs

    def _detach_finalizer(self) -> None:
        """Safely detaches the GC finalizer to prevent duplicate warnings."""
        finalizer = getattr(self, "_finalizer", None)
        if finalizer is not None and finalizer.alive:
            try:
                finalizer.detach()
            except Exception:
                pass

    @final
    def close(self, *, suppress_errors: bool = False) -> None:
        """Synchronously release managed resources.

        Thread-safe and idempotent: concurrent or repeated calls are silently
        ignored after the first caller sets ``_closing``.  The state lock is
        held only for the brief state-transition checks; the actual cleanup
        runs outside the lock so subclass hooks can freely call other methods.
        """
        with self._state_lock:
            if self._is_closed or self._closing:
                return
            self._closing = True
        try:
            self._cleanup()
        except Exception:
            self.logger.error(f"Error during {self.__class__.__name__}._cleanup()", exc_info=self.debug)
            if not suppress_errors:
                raise
        finally:
            with self._state_lock:
                self._is_closed = True
                self._closing = False
            self._detach_finalizer()

    async def aclose(self, *, suppress_errors: bool = False) -> None:
        """Asynchronously release managed resources.

        The async lock serialises concurrent async closes within the same event
        loop.  ``_closing`` guards against a simultaneous sync ``close()`` from
        another thread.  ``_detach_finalizer`` is called inside the ``finally``
        block so it runs even when cleanup raises.
        """
        async with self._aclose_lock:
            with self._state_lock:
                if self._is_closed or self._closing:
                    return
                self._closing = True

            try:
                if type(self)._acleanup is not ManagedResource._acleanup:
                    await self._acleanup()
                else:
                    await asyncio.to_thread(self._cleanup)
            except Exception:
                self.logger.error(f"Error during {self.__class__.__name__}._acleanup()", exc_info=self.debug)
                if not suppress_errors:
                    raise
            finally:
                with self._state_lock:
                    self._is_closed = True
                    self._closing = False
                self._detach_finalizer()

    @final
    def __enter__(self) -> Self:
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
