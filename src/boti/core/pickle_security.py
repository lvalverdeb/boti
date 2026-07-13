"""
Pickle-security gating for Boti resources.

Provides ``PickleSecurityMixin``, a composable mixin that guards a
resource's pickling behind an explicit opt-in (``config.allow_pickle``) and
gates unpickling behind ``BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE`` or
``trusted_unpickle_scope()``, since deserializing an untrusted payload can
execute attacker-controlled code.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import pickle
import threading
import warnings
from typing import Any, ClassVar

from boti.core.models import ResourceConfig

__all__ = ["PickleSecurityMixin"]

# Module-level fallback for staticmethods with no instance logger available.
# Debug level only — this is an expected/best-effort probe.
_module_log = logging.getLogger(__name__)


class PickleSecurityMixin:
    """
    Pickle-security gating for resources.

    Requires ``config.allow_pickle`` to serialize, and requires
    ``BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE`` or ``trusted_unpickle_scope()``
    to deserialize, since unpickling an untrusted payload can execute
    attacker-controlled code via ``__reduce__``/``__setstate__``.
    """

    _TRUSTED_UNPICKLE_ENV = "BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE"
    # Thread-local storage for trusted_unpickle_scope so the scope is
    # confined to the calling thread and does not bleed into sibling threads.
    _thread_local: threading.local = threading.local()
    # Process-wide dedupe for the trusted-unpickle startup warning: worker
    # processes that enable the env var create many resources, and repeating
    # the same SECURITY message on every init drowns out real log content.
    _trusted_unpickle_warning_emitted: ClassVar[bool] = False

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
        if PickleSecurityMixin._trusted_unpickle_warning_emitted:
            return
        # Benign race: two concurrent first inits may both warn; that is acceptable.
        PickleSecurityMixin._trusted_unpickle_warning_emitted = True
        msg = (
            f"[SECURITY] {self.__class__.__name__}: "
            f"{self._TRUSTED_UNPICKLE_ENV} is ENABLED on this process. "
            "Trusted-unpickle mode allows ManagedResource deserialization. "
            "Ensure this process is only accessible from trusted internal workers — "
            "never expose it to public or untrusted networks."
        )
        warnings.warn(msg, RuntimeWarning, stacklevel=4)
        if self.logger is not None:  # type: ignore[attr-defined]
            with contextlib.suppress(Exception):
                self.logger.warning(msg)  # type: ignore[attr-defined]

    def __getstate__(self) -> dict[str, Any]:
        """Drop runtime-only state so subclasses remain pickleable."""
        if not self.config.allow_pickle:  # type: ignore[attr-defined]
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
        self._closing_thread: int | None = None
        self._closing_task: asyncio.Task[Any] | None = None
        self._configure_logger()  # type: ignore[attr-defined]
        self._restore_runtime_state()
        self._attach_finalizer()  # type: ignore[attr-defined]

    def _restore_runtime_state(self) -> None:
        """Hook for subclasses to rebuild transient runtime dependencies after unpickling."""
        pass
