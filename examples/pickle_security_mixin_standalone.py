"""
PickleSecurityMixin composed directly onto LifecycleCore, with no fsspec.

PickleSecurityMixin is the piece of ManagedResource that gates pickling
behind an explicit opt-in and gates unpickling behind a trusted-runtime
check, since deserializing an untrusted payload can execute
attacker-controlled code. This example composes it with LifecycleCore
directly (no FsspecMixin) to build a minimal picklable resource — and shows
that __getstate__'s attribute-pop list degrades safely when the filesystem
mixin isn't present at all, rather than assuming it always is.

Demonstrates:
  - Composing PickleSecurityMixin + LifecycleCore, reusing ResourceConfig
    purely for its allow_pickle field
  - Pickle denied by default; allowed with allow_pickle=True
  - Unpickle denied without trusted_unpickle_scope()
  - __getstate__ tolerating the total absence of fsspec-owned state
    (no `fs`/`_fs_factory`/`_owns_fs` keys exist at all on this class)
"""

from __future__ import annotations

import pickle

from boti.core.lifecycle import LifecycleCore
from boti.core.models import ResourceConfig
from boti.core.pickle_security import PickleSecurityMixin


class Ledger(PickleSecurityMixin, LifecycleCore):
    """A minimal picklable resource with no filesystem concept whatsoever."""

    def __init__(self, name: str, config: ResourceConfig | None = None) -> None:
        self.config = config or ResourceConfig()
        self.name = name
        self.entries: list[str] = []
        self._configure_logger()  # PickleSecurityMixin.__setstate__ expects this hook
        super().__init__()

    def _configure_logger(self) -> None:
        # No default-logger factory here — just pass through whatever the
        # config carries, matching PickleSecurityMixin's expectations without
        # needing ManagedResource's ProjectService/Logger integration.
        self.logger = self.config.logger

    def record(self, entry: str) -> None:
        self.entries.append(entry)


def example_pickle_denied_by_default() -> None:
    """Pickling raises TypeError unless allow_pickle=True."""
    ledger = Ledger(name="no-pickle")
    ledger.record("opening balance: 0")
    try:
        pickle.dumps(ledger)
    except TypeError as exc:
        print(f"  pickle denied: {exc}")
    ledger.close()
    print()


def example_pickle_roundtrip_with_trusted_scope() -> None:
    """allow_pickle=True + trusted_unpickle_scope() round-trips cleanly."""
    ledger = Ledger(name="roundtrip", config=ResourceConfig(allow_pickle=True))
    ledger.record("deposit: 100")
    data = pickle.dumps(ledger)
    ledger.close()

    with Ledger.trusted_unpickle_scope():
        restored = pickle.loads(data)

    print(f"  restored.name={restored.name!r}, entries={restored.entries}")
    restored.close()
    print()


def example_unpickle_denied_without_trusted_scope() -> None:
    """Deserializing outside trusted_unpickle_scope() is rejected."""
    ledger = Ledger(name="blocked", config=ResourceConfig(allow_pickle=True))
    data = pickle.dumps(ledger)
    ledger.close()

    try:
        pickle.loads(data)
    except pickle.UnpicklingError as exc:
        print(f"  unpickle denied: {exc}")
    print()


def example_getstate_tolerates_missing_fs_mixin() -> None:
    """__getstate__ pops fs-owned keys with dict.pop(key, None) — Ledger has
    none of them, and nothing breaks."""
    ledger = Ledger(name="fs-free", config=ResourceConfig(allow_pickle=True))
    state = ledger.__getstate__()
    print(f"  state keys: {sorted(state)}")
    print(f"  no 'fs'/'_fs_factory'/'_owns_fs' keys present: "
          f"{not {'fs', '_fs_factory', '_owns_fs'} & state.keys()}")
    ledger.close()
    print()


def main() -> None:
    print("=== PickleSecurityMixin standalone examples ===\n")
    example_pickle_denied_by_default()
    example_pickle_roundtrip_with_trusted_scope()
    example_unpickle_denied_without_trusted_scope()
    example_getstate_tolerates_missing_fs_mixin()


if __name__ == "__main__":
    main()
