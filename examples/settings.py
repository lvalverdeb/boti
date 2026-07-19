"""
Settings examples: SqlDatabaseSettings, FilesystemSettings, load_prefixed_model.

Demonstrates:
  - SqlDatabaseSettings with DB_* environment variables
  - FilesystemSettings for typed filesystem connection profiles
  - load_prefixed_model() to load any pydantic model from prefixed env vars
  - load_dotenv_values() for raw dotenv parsing
  - Environment variable override precedence (dotenv < process env)
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from boti.core import SqlDatabaseSettings, load_prefixed_model
from boti.core.settings import FilesystemSettings, load_dotenv_values


def example_sql_database_settings() -> None:
    """SqlDatabaseSettings loads DB_*-prefixed vars from the environment."""
    # Set some DB_ prefixed env vars
    os.environ["DB_CONNECTION_URL"] = "postgresql://user:pass@localhost:5432/mydb"
    os.environ["DB_POOL_SIZE"] = "10"
    os.environ["DB_QUERY_ONLY"] = "false"

    settings = SqlDatabaseSettings()
    print(f"  connection_url: {settings.connection_url}")
    print(f"  pool_size:      {settings.pool_size}")
    print(f"  query_only:     {settings.query_only}")
    print()

    # Cleanup
    for k in ("DB_CONNECTION_URL", "DB_POOL_SIZE", "DB_QUERY_ONLY"):
        os.environ.pop(k, None)


def example_filesystem_settings() -> None:
    """FilesystemSettings provides typed filesystem config from env."""
    os.environ["MY_FS_TYPE"] = "memory"
    os.environ["MY_FS_PATH"] = "my-bucket/data"

    settings = load_prefixed_model(FilesystemSettings, "MY_")
    print(f"  fs_type: {settings.fs_type}")
    print(f"  fs_path: {settings.fs_path}")
    print(f"  fs_verify_ssl: {settings.fs_verify_ssl}")
    print()

    for k in ("MY_FS_TYPE", "MY_FS_PATH"):
        os.environ.pop(k, None)


def example_load_prefixed_model() -> None:
    """Load any pydantic model from prefixed env vars with type coercion."""
    from pydantic import BaseModel

    class AppConfig(BaseModel):
        host: str = "localhost"
        port: int = 8080
        debug: bool = False
        tags: list[str] = []

    os.environ["APP_HOST"] = "0.0.0.0"
    os.environ["APP_PORT"] = "3000"
    os.environ["APP_DEBUG"] = "true"
    os.environ["APP_TAGS"] = '["api", "production"]'

    config = load_prefixed_model(AppConfig, "APP_")
    print(f"  host:  {config.host}")
    print(f"  port:  {config.port}")
    print(f"  debug: {config.debug}")
    print(f"  tags:  {config.tags}")
    print()

    for k in ("APP_HOST", "APP_PORT", "APP_DEBUG", "APP_TAGS"):
        os.environ.pop(k, None)


def example_load_dotenv_values() -> None:
    """Parse a .env file without polluting os.environ."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        env_file = Path(tmp_dir) / ".env"
        env_file.write_text(
            "DATABASE_URL=postgresql://localhost:5432/db\nLOG_LEVEL=debug\nFEATURE_FLAG_X=true\n"
        )

        values = load_dotenv_values(env_file)
        print(f"  DATABASE_URL:  {values['DATABASE_URL']}")
        print(f"  LOG_LEVEL:     {values['LOG_LEVEL']}")
        print(f"  FEATURE_FLAG_X: {values['FEATURE_FLAG_X']}")
    print()


def example_env_override_precedence() -> None:
    """Process env vars take precedence over dotenv values."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        env_file = Path(tmp_dir) / ".env"
        env_file.write_text("DB_CONNECTION_URL=postgresql://dotenv:5432/db\n")
        os.environ["DB_CONNECTION_URL"] = "postgresql://process:5432/db"

        settings = load_prefixed_model(SqlDatabaseSettings, "DB_", env_file=env_file)
        print(f"  connection_url (process env wins): {settings.connection_url}")

        os.environ.pop("DB_CONNECTION_URL", None)
    print()


def main() -> None:
    print("=== Settings examples ===\n")
    example_sql_database_settings()
    example_filesystem_settings()
    example_load_prefixed_model()
    example_load_dotenv_values()
    example_env_override_precedence()


if __name__ == "__main__":
    main()
