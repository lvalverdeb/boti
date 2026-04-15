"""
ManagedResource example backed by an fsspec filesystem.
"""

from __future__ import annotations

from fsspec.implementations.memory import MemoryFileSystem

from boti.core import ManagedResource


class FilesystemResource(ManagedResource):
    """Custom resource that reads and writes through require_fs()."""

    def __init__(self) -> None:
        super().__init__(fs_factory=MemoryFileSystem)

    def write_text(self, path: str, content: str) -> None:
        fs = self.require_fs()
        with fs.open(path, "w", encoding="utf-8") as handle:
            handle.write(content)

    def read_text(self, path: str) -> str:
        fs = self.require_fs()
        with fs.open(path, "r", encoding="utf-8") as handle:
            return handle.read()

    def _cleanup(self) -> None:
        if self._owns_fs and self.fs is not None:
            self.fs = None


def main() -> None:
    with FilesystemResource() as resource:
        resource.write_text("memory://example.txt", "hello from fsspec")
        print(resource.read_text("memory://example.txt"))

    print(f"closed={resource.closed}")


if __name__ == "__main__":
    main()
