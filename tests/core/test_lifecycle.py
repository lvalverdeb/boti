"""
Tests for ManagedResource lifecycle (sync and async).
"""
import asyncio
import pickle
import warnings
from types import SimpleNamespace

import fsspec
import pytest
from boti.core import ManagedResource
from boti.core import project as project_module
from boti.core.models import ResourceConfig
from pydantic import ValidationError


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
