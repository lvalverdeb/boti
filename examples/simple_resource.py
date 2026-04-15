"""
Minimal synchronous ManagedResource example.
"""

from boti.core import ManagedResource


class SimpleResource(ManagedResource):
    """Small custom resource with explicit synchronous cleanup."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name
        self.cleaned_up = False

    def describe(self) -> str:
        return f"SimpleResource(name={self.name!r}, closed={self.closed})"

    def _cleanup(self) -> None:
        self.cleaned_up = True


def main() -> None:
    with SimpleResource(name="example-sync") as resource:
        print(resource.describe())

    print(f"closed={resource.closed}, cleaned_up={resource.cleaned_up}")


if __name__ == "__main__":
    main()
