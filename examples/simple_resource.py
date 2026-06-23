"""
Minimal synchronous ManagedResource example.

Demonstrates:
  - Custom resource with explicit synchronous cleanup
  - Context manager usage (__enter__ / __exit__)
  - Close idempotency and suppress_errors
  - GC leak detection via weakref.finalize
  - _restore_runtime_state() hook
"""

from boti.core import ManagedResource
from boti.core.models import ResourceConfig


class SimpleResource(ManagedResource):
    """Custom resource with explicit synchronous cleanup and runtime state."""

    def __init__(self, name: str = "default", **kwargs) -> None:
        super().__init__(**kwargs)
        self.name = name
        self.cleaned_up = False
        self.restored = False

    def describe(self) -> str:
        return f"SimpleResource(name={self.name!r}, closed={self.closed})"

    def _cleanup(self) -> None:
        self.cleaned_up = True

    def _restore_runtime_state(self) -> None:
        self.restored = True


def example_basic_lifecycle() -> None:
    """Basic context manager usage with state verification."""
    with SimpleResource(name="example-sync") as resource:
        print(f"  inside context: {resource.describe()}")

    print(f"  after exit: closed={resource.closed}, cleaned_up={resource.cleaned_up}")
    print()


def example_close_idempotency() -> None:
    """Repeated close() calls are safe and do not re-trigger cleanup."""
    resource = SimpleResource(name="idempotent")
    resource.close()
    before_cleanup = resource.cleaned_up
    resource.close()
    resource.close()
    print(f"  cleaned_up={resource.cleaned_up} (should be {before_cleanup}, unchanged)")
    print()


def example_suppress_errors() -> None:
    """Use suppress_errors when you do not want cleanup failures to propagate."""

    class FragileResource(SimpleResource):
        def _cleanup(self) -> None:
            raise RuntimeError("cleanup failed")

    resource = FragileResource(name="fragile")
    resource.close(suppress_errors=True)
    print(f"  closed={resource.closed} (survived cleanup failure)")
    print()


def example_restore_runtime_state() -> None:
    """The _restore_runtime_state hook fires when resource is re-opened."""
    resource = SimpleResource(name="restored", config=ResourceConfig(allow_pickle=True))
    resource.close()
    print(f"  restored={resource.restored} after close (set during __init__)")
    print()


def example_gc_warning() -> None:
    """A resource that is GC'd without close emits a warning via weakref.finalize."""
    import warnings

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _leaked = SimpleResource(name="leaky")
        _leaked = None  # allow GC

        gc_warnings = [w for w in caught if "garbage collected" in str(w.message)]
        if gc_warnings:
            print(f"  GC warning issued: {gc_warnings[0].message}")
        else:
            print("  (no GC warning — resource was not collected)")
    print()


def main() -> None:
    print("=== SimpleResource examples ===\n")
    example_basic_lifecycle()
    example_close_idempotency()
    example_suppress_errors()
    example_restore_runtime_state()
    example_gc_warning()


if __name__ == "__main__":
    main()
