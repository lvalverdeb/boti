"""
Tests for PicklableLifecycleCoreMixin.

Covers a bare `LifecycleCore` composition — no fsspec/pickle ownership of
its own — that needs to survive pickle.dumps()/loads() without choking on
LifecycleCore's locks/events/GC finalizer.
"""

from __future__ import annotations

import pickle

from boti.core.lifecycle import LifecycleCore
from boti.core.lifecycle_pickle import PicklableLifecycleCoreMixin


class PicklableThing(PicklableLifecycleCoreMixin, LifecycleCore):
    def __init__(self, name: str) -> None:
        self.name = name
        self.cleaned_up = False
        self.cleanup_calls = 0
        super().__init__()

    def _cleanup(self) -> None:
        self.cleaned_up = True
        self.cleanup_calls += 1


def test_roundtrip_preserves_instance_state():
    thing = PicklableThing("widget")
    restored = pickle.loads(pickle.dumps(thing))
    assert restored.name == "widget"
    assert not restored.closed
    thing.close()
    restored.close()


def test_getstate_strips_lifecyclecore_locks_and_events():
    thing = PicklableThing("widget")
    try:
        state = thing.__getstate__()
        for key in (
            "_state_lock", "_aclose_lock", "_closed_event",
            "_closing_thread", "_closing_task", "_finalizer",
        ):
            assert key not in state
        assert state["name"] == "widget"
    finally:
        thing.close()


def test_setstate_rebuilds_fresh_lock_objects():
    thing = PicklableThing("widget")
    try:
        original_lock = thing._state_lock
        restored = pickle.loads(pickle.dumps(thing))
        try:
            assert restored._state_lock is not original_lock
            assert restored._aclose_lock is not thing._aclose_lock
            assert restored._closed_event is not thing._closed_event
        finally:
            restored.close()
    finally:
        thing.close()


def test_roundtrip_preserves_closed_state_without_rerunning_cleanup():
    thing = PicklableThing("widget")
    thing.close()
    assert thing.cleanup_calls == 1
    restored = pickle.loads(pickle.dumps(thing))
    assert restored.closed
    # __setstate__ rebuilds locks/events but must not re-invoke _cleanup().
    assert restored.cleanup_calls == 1


def test_roundtrip_open_instance_still_usable_and_closable():
    thing = PicklableThing("widget")
    try:
        restored = pickle.loads(pickle.dumps(thing))
        assert not restored.closed
        restored.close()
        assert restored.closed
        assert restored.cleaned_up
    finally:
        thing.close()
