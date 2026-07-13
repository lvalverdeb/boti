"""
Pickle support for classes composed from bare ``LifecycleCore``.

``LifecycleCore`` deliberately carries no pickle-stripping logic (that lives
in ``boti.core.pickle_security.PickleSecurityMixin``, part of the heavier
``ManagedResource`` composition), so its locks/events/GC finalizer go
straight into ``pickle.dumps()`` and fail. Orchestrator classes that compose
bare ``LifecycleCore`` — no fsspec/pickle ownership of their own, e.g.
``boti_data.gateway.DataGateway``, ``boti_data.helper.DataHelper`` — but
wrap objects that *are* ``ManagedResource``-based already enforce the real
``allow_pickle``/``trusted_unpickle_scope`` security gate on those wrapped
objects. This mixin only strips and rebuilds the non-picklable
``LifecycleCore`` runtime state so pickling such an orchestrator (and thus
its wrapped, security-gated resources) isn't short-circuited by an unrelated
``TypeError`` on a bare lock object.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

__all__ = ["PicklableLifecycleCoreMixin"]


class PicklableLifecycleCoreMixin:
    """Strip/rebuild ``LifecycleCore``'s non-picklable runtime state."""

    def __getstate__(self) -> dict[str, Any]:
        state = self.__dict__.copy()
        state.pop("_state_lock", None)
        state.pop("_aclose_lock", None)
        state.pop("_closed_event", None)
        state.pop("_closing_thread", None)
        state.pop("_closing_task", None)
        state.pop("_finalizer", None)
        return state

    def __setstate__(self, state: dict[str, Any]) -> None:
        self.__dict__.update(state)
        self._state_lock = threading.RLock()
        self._aclose_lock = asyncio.Lock()
        self._closed_event = threading.Event()
        self._closing = False
        self._closing_thread: int | None = None
        self._closing_task: asyncio.Task[Any] | None = None
        self._attach_finalizer()  # type: ignore[attr-defined]
