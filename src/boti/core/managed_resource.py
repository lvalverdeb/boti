"""
Lifecycle management base for Boti resources.

Provides the ManagedResource abstract base class to standardize 
initialization, cleanup, and context management across the toolkit.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

__all__ = ["ManagedResource"]

import fsspec

from boti.core.fsspec_mixin import FsspecMixin
from boti.core.lifecycle import LifecycleCore
from boti.core.logger import Logger
from boti.core.models import ResourceConfig
from boti.core.pickle_security import PickleSecurityMixin
from boti.core.project import ProjectService


class ManagedResource(PickleSecurityMixin, FsspecMixin, LifecycleCore):
    """
    Lifecycle management base class for resources.

    Handles both synchronous and asynchronous cleanup, logging integration,
    thread-safe state management, and provides a consistent context manager interface.
    """

    def __init__(
        self,
        config: ResourceConfig | None = None,
        *,
        fs: fsspec.AbstractFileSystem | None = None,
        fs_factory: Callable[[], fsspec.AbstractFileSystem] | None = None,
        **config_overrides: Any,
    ) -> None:
        if config is None:
            config = ResourceConfig(**config_overrides)
        elif config_overrides:
            unexpected_keys = ", ".join(sorted(config_overrides))
            raise TypeError(
                f"Unexpected config override(s) for {self.__class__.__name__}: {unexpected_keys}"
            )
        
        self.config = config
        self.verbose = config.verbose
        self.debug = config.debug
        self._skip_logger = config.skip_logger

        # Logger must be configured before LifecycleCore.__init__() runs
        # (reached via the FsspecMixin -> LifecycleCore super() chain), since
        # it attaches the GC finalizer with self.logger as a captured argument.
        self._configure_logger()
        super().__init__(fs=fs, fs_factory=fs_factory)
        self._warn_if_trusted_unpickle_active()

    def _configure_logger(self) -> None:
        """Restore the configured logger or create a default one for runtime use."""
        if self._skip_logger:
            self.logger = None
            return
        if self.config.logger is None:
            log_base_dir = self.config.project_root or ProjectService.detect_project_root()
            self.logger = Logger.default_logger(
                logger_name=self.__class__.__name__,
                base_dir=log_base_dir,
            )
            level = (
                Logger.DEBUG if self.debug
                else (Logger.INFO if self.verbose else Logger.WARNING)
            )
            self.logger.set_level(level)
        else:
            self.logger = self.config.logger

