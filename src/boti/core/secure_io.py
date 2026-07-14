"""
Sandboxed I/O resource for Boti Tools.

Provides the SecureResource class which enforces path sandboxing
to prevent path traversal attacks during file operations.
"""

from __future__ import annotations

import tempfile
import warnings
from pathlib import Path
from typing import IO, Any

__all__ = ["SecureResource"]

from boti.core.managed_resource import ManagedResource
from boti.core.models import ResourceConfig
from boti.core.project import ProjectService
from boti.core.security import is_secure_path


class SecureResource(ManagedResource):
    """
    Enhanced resource that automatically enforces path sandboxing for all I/O.

    Ensures that all operations remain within the project root, the system
    temporary directory, or explicitly configured extra allowed paths to
    prevent path traversal.
    """

    def __init__(self, config: ResourceConfig | None = None, **kwargs: Any) -> None:
        """
        Initialize the SecureResource.
        Sets the project root for sandboxing context.
        """
        super().__init__(config=config, **kwargs)

        # Use config for project root, fallback to auto-detection
        root = self.config.project_root or ProjectService.detect_project_root()
        self.project_root = Path(root).resolve()

        configured_allowed_paths: list[Path] = []
        for raw in self.config.extra_allowed_paths:
            unresolved = Path(raw)
            # Check for symlink BEFORE resolving — Path.resolve() follows symlinks
            # so is_symlink() on the result is always False.
            if unresolved.is_symlink():
                raise ValueError(
                    f"extra_allowed_path must not be a symlink — it could silently widen "
                    f"the sandbox boundary to an unintended location: {raw!r}"
                )
            resolved = unresolved.resolve()
            if not resolved.exists():
                warnings.warn(
                    f"extra_allowed_path does not exist and will have no effect: {raw!r}",
                    UserWarning,
                    stacklevel=2,
                )
            configured_allowed_paths.append(resolved)
        default_allowed_paths = [self.project_root, Path(tempfile.gettempdir()).resolve()]

        self.allowed_paths: list[Path] = []
        for candidate in [*default_allowed_paths, *configured_allowed_paths]:
            if candidate not in self.allowed_paths:
                self.allowed_paths.append(candidate)

    def get_secure_path(self, path: str | Path) -> Path:
        """
        Validates and resolves a path within the configured sandbox roots.

        Args:
            path: The path to secure.

        Returns:
            Path: The resolved absolute Path object.

        Raises:
            PermissionError: If the path is outside the configured sandbox roots,
                or cannot be resolved at all (e.g. contains a NUL byte, hits a
                filesystem error, or fails a symlink lookup). Resolution failures
                are treated as untrusted input and denied rather than left to
                bubble up as a raw OSError/ValueError.
        """
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError) as exc:
            if self.logger is not None:
                self.logger.error(
                    f"SECURITY VIOLATION: Path could not be resolved. "
                    f"Target: {path!r}, Error: {exc}"
                )
            raise PermissionError(
                f"Access denied: Path {path!r} could not be resolved ({exc})."
            ) from exc
        if not is_secure_path(resolved, self.allowed_paths):
            if self.logger is not None:
                self.logger.error(
                    f"SECURITY VIOLATION: Path traversal attempt detected. "
                    f"Target: {path} (resolved: {resolved}), Allowed Roots: {self.allowed_paths}"
                )
            raise PermissionError(
                f"Access denied: Path {path} is outside the configured sandbox roots."
            )
        return resolved

    def open_secure(self, path: str | Path, mode: str = "r", **kwargs: Any) -> IO[Any]:
        """
        Opens a file securely, enforcing sandbox constraints.

        Args:
            path: The path to the file.
            mode: The file mode.
            **kwargs: Arguments for the open() function.

        Returns:
            File handle.
        """
        secure_path = self.get_secure_path(path)
        return open(secure_path, mode, **kwargs)

    def write_text_secure(self, path: str | Path, content: str, encoding: str = "utf-8") -> None:
        """Writes text content to a file securely."""
        secure_path = self.get_secure_path(path)
        secure_path.write_text(content, encoding=encoding)

    def read_text_secure(self, path: str | Path, encoding: str = "utf-8") -> str:
        """Reads text content from a file securely."""
        secure_path = self.get_secure_path(path)
        return secure_path.read_text(encoding=encoding)
