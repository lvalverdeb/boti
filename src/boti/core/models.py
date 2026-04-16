"""
Core Pydantic models for configuration and data validation.

This module defines standardized models for logger settings, resource 
configurations, and other core components of the Boti toolkit.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["LoggerConfig", "ResourceConfig"]

import re
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator
from typing import Any, ClassVar, Optional, Union


class LoggerConfig(BaseModel):
    """
    Configuration for the Boti logger.
    """
    model_config = ConfigDict(arbitrary_types_allowed=True)

    log_dir: Union[str, Path] = Field(default="logs", description="Directory for log files.")
    logger_name: str = Field(..., description="Unique name for the logger.")
    log_file: Optional[str] = Field(None, description="Base name for the log file.")
    log_level: int = Field(default=20, description="Logging level (e.g., logging.INFO).")
    verbose: bool = Field(default=False, description="Enable verbose logging output.")
    debug: bool = Field(default=False, description="Enable debug logging output.")

    _LOGGER_NAME_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.-]+$")

    @staticmethod
    def _validate_base_log_name(value: str) -> str:
        if not value or value in {".", ".."} or "/" in value or "\\" in value:
            raise ValueError("log_file must be a simple base name, not a path.")
        return value

    @field_validator("log_dir")
    @classmethod
    def validate_log_dir(cls, v: Union[str, Path]) -> Path:
        """Ensures log_dir is a Path object without rebasing relative paths."""
        return Path(v).expanduser()

    @field_validator("log_file")
    @classmethod
    def validate_log_file(cls, v: Optional[str]) -> Optional[str]:
        """Reject path-like log file names that could escape the log directory."""
        if v is None:
            return None
        return cls._validate_base_log_name(v)

    @field_validator("logger_name")
    @classmethod
    def validate_logger_name(cls, value: str) -> str:
        """Restrict logger names to safe characters for logger registry/filtering."""
        if not value or value in {".", ".."} or not cls._LOGGER_NAME_PATTERN.fullmatch(value):
            raise ValueError(
                "logger_name must contain only letters, digits, dots, underscores, or hyphens."
            )
        return value

    @model_validator(mode="after")
    def validate_effective_log_file(self) -> "LoggerConfig":
        """Validate the effective log file name without mutating the user-provided value."""
        effective_log_file = self.log_file or self.logger_name
        self._validate_base_log_name(effective_log_file)
        return self


class ResourceConfig(BaseModel):
    """
    Base configuration for ManagedResource and SecureResource.
    """
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    verbose: bool = Field(default=False, description="Enable verbose output.")
    debug: bool = Field(default=False, description="Enable debug output.")
    logger: Optional[Any] = Field(default=None, description="Optional logger instance.")
    allow_pickle: bool = Field(
        default=False,
        description=(
            "Allow trusted pickle serialization for distributed workflows. "
            "Keep disabled unless both serialization and deserialization happen in trusted runtimes."
        ),
    )

    # Secure specific fields
    project_root: Optional[Union[str, Path]] = Field(default=None, description="Project root for sandboxing.")
    extra_allowed_paths: list[Union[str, Path]] = Field(default_factory=list, description="Additional allowed paths.")
