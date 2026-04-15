"""
Sandboxed I/O resource for Boti Tools.

Provides the SecureResource class which enforces path sandboxing 
to prevent path traversal attacks during file operations.
"""

from __future__ import annotations
import tempfile
from pathlib import Path
from typing import Any, Optional, Union

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
    
    def __init__(self, config: Optional[ResourceConfig] = None, **kwargs: Any) -> None:
        """
        Initialize the SecureResource.
        Sets the project root for sandboxing context.
        """
        super().__init__(config=config, **kwargs)
        
        # Use config for project root, fallback to auto-detection
        root = self.config.project_root or ProjectService.detect_project_root()
        self.project_root = Path(root).resolve()

        configured_allowed_paths = [
            Path(p).resolve() for p in self.config.extra_allowed_paths
        ]
        default_allowed_paths = [self.project_root, Path(tempfile.gettempdir()).resolve()]

        self.allowed_paths: list[Path] = []
        for candidate in [*default_allowed_paths, *configured_allowed_paths]:
            if candidate not in self.allowed_paths:
                self.allowed_paths.append(candidate)

    def get_secure_path(self, path: Union[str, Path]) -> Path:
        """
        Validates and resolves a path within the configured sandbox roots.
        
        Args:
            path: The path to secure.
            
        Returns:
            Path: The resolved absolute Path object.
            
        Raises:
            PermissionError: If the path is outside the configured sandbox roots.
        """
        resolved = Path(path).resolve()
        if not is_secure_path(resolved, self.allowed_paths):
            self.logger.error(
                f"SECURITY VIOLATION: Path traversal attempt detected. "
                f"Target: {path} (resolved: {resolved}), Allowed Roots: {self.allowed_paths}"
            )
            raise PermissionError(
                f"Access denied: Path {path} is outside the configured sandbox roots."
            )
        return resolved

    def open_secure(self, path: Union[str, Path], mode: str = "r", **kwargs: Any):
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

    def write_text_secure(self, path: Union[str, Path], content: str, encoding: str = "utf-8") -> None:
        """Writes text content to a file securely."""
        secure_path = self.get_secure_path(path)
        secure_path.write_text(content, encoding=encoding)

    def read_text_secure(self, path: Union[str, Path], encoding: str = "utf-8") -> str:
        """Reads text content from a file securely."""
        secure_path = self.get_secure_path(path)
        return secure_path.read_text(encoding=encoding)
