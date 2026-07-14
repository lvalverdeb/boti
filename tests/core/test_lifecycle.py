"""
Tests for ManagedResource lifecycle (sync and async).
"""
import asyncio
import gc
import pickle
import threading
import warnings
from types import SimpleNamespace

import fsspec
import pytest
from pydantic import ValidationError

from boti.core import ManagedResource
from boti.core import project as project_module
from boti.core.models import ResourceConfig
from boti.core.pickle_security import PickleSecurityMixin


class SimpleResource(ManagedResource):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cleaned_up_sync = False
        self.cleaned_up_async = False

    def _cleanup(self):
        self.cleaned_up_sync = True

    async def _acleanup(self):
        self.cleaned_up_async = True


def test_managed_resource_sync_context():
    """Verify synchronous context manager lifecycle."""
    res = SimpleResource()
    with res as r:
        assert not r.closed
        assert not r.cleaned_up_sync

    assert res.closed
    assert res.cleaned_up_sync


@pytest.mark.asyncio
async def test_managed_resource_async_context():
    """Verify asynchronous context manager lifecycle."""
    res = SimpleResource()
    async with res as r:
        assert not r.closed
        assert not r.cleaned_up_async

    assert res.closed
    assert res.cleaned_up_async


@pytest.mark.asyncio
async def test_managed_resource_aclose_fallback():
    """Verify that aclose falls back to sync cleanup if async is not overridden."""
    class SyncOnlyResource(ManagedResource):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.cleaned_up = False
        def _cleanup(self):
            self.cleaned_up = True

    res = SyncOnlyResource()
    await res.aclose()
    assert res.closed
    assert res.cleaned_up


def test_managed_resource_close_idempotency():
    """Verify that calling close multiple times is safe."""
    res = SimpleResource()
    res.close()
    assert res.closed
    res.close() # Should not raise
    assert res.closed


def test_managed_resource_supports_runtime_fs_factory():
    """Verify runtime-only filesystem dependencies remain constructor-injected."""
    res = SimpleResource(fs_factory=lambda: fsspec.filesystem("memory"))
    try:
        fs = res.require_fs()
        assert fs.protocol == "memory"
    finally:
        res.close()


def test_resource_config_rejects_unknown_fields():
    """Verify ResourceConfig fails fast on unexpected config input."""
    with pytest.raises(ValidationError):
        ResourceConfig(unexpected_setting=True)


def test_managed_resource_rejects_config_overrides_when_config_is_supplied():
    """Verify validated config and ad-hoc overrides cannot be mixed silently."""
    config = ResourceConfig()

    with pytest.raises(TypeError, match="Unexpected config override"):
        SimpleResource(config=config, verbose=True)


def test_managed_resource_instances_are_pickleable():
    """Verify ManagedResource subclasses can round-trip through pickle."""
    res = SimpleResource(config=ResourceConfig(allow_pickle=True))
    restored = None
    try:
        with ManagedResource.trusted_unpickle_scope():
            restored = pickle.loads(pickle.dumps(res))
        assert isinstance(restored, SimpleResource)
        assert not restored.closed
        assert restored.cleaned_up_sync is False
        restored.close()
        assert restored.cleaned_up_sync is True
    finally:
        res.close()
        if restored is not None and not restored.closed:
            restored.close()


def test_is_pickleable_state_logs_debug_on_failure(caplog):
    """Regression: _is_pickleable_state used to swallow pickle.dumps()
    failures with a bare `except Exception: return False`, giving no trace
    of why a resource was deemed unpickleable. It now logs at debug level."""
    import logging

    unpickleable = lambda: None  # noqa: E731 -- lambdas cannot be pickled

    with caplog.at_level(logging.DEBUG, logger="boti.core.pickle_security"):
        result = ManagedResource._is_pickleable_state(unpickleable)

    assert result is False
    assert any("pickle.dumps() failed" in record.message for record in caplog.records)


def test_managed_resource_pickle_requires_explicit_opt_in():
    """Verify resource pickling is disabled unless explicitly enabled."""
    res = SimpleResource()
    try:
        with pytest.raises(TypeError, match="allow_pickle=True"):
            pickle.dumps(res)
    finally:
        res.close()


def test_managed_resource_unpickle_requires_trusted_scope():
    """Verify resource unpickling is disabled by default even after serialization opt-in."""
    res = SimpleResource(config=ResourceConfig(allow_pickle=True))
    try:
        payload = pickle.dumps(res)
        with pytest.raises(pickle.UnpicklingError, match="disabled by default"):
            pickle.loads(payload)
    finally:
        res.close()


def test_managed_resource_default_logger_recovers_from_root_cwd(monkeypatch, temp_project_root):
    """Verify resources do not anchor logs to /logs when cwd is not useful."""
    notebook_dir = temp_project_root / "notebooks"
    notebook_dir.mkdir()
    notebook_file = notebook_dir / "example_notebook.py"
    notebook_file.touch()

    monkeypatch.setattr(project_module.os, "getcwd", lambda: "/")
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.setattr(
        project_module.inspect,
        "stack",
        lambda: [
            SimpleNamespace(filename="<frame>"),
            SimpleNamespace(filename=str(notebook_file)),
        ],
    )

    res = SimpleResource()
    try:
        assert res.logger.log_dir == (temp_project_root / "logs").resolve()
    finally:
        res.close()


def test_skip_logger_does_not_create_logger():
    """Verify skip_logger=True prevents logger creation."""
    res = SimpleResource(config=ResourceConfig(skip_logger=True))
    try:
        assert res.logger is None, "Expected logger to be None when skip_logger=True"
        # close should not crash despite no logger
        res.close()
        assert res.closed
    finally:
        if not res.closed:
            res.close()


def test_skip_logger_stress_with_fs():
    """Stress test with skip_logger to verify no logger-related crashes."""
    N = 500
    resources = []
    for _ in range(N):
        r = SimpleResource(
            config=ResourceConfig(skip_logger=True),
            fs_factory=lambda: fsspec.filesystem("memory"),
        )
        r.require_fs()
        resources.append(r)
    for r in resources:
        r.close()
        assert r.fs is None
    assert all(r.closed for r in resources)


def test_managed_resource_pickle_strips_runtime_only_logger_and_fs_factory():
    class UnpickleableLogger:
        def __init__(self) -> None:
            import threading

            self._lock = threading.Lock()

    res = SimpleResource(
        config=ResourceConfig(allow_pickle=True, logger=UnpickleableLogger()),
        fs_factory=lambda: fsspec.filesystem("memory"),
    )
    restored = None
    try:
        with ManagedResource.trusted_unpickle_scope():
            restored = pickle.loads(pickle.dumps(res))
        assert isinstance(restored, SimpleResource)
        assert restored.config.logger is None
        assert restored._fs_factory is None
    finally:
        res.close()
        if restored is not None and not restored.closed:
            restored.close()


def test_trusted_unpickle_active_emits_startup_warning(monkeypatch):
    """SECURITY: instantiating a ManagedResource while trusted-unpickle mode is active
    must emit a loud RuntimeWarning so operators never miss the setting."""
    monkeypatch.setattr(PickleSecurityMixin, "_trusted_unpickle_warning_emitted", False)
    with (
        ManagedResource.trusted_unpickle_scope(),
        warnings.catch_warnings(record=True) as caught,
    ):
        warnings.simplefilter("always")
        res = SimpleResource()
        try:
            security_warnings = [
                w for w in caught if issubclass(w.category, RuntimeWarning)
                and "BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE" in str(w.message)
            ]
            assert security_warnings, (
                "Expected a RuntimeWarning about BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE "
                "but none was emitted."
            )
        finally:
            res.close()


def test_trusted_unpickle_warning_emitted_once_per_process(monkeypatch):
    """The trusted-unpickle warning is deduped so worker processes creating many
    resources do not repeat the same SECURITY message on every init."""
    monkeypatch.setattr(PickleSecurityMixin, "_trusted_unpickle_warning_emitted", False)
    with (
        ManagedResource.trusted_unpickle_scope(),
        warnings.catch_warnings(record=True) as caught,
    ):
        warnings.simplefilter("always")
        resources = [SimpleResource() for _ in range(5)]
        try:
            security_warnings = [
                w for w in caught if issubclass(w.category, RuntimeWarning)
                and "BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE" in str(w.message)
            ]
            assert len(security_warnings) == 1, (
                f"Expected exactly one SECURITY warning, got {len(security_warnings)}."
            )
        finally:
            for res in resources:
                res.close()


# ---------------------------------------------------------------------------
# Regression tests for mass-subclassing scenarios
# ---------------------------------------------------------------------------

def test_fs_cleanup_on_close():
    """Verify that _cleanup() releases the owned filesystem on close()."""
    res = SimpleResource(fs_factory=lambda: fsspec.filesystem("memory"))
    res.require_fs()
    assert res.fs is not None
    res.close()
    assert res.fs is None


def test_fs_cleanup_on_context_exit():
    """Verify that context manager exit releases the owned filesystem."""
    with SimpleResource(fs_factory=lambda: fsspec.filesystem("memory")) as res:
        res.require_fs()
        assert res.fs is not None
    assert res.fs is None


def test_fs_cleanup_when_lazy_not_loaded():
    """Verify _cleanup() handles the case where fs was never materialized."""
    res = SimpleResource(fs_factory=lambda: fsspec.filesystem("memory"))
    assert res.fs is None
    res.close()
    assert res.fs is None


def test_managed_resource_stress_create_destroy():
    """Create and destroy many instances to exercise cache eviction and locks."""
    N = 2000
    resources = [SimpleResource() for _ in range(N)]
    for r in resources:
        r.close()
    assert all(r.closed for r in resources)


def test_managed_resource_stress_create_destroy_with_fs():
    """Stress test with fs_factory to exercise the new cleanup path."""
    N = 2000
    resources = []
    for _ in range(N):
        r = SimpleResource(fs_factory=lambda: fsspec.filesystem("memory"))
        r.require_fs()
        resources.append(r)
    for r in resources:
        r.close()
        assert r.fs is None
    assert all(r.closed for r in resources)


def test_concurrent_lifecycle():
    """Multiple threads creating and closing resources simultaneously."""
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker(n: int) -> None:
        try:
            for _ in range(n):
                with SimpleResource() as r:
                    assert not r.closed
                assert r.closed
        except Exception as e:
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=worker, args=(100,)) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent lifecycle raised: {errors}"


def test_concurrent_pickle_roundtrip():
    """Multiple threads pickling and unpickling resources concurrently."""
    errors: list[Exception] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            res = SimpleResource(config=ResourceConfig(allow_pickle=True))
            try:
                with ManagedResource.trusted_unpickle_scope():
                    restored = pickle.loads(pickle.dumps(res))
                restored.close()
            finally:
                res.close()
        except Exception as e:
            with lock:
                errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Concurrent pickle raised: {errors}"


def test_gc_finalizer_fires_when_not_closed():
    """Verify weakref.finalizer fires when an unclosed resource is GC'd."""
    res = SimpleResource()
    finalizer = res._finalizer
    assert finalizer is not None
    assert finalizer.alive

    import weakref
    ref = weakref.ref(res)
    del res
    gc.collect()
    gc.collect()

    assert ref() is None, "Resource was not garbage collected"
    assert not finalizer.alive, "Finalizer did not fire during GC"


def test_cross_thread_trusted_unpickle_scope_isolation():
    """Verify trusted_unpickle_scope is thread-local and does not leak across threads."""
    res = SimpleResource(config=ResourceConfig(allow_pickle=True))
    payload = pickle.dumps(res)
    res.close()

    results: dict[str, bool] = {}

    def setter() -> None:
        with ManagedResource.trusted_unpickle_scope():
            results["setter_active"] = ManagedResource._trusted_unpickle_enabled()

    def checker() -> None:
        results["checker_active"] = ManagedResource._trusted_unpickle_enabled()

    t1 = threading.Thread(target=setter)
    t2 = threading.Thread(target=checker)
    t1.start()
    t1.join()
    t2.start()
    t2.join()

    assert results.get("setter_active") is True
    assert results.get("checker_active") is False, (
        "trusted_unpickle_scope leaked across threads"
    )

    # Also verify the scope works for actual unpickling within one thread
    with ManagedResource.trusted_unpickle_scope():
        restored = pickle.loads(payload)
        assert isinstance(restored, SimpleResource)
        restored.close()


# ---------------------------------------------------------------------------
# Close-barrier and lock-scope regression tests
# ---------------------------------------------------------------------------

def test_concurrent_close_waits_for_cleanup():
    """close() from a second thread must not return until the first closer's
    cleanup has actually finished (barrier semantics)."""
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()

    class SlowCloseResource(ManagedResource):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.cleanup_finished = False

        def _cleanup(self):
            cleanup_started.set()
            release_cleanup.wait(timeout=5)
            self.cleanup_finished = True

    res = SlowCloseResource()
    first_closer = threading.Thread(target=res.close)
    first_closer.start()
    assert cleanup_started.wait(timeout=5)

    observed: dict[str, bool] = {}

    def second_closer() -> None:
        res.close()  # must block until cleanup is done
        observed["finished_at_return"] = res.cleanup_finished

    second = threading.Thread(target=second_closer)
    second.start()
    # The second closer is waiting on the barrier, not returning early.
    second.join(timeout=0.2)
    assert second.is_alive(), "second close() returned before cleanup finished"

    release_cleanup.set()
    first_closer.join(timeout=5)
    second.join(timeout=5)
    assert not second.is_alive()
    assert observed["finished_at_return"] is True
    assert res.closed


def test_reentrant_close_from_cleanup_does_not_deadlock():
    """A _cleanup() hook that calls close() again must return, not deadlock."""
    class ReentrantResource(ManagedResource):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.reentered = False

        def _cleanup(self):
            self.close()  # reentrant: same thread, must be a no-op
            self.reentered = True

    res = ReentrantResource()
    closer = threading.Thread(target=res.close)
    closer.start()
    closer.join(timeout=5)
    assert not closer.is_alive(), "reentrant close() deadlocked"
    assert res.reentered
    assert res.closed


@pytest.mark.asyncio
async def test_reentrant_aclose_from_acleanup_does_not_deadlock():
    """An _acleanup() hook that awaits aclose() again must return, not
    deadlock on the non-reentrant _aclose_lock."""
    class ReentrantAsync(ManagedResource):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.reentered = False

        async def _acleanup(self):
            await self.aclose()  # reentrant: same task, must be a no-op
            self.reentered = True

    res = ReentrantAsync()
    await asyncio.wait_for(res.aclose(), timeout=5)
    assert res.reentered
    assert res.closed


@pytest.mark.asyncio
async def test_concurrent_aclose_tasks_observe_barrier():
    """A sibling task calling aclose() runs on the same OS thread as the
    closer but is a different task: it must wait for cleanup, not skip it."""
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    class SlowAsyncClose(ManagedResource):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.cleanup_finished = False

        async def _acleanup(self):
            cleanup_started.set()
            await release_cleanup.wait()
            self.cleanup_finished = True

    res = SlowAsyncClose()
    first = asyncio.create_task(res.aclose())
    await asyncio.wait_for(cleanup_started.wait(), timeout=5)

    second = asyncio.create_task(res.aclose())
    await asyncio.sleep(0.05)
    assert not second.done(), "sibling aclose() returned before cleanup finished"

    release_cleanup.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=5)
    assert res.cleanup_finished
    assert res.closed


@pytest.mark.asyncio
async def test_reentrant_close_from_acleanup_fallback_does_not_deadlock():
    """aclose()'s to_thread fallback runs _cleanup on a worker thread; a
    reentrant close() from inside the hook must still be recognised."""
    class ReentrantSyncOnly(ManagedResource):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.reentered = False

        def _cleanup(self):
            self.close()
            self.reentered = True

    res = ReentrantSyncOnly()
    await asyncio.wait_for(res.aclose(), timeout=5)
    assert res.reentered
    assert res.closed


@pytest.mark.asyncio
async def test_sync_close_from_sibling_task_during_aclose_raises():
    """Regression: close()'s same-thread reentrancy check used to rely on
    thread identity alone. An async closer suspended at an await leaves its
    loop thread free, so a *sibling task* calling sync close() matched the
    closer's thread id, was mistaken for a reentrant call, and returned
    immediately — silently breaking the "cleanup is done when close()
    returns" barrier. It must refuse loudly instead (waiting would deadlock
    the loop the closer needs to resume on)."""
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    class SlowAsyncClose(ManagedResource):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.cleanup_finished = False

        async def _acleanup(self):
            cleanup_started.set()
            await release_cleanup.wait()
            self.cleanup_finished = True

    res = SlowAsyncClose()
    closer = asyncio.create_task(res.aclose())
    await asyncio.wait_for(cleanup_started.wait(), timeout=5)

    with pytest.raises(RuntimeError, match="await aclose"):
        res.close()
    assert not res.cleanup_finished

    release_cleanup.set()
    await asyncio.wait_for(closer, timeout=5)
    assert res.closed
    assert res.cleanup_finished


@pytest.mark.asyncio
async def test_sync_close_from_loop_thread_during_fallback_window_raises():
    """Regression: while aclose()'s to_thread fallback runs _cleanup() on a
    worker (which adopts closer identity), a sync close() from the loop
    thread used to take the waiter path and block the loop on
    _closed_event.wait() — a hard deadlock, since the fallback's completion
    callback needs that same loop to run. It must raise instead."""
    cleanup_started = threading.Event()
    release_cleanup = threading.Event()

    class SlowSyncOnly(ManagedResource):
        def _cleanup(self):
            cleanup_started.set()
            release_cleanup.wait(timeout=5)

    res = SlowSyncOnly()
    closer = asyncio.create_task(res.aclose())
    await asyncio.to_thread(cleanup_started.wait, 5)

    with pytest.raises(RuntimeError, match="await aclose"):
        res.close()

    release_cleanup.set()
    await asyncio.wait_for(closer, timeout=5)
    assert res.closed


@pytest.mark.asyncio
async def test_sync_close_reentrant_from_within_acleanup_task_still_returns():
    """A sync close() called from *inside* the async closer's own _acleanup()
    (same task, same thread) is genuine reentrancy and must return quietly,
    not raise — task identity is what distinguishes it from a sibling task."""
    class ReentrantMixed(ManagedResource):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.reentered = False

        async def _acleanup(self):
            self.close()  # same task as the aclose() closer
            self.reentered = True

    res = ReentrantMixed()
    await asyncio.wait_for(res.aclose(), timeout=5)
    assert res.reentered
    assert res.closed


def test_closed_property_responsive_while_fs_factory_blocks():
    """A slow fs_factory must not hold _state_lock: closed/close() stay usable."""
    factory_entered = threading.Event()
    release_factory = threading.Event()

    def slow_factory():
        factory_entered.set()
        release_factory.wait(timeout=5)
        return fsspec.filesystem("memory")

    res = SimpleResource(fs_factory=slow_factory)
    loader = threading.Thread(target=res.require_fs)
    loader.start()
    assert factory_entered.wait(timeout=5)

    # With the factory mid-flight, state reads must return promptly.
    probe_done = threading.Event()

    def probe() -> None:
        assert res.closed is False
        probe_done.set()

    prober = threading.Thread(target=probe)
    prober.start()
    assert probe_done.wait(timeout=1), "closed property blocked behind fs_factory"

    release_factory.set()
    loader.join(timeout=5)
    prober.join(timeout=5)
    assert res.fs is not None
    res.close()


def test_close_during_fs_factory_discards_filesystem():
    """If the resource is closed while the factory runs, the built filesystem
    must not be published and require_fs must raise."""
    factory_entered = threading.Event()
    release_factory = threading.Event()

    def slow_factory():
        factory_entered.set()
        release_factory.wait(timeout=5)
        return fsspec.filesystem("memory")

    res = SimpleResource(fs_factory=slow_factory)
    outcome: dict[str, object] = {}

    def loader() -> None:
        try:
            res.require_fs()
            outcome["result"] = "published"
        except RuntimeError as exc:
            outcome["result"] = exc

    loader_thread = threading.Thread(target=loader)
    loader_thread.start()
    assert factory_entered.wait(timeout=5)

    res.close()  # completes promptly: factory holds only _fs_init_lock
    release_factory.set()
    loader_thread.join(timeout=5)

    assert isinstance(outcome["result"], RuntimeError)
    assert res.fs is None



