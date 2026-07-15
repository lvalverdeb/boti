"""
Tests for the Agent base class.
"""

import gc
import logging
import weakref

import pytest
from pydantic import ValidationError

from boti.core import Agent, AgentConfig


class SimpleAgent(Agent):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.cleaned_up_sync = False
        self.cleaned_up_async = False

    def _cleanup(self):
        self.cleaned_up_sync = True

    async def _acleanup(self):
        self.cleaned_up_async = True


def test_agent_sync_context():
    """Verify synchronous context manager lifecycle."""
    agent = SimpleAgent(skip_logger=True)
    with agent as a:
        assert not a.closed
        assert not a.cleaned_up_sync

    assert agent.closed
    assert agent.cleaned_up_sync


@pytest.mark.asyncio
async def test_agent_async_context():
    """Verify asynchronous context manager lifecycle."""
    agent = SimpleAgent(skip_logger=True)
    async with agent as a:
        assert not a.closed
        assert not a.cleaned_up_async

    assert agent.closed
    assert agent.cleaned_up_async


@pytest.mark.asyncio
async def test_agent_aclose_falls_back_to_sync_cleanup():
    """Verify aclose() runs _cleanup() when _acleanup() is not overridden."""

    class SyncOnlyAgent(Agent):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.cleaned_up = False

        def _cleanup(self):
            self.cleaned_up = True

    agent = SyncOnlyAgent(skip_logger=True)
    await agent.aclose()
    assert agent.closed
    assert agent.cleaned_up


def test_agent_close_idempotent():
    """Verify calling close() multiple times is safe."""
    agent = SimpleAgent(skip_logger=True)
    agent.close()
    assert agent.closed
    agent.close()  # Should not raise
    assert agent.closed


def test_agent_config_rejects_unknown_fields():
    """Verify AgentConfig fails fast on unexpected config input."""
    with pytest.raises(ValidationError):
        AgentConfig(unexpected_setting=True)


def test_agent_rejects_config_overrides_when_config_is_supplied():
    """Verify a validated config and ad-hoc overrides cannot be mixed silently."""
    config = AgentConfig()
    with pytest.raises(TypeError, match="Unexpected config override"):
        SimpleAgent(config=config, verbose=True)


def test_agent_skip_logger_does_not_create_logger():
    """Verify skip_logger=True prevents logger creation."""
    agent = SimpleAgent(skip_logger=True)
    try:
        assert agent.logger is None
        agent.close()  # close should not crash despite no logger
        assert agent.closed
    finally:
        if not agent.closed:
            agent.close()


def test_agent_uses_provided_logger_without_creating_default():
    """Verify a caller-supplied logger is used as-is."""
    sentinel = object()
    agent = SimpleAgent(logger=sentinel)
    try:
        assert agent.logger is sentinel
    finally:
        agent.close()


def test_agent_default_logger_level_follows_verbose_and_debug():
    """Verify the default logger's level reflects verbose/debug config.

    Uses a distinct Agent subclass per case so each gets its own
    Logger.default_logger() cache entry (keyed on class name), since
    set_level() mutates the cached logger in place.
    """
    cases = [
        (False, False, logging.WARNING),
        (True, False, logging.INFO),
        (True, True, logging.DEBUG),
        (False, True, logging.DEBUG),
    ]
    for verbose, debug, expected_level in cases:
        cls = type(f"LevelAgent_{verbose}_{debug}", (SimpleAgent,), {})
        agent = cls(verbose=verbose, debug=debug)
        try:
            assert agent.logger._core.level == expected_level
        finally:
            agent.close()


def test_agent_gc_finalizer_fires_when_not_closed():
    """Verify weakref.finalizer fires when an unclosed agent is GC'd."""
    agent = SimpleAgent(skip_logger=True)
    finalizer = agent._finalizer
    assert finalizer is not None
    assert finalizer.alive

    ref = weakref.ref(agent)
    del agent
    gc.collect()
    gc.collect()

    assert ref() is None, "Agent was not garbage collected"
    assert not finalizer.alive, "Finalizer did not fire during GC"


def test_agent_assert_open_raises_after_close():
    """Verify operations gated by _assert_open() reject use after close()."""
    agent = SimpleAgent(skip_logger=True)
    agent.close()
    with pytest.raises(RuntimeError, match="is closed"):
        agent._assert_open()
