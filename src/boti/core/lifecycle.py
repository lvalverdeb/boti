"""
Sync/async close barrier and context-manager protocol.

Provides ``LifecycleCore``, a composable base that gives any resource-like
object a thread-safe, idempotent close()/aclose() barrier, a context-manager
protocol, and a GC leak-warning finalizer. Carries no filesystem or pickle
concerns so it can back lightweight objects (e.g. an AI Agent base class)
that only need the lifecycle guarantees.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import warnings
import weakref
from typing import Any, Self, final

__all__ = ["LifecycleCore"]

# Module-level fallback for staticmethods with no instance logger available.
# Debug level only — this is an expected/best-effort probe.
_module_log = logging.getLogger(__name__)


class LifecycleCore:
    """
    Sync/async close barrier and context-manager protocol.

    Handles both synchronous and asynchronous cleanup, thread-safe state
    management, and provides a consistent context manager interface.
    Subclasses/mixins that own transient runtime state (e.g. a filesystem
    handle) should override ``_release_transient_state()`` rather than
    touching ``close()``/``aclose()`` directly.
    """

    # Composers that want cleanup errors logged should set a real logger and
    # debug flag; these safe defaults let LifecycleCore stand alone otherwise.
    logger: Any = None
    debug: bool = False

    def __init__(self) -> None:
        self._is_closed = False
        self._closing = False

        self._state_lock = threading.RLock()
        self._aclose_lock = asyncio.Lock()
        # Set once cleanup has fully finished; concurrent close() callers wait
        # on it so close() acts as a barrier rather than returning early.
        self._closed_event = threading.Event()
        self._closing_thread: int | None = None
        # Task identity of the aclose() closer: a reentrant aclose() from
        # within _acleanup runs in the same task, while a sibling task on the
        # same event loop (same OS thread) must still hit the barrier.
        self._closing_task: asyncio.Task[Any] | None = None
        self._attach_finalizer()

    def _attach_finalizer(self) -> None:
        """Install leak warnings only for live resources."""
        if self._is_closed:
            self._finalizer = None
            return
        self._finalizer = weakref.finalize(
            self, LifecycleCore._finalize_callback, self.logger, self.__class__.__name__
        )

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
                    stacklevel=2,
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
        """Synchronous cleanup hook for subclasses. Override to release non-async resources.

        Leaf/concrete subclasses only: an override here, however trivial,
        replaces this no-op for every subclass beneath it — a base or mixin
        class in the middle of a hierarchy that redefines this "to document
        the hook" silently disables cleanup for any subclass that doesn't
        re-override it again itself. Only define this where you have actual
        resources to release.
        """
        pass

    async def _acleanup(self) -> None:
        """Asynchronous cleanup hook for subclasses. Override to release async resources.

        The default implementation runs the synchronous ``_cleanup()`` hook in
        a ``to_thread`` worker, adopting closer identity so a reentrant
        ``close()`` from within the hook is recognised instead of deadlocking.
        A subclass that overrides this replaces that fallback entirely — it is
        responsible for calling ``_cleanup()`` itself if it still wants it run.

        Leaf/concrete subclasses only, same as ``_cleanup()``: Python's
        override semantics are unconditional, so a pass-through stub anywhere
        above the leaf class (even one that looks like harmless boilerplate)
        silently skips both this method's default to-thread fallback and any
        ``_cleanup()`` a further subclass defines. This is what caused a real
        bug in ``Agent`` (see ``boti/tests/core/test_agent.py``): it isn't
        preventable by restructuring this method, only by not redefining it
        above the leaf class.
        """
        await asyncio.to_thread(self._cleanup_adopting_closer_thread)

    def _release_transient_state(self) -> None:
        """Hook for mixins to drop transient runtime state after cleanup.

        Runs in close()/aclose() after _cleanup()/_acleanup(), with
        _state_lock held so the release is atomic with the _is_closed
        transition. No-op here; e.g. a filesystem mixin would override this
        to drop an owned filesystem reference.
        """
        pass

    def _assert_open(self) -> None:
        """Raises RuntimeError if the resource is closed."""
        with self._state_lock:
            if self._is_closed or self._closing:
                raise RuntimeError(f"{self.__class__.__name__} is closed")

    def _detach_finalizer(self) -> None:
        """Safely detaches the GC finalizer to prevent duplicate warnings."""
        finalizer = getattr(self, "_finalizer", None)
        if finalizer is not None and finalizer.alive:
            with contextlib.suppress(Exception):
                finalizer.detach()

    def _same_thread_close_is_reentrant(self) -> bool:
        """Whether a close() on the closer's own thread is genuinely reentrant.

        Thread identity alone cannot tell a reentrant call (from inside the
        closer's own cleanup) apart from a sibling task or loop callback that
        merely shares the closer's OS thread: an async closer suspended at an
        await leaves its event-loop thread free to run other code under the
        same thread id.  Disambiguate with task identity.  Must be called with
        ``_state_lock`` held.
        """
        if self._closing_task is None:
            # Closer is a sync close(); same thread means we're inside its
            # _cleanup() call stack.
            return True
        try:
            current = asyncio.current_task()
        except RuntimeError:
            # No running loop on this thread: we are the to_thread worker that
            # adopted closer identity in _cleanup_adopting_closer_thread().
            return True
        return current is self._closing_task

    def _raise_if_barrier_wait_would_deadlock(self) -> None:
        """Refuse to block on the close barrier when doing so can never finish.

        If the in-flight closer is an async task whose event loop runs on
        *this* thread, its cleanup only completes once this thread returns to
        the loop — which a blocking ``waiter.wait()`` here would prevent
        forever.  Must be called with ``_state_lock`` held.
        """
        closing_task = self._closing_task
        if closing_task is None:
            return
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if closing_task.get_loop() is running_loop:
            raise RuntimeError(
                f"{self.__class__.__name__}.close() called on an event-loop thread "
                "while aclose() is in progress on the same loop — blocking on the "
                "close barrier here would deadlock the loop; use 'await aclose()' "
                "instead."
            )

    @final
    def close(self, *, suppress_errors: bool = False) -> None:
        """Synchronously release managed resources.

        Thread-safe and idempotent.  The first caller runs ``_cleanup()``;
        concurrent callers from other threads block until that cleanup has
        finished, so ``close()`` is a barrier — when it returns, cleanup is
        done.  A reentrant call from within the closer's own cleanup returns
        immediately.  The state lock guards only the brief state transitions;
        cleanup runs outside it so hooks can freely call other methods.

        Raises RuntimeError if called on an event-loop thread while aclose()
        is in flight on that loop (except reentrantly from the closer's own
        cleanup): waiting would deadlock the loop — ``await aclose()`` instead.
        """
        with self._state_lock:
            if self._is_closed:
                return
            if self._closing:
                if (
                    self._closing_thread == threading.get_ident()
                    and self._same_thread_close_is_reentrant()
                ):
                    return
                self._raise_if_barrier_wait_would_deadlock()
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
                self._release_transient_state()
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
                await self._acleanup()
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
                    self._release_transient_state()
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
