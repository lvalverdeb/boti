"""
SqlDatabaseSettings connection-pool tuning and multi-worker patterns.

Demonstrates:
  - Pool sizing knobs (pool_size, max_overflow, pool_timeout, pool_recycle,
    pool_pre_ping) loaded from DB_*-prefixed environment variables
  - Assembling the kwargs dict a caller would pass to
    sqlalchemy.create_engine(url, **kwargs) — without requiring sqlalchemy
    to be installed
  - worker_connection_env_var for letting each multiprocessing worker resolve
    its own connection string indirectly (e.g. per-pod credentials)
  - connect_args / execution_options passthrough as JSON env values
  - query_only defaulting to True as a safe-by-default guard
"""

from __future__ import annotations

import os

from boti.core import SqlDatabaseSettings


def example_pool_tuning_from_env() -> None:
    """Pool knobs are read straight from DB_*-prefixed environment variables."""
    os.environ.update({
        "DB_CONNECTION_URL": "postgresql://user:pass@localhost:5432/mydb",
        "DB_POOL_SIZE": "20",
        "DB_MAX_OVERFLOW": "5",
        "DB_POOL_TIMEOUT": "10",
        "DB_POOL_RECYCLE": "900",
        "DB_POOL_PRE_PING": "true",
    })
    try:
        settings = SqlDatabaseSettings()
        engine_kwargs = {
            "pool_size": settings.pool_size,
            "max_overflow": settings.max_overflow,
            "pool_timeout": settings.pool_timeout,
            "pool_recycle": settings.pool_recycle,
            "pool_pre_ping": settings.pool_pre_ping,
        }
        # sqlalchemy.create_engine(settings.connection_url.get_secret_value(), **engine_kwargs)
        print(f"  engine kwargs: {engine_kwargs}")
    finally:
        for key in (
            "DB_CONNECTION_URL", "DB_POOL_SIZE", "DB_MAX_OVERFLOW",
            "DB_POOL_TIMEOUT", "DB_POOL_RECYCLE", "DB_POOL_PRE_PING",
        ):
            os.environ.pop(key, None)
    print()


def example_worker_connection_env_var() -> None:
    """Each multiprocessing worker can resolve its own DSN indirectly."""
    os.environ["DB_WORKER_CONNECTION_ENV_VAR"] = "WORKER_DB_URL"
    # In a real deployment, each worker process would have a distinct
    # WORKER_DB_URL injected by the orchestrator (e.g. per-replica secrets).
    os.environ["WORKER_DB_URL"] = "postgresql://worker7:pass@replica-7:5432/mydb"
    try:
        settings = SqlDatabaseSettings()
        resolved_url = (
            os.environ.get(settings.worker_connection_env_var)
            if settings.worker_connection_env_var
            else None
        )
        print(f"  worker_connection_env_var: {settings.worker_connection_env_var!r}")
        print(f"  this worker's resolved DSN: {resolved_url!r}")
    finally:
        os.environ.pop("DB_WORKER_CONNECTION_ENV_VAR", None)
        os.environ.pop("WORKER_DB_URL", None)
    print()


def example_connect_args_and_execution_options() -> None:
    """Structured JSON env values populate connect_args / execution_options."""
    os.environ["DB_CONNECT_ARGS"] = '{"sslmode": "require", "connect_timeout": 5}'
    os.environ["DB_EXECUTION_OPTIONS"] = '{"isolation_level": "AUTOCOMMIT"}'
    try:
        settings = SqlDatabaseSettings()
        print(f"  connect_args:       {settings.connect_args}")
        print(f"  execution_options:  {settings.execution_options}")
    finally:
        os.environ.pop("DB_CONNECT_ARGS", None)
        os.environ.pop("DB_EXECUTION_OPTIONS", None)
    print()


def example_query_only_default() -> None:
    """query_only defaults to True — an explicit opt-out is required for writes."""
    settings = SqlDatabaseSettings()
    print(f"  query_only default: {settings.query_only} (safe-by-default for read paths)")
    print()


def main() -> None:
    print("=== SqlDatabaseSettings pool & worker examples ===\n")
    example_pool_tuning_from_env()
    example_worker_connection_env_var()
    example_connect_args_and_execution_options()
    example_query_only_default()


if __name__ == "__main__":
    main()
