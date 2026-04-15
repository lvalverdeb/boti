"""
Environment-backed settings helpers for Boti.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional, TypeVar

from pydantic import BaseModel, Field, SecretStr, TypeAdapter, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import DotEnvSettingsSource
from boti.core.security import validate_environment_bindings

TModel = TypeVar("TModel", bound=BaseModel)
_ENV_PREFIX_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*_?$")


class ArbitraryDotEnvSettings(BaseSettings):
    """Lightweight settings model used to load arbitrary key/value pairs from dotenv files."""

    model_config = SettingsConfigDict(
        env_file=None,
        extra="allow",
        case_sensitive=True,
    )


def load_dotenv_values(env_file: Path) -> dict[str, str]:
    """Load raw dotenv values while preserving variable name casing."""
    source = DotEnvSettingsSource(
        ArbitraryDotEnvSettings,
        env_file=env_file,
        case_sensitive=True,
    )
    data = source()
    return validate_environment_bindings({
        str(key): "" if value is None else str(value)
        for key, value in data.items()
    })


def _validate_env_prefix(prefix: str) -> str:
    normalized = prefix.strip()
    if not normalized or not _ENV_PREFIX_PATTERN.fullmatch(normalized):
        raise ValueError(
            "Environment prefixes must match [A-Za-z_][A-Za-z0-9_]* and may end with a single underscore."
        )
    return normalized


def load_prefixed_model(
    model_cls: type[TModel],
    prefix: str,
    *,
    env_file: Optional[Path | str] = None,
) -> TModel:
    """Load a typed model from environment variables using an explicit prefix."""
    normalized_prefix = _validate_env_prefix(prefix)

    merged_bindings: dict[str, str] = {}
    if env_file is not None:
        merged_bindings.update(load_dotenv_values(Path(env_file)))
    merged_bindings.update({key: value for key, value in os.environ.items() if isinstance(value, str)})

    payload: dict[str, Any] = {}
    for field_name, field in model_cls.model_fields.items():
        env_key = f"{normalized_prefix}{field_name.upper()}"
        raw_value = merged_bindings.get(env_key)
        if raw_value in (None, ""):
            continue

        adapter = TypeAdapter(field.annotation)
        try:
            payload[field_name] = adapter.validate_python(raw_value)
        except ValidationError:
            try:
                payload[field_name] = adapter.validate_json(raw_value)
            except ValidationError as json_exc:
                raise ValueError(
                    f"Invalid value for environment variable {env_key!r} targeting field "
                    f"{field_name!r}. Provide a plain scalar accepted by the field type or "
                    "a valid JSON literal/object for structured values."
                ) from json_exc

    return model_cls.model_validate(payload)


class SqlDatabaseSettings(BaseSettings):
    """Environment-backed defaults for SQLAlchemy resource configuration."""

    model_config = SettingsConfigDict(
        env_prefix="DB_",
        env_file=None,
        env_ignore_empty=True,
        extra="ignore",
    )

    connection_url: Optional[SecretStr] = Field(default=None)
    query_only: bool = Field(default=True)
    worker_connection_env_var: Optional[str] = Field(default=None)
    pool_size: int = Field(default=5, ge=0)
    max_overflow: int = Field(default=10, ge=0)
    pool_timeout: int = Field(default=30, ge=0)
    pool_recycle: int = Field(default=1800)
    pool_pre_ping: bool = Field(default=True)
    connect_args: dict[str, Any] = Field(default_factory=dict)
    execution_options: dict[str, Any] = Field(default_factory=dict)


class FilesystemSettings(BaseModel):
    """Typed environment-backed settings for filesystem connection profiles."""

    fs_type: str = Field(default="file")
    fs_path: str = Field(..., min_length=1)
    fs_key: Optional[str] = Field(default=None)
    fs_secret: Optional[SecretStr] = Field(default=None)
    fs_endpoint: Optional[str] = Field(default=None)
    fs_token: Optional[SecretStr] = Field(default=None)
    fs_region: str = Field(default="us-east-1")
    fs_verify_ssl: bool = Field(default=True)
    fs_options: dict[str, Any] = Field(default_factory=dict)
