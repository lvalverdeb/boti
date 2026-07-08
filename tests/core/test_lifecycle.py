"""
Tests for ManagedResource lifecycle (sync and async).
"""
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


def test_managed_resource_pickle_strips_runtime_only_logger_and_fs_factory():
    class UnpickleableLogger:
        def __init__(self) -> None:
            import threading


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


def test_trusted_unpickle_active_emits_startup_warning():
    """SECURITY: instantiating a ManagedResource while trusted-unpickle mode is active
    must emit a loud RuntimeWarning so operators never miss the setting."""
    with ManagedResource.trusted_unpickle_scope():
        with warnings.catch_warnings(record=True) as caught:
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



