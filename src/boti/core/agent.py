"""
Base class for AI agents built on Boti's lifecycle guarantees.

Provides the Agent abstract base class, reusing LifecycleCore's sync+async
close barrier, context-manager protocol, and leak-warning finalizer without
the fsspec lazy-init or pickle-security gating that ManagedResource carries
for its filesystem-backed use cases.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict

__all__ = ["Agent", "AgentConfig"]

from boti.core.lifecycle import LifecycleCore
from boti.core.logger import Logger


class AgentConfig(BaseModel):
    """Typed configuration for Agent, mirroring ResourceConfig's logging fields."""

    model_config = ConfigDict(extra="forbid")

    verbose: bool = False
    debug: bool = False
    logger: Any = None
    skip_logger: bool = False


class Agent(LifecycleCore):
    """
    Base class for AI agents.

    Handles logging integration and delegates lifecycle management (thread-safe
    close()/aclose() barrier, context-manager protocol, GC leak-warning) to
    LifecycleCore. Subclasses override _cleanup()/_acleanup() to release
    agent-specific resources (e.g. an LLM client, tool-call sessions).
    """

    def __init__(self, config: AgentConfig | None = None, **config_overrides: Any) -> None:
        if config is None:
            config = AgentConfig(**config_overrides)
        elif config_overrides:
            unexpected_keys = ", ".join(sorted(config_overrides))
            raise TypeError(
                f"Unexpected config override(s) for {self.__class__.__name__}: {unexpected_keys}"
            )

        self.config = config
        self.verbose = config.verbose
        self.debug = config.debug
        self._skip_logger = config.skip_logger

        # Logger must be configured before LifecycleCore.__init__() runs,
        # since it attaches the GC finalizer with self.logger as a captured argument.
        self._configure_logger()
        super().__init__()

    def _configure_logger(self) -> None:
        """Restore the configured logger or create a default one for runtime use."""
        if self._skip_logger:
            self.logger = None
            return
        if self.config.logger is None:
            self.logger = Logger.default_logger(logger_name=self.__class__.__name__)
            level = (
                Logger.DEBUG if self.debug else (Logger.INFO if self.verbose else Logger.WARNING)
            )
            self.logger.set_level(level)
        else:
            self.logger = self.config.logger
