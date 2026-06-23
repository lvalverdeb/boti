"""
ManagedResource pickle/unpickle lifecycle example.

Demonstrates:
  - Pickle serialization with allow_pickle=True
  - Unpickle with trusted_unpickle_scope() context manager
  - _restore_runtime_state() hook after unpickling
  - The environment variable fallback BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE
  - Pickle denial when allow_pickle=False (default)
  - Unpickle rejection without trusted scope
"""

from __future__ import annotations

import pickle
from typing import Any

from boti.core import ManagedResource
from boti.core.models import ResourceConfig


class PickleResource(ManagedResource):
    """Resource that preserves name through pickle and restores runtime."""

    def __init__(self, name: str = "default", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.name = name
        self.restored = False

    def greet(self) -> str:
        return f"PickleResource(name={self.name!r})"

    def _cleanup(self) -> None:
        pass

    def _restore_runtime_state(self) -> None:
        self.restored = True


def example_pickle_roundtrip() -> None:
    """Serialize and deserialize a resource through pickle."""
    original = PickleResource(
        name="pickle-test",
        config=ResourceConfig(allow_pickle=True),
    )
    data = pickle.dumps(original)
    original.close()

    with ManagedResource.trusted_unpickle_scope():
        restored = pickle.loads(data)

    print(f"  restored name: {restored.name!r}")
    print(f"  restored greet: {restored.greet()}")
    print(f"  restored.restored: {restored.restored} (from _restore_runtime_state)")

    restored.close()
    print()


def example_pickle_disabled_by_default() -> None:
    """Pickle raises TypeError when allow_pickle=False."""
    resource = PickleResource(name="no-pickle")
    try:
        pickle.dumps(resource)
    except TypeError as exc:
        print(f"  pickle denied: {exc}")
    resource.close()
    print()


def example_unpickle_without_trusted_scope() -> None:
    """Unpickle raises UnpicklingError without trusted scope."""
    resource = PickleResource(
        name="blocked",
        config=ResourceConfig(allow_pickle=True),
    )
    data = pickle.dumps(resource)
    resource.close()

    try:
        pickle.loads(data)
    except pickle.UnpicklingError as exc:
        print(f"  unpickle denied: {exc}")
    print()


def example_factory_with_env_var() -> None:
    """Demonstrate using BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE env var."""
    import os

    resource = PickleResource(
        name="env-var-mode",
        config=ResourceConfig(allow_pickle=True),
    )
    data = pickle.dumps(resource)
    resource.close()

    # Enable trusted unpickle via env var (alternative to context manager)
    os.environ[ManagedResource._TRUSTED_UNPICKLE_ENV] = "1"

    restored = pickle.loads(data)
    print(f"  restored via env var: {restored.name!r}")
    restored.close()

    os.environ.pop(ManagedResource._TRUSTED_UNPICKLE_ENV, None)
    print()


def main() -> None:
    print("=== ManagedResource pickle examples ===\n")
    example_pickle_roundtrip()
    example_pickle_disabled_by_default()
    example_unpickle_without_trusted_scope()
    example_factory_with_env_var()


if __name__ == "__main__":
    main()
