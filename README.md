# boti

[English](README.md) · [Español](README.es.md) · [Français](README.fr.md)

`boti` stands for **Base Object Transformation Interface**.

It is a Python library for building **reliable, reusable transformation-oriented software**: scripts, services, data pipelines, batch jobs, notebook helpers, and internal tooling that all need the same operational foundations.

At its core, `boti` is about giving transformation code a consistent runtime model:

- how resources are opened and closed
- how file access is constrained and validated
- how projects discover their root and runtime configuration
- how logs are emitted in a predictable way

## What problem `boti` solves

Python is the dominant language for data engineering, automation, and ML — but the path from exploratory notebook to production pipeline is littered with well-documented failure modes:

- **Notebooks don't deploy.** Jupyter notebooks encourage non-linear cell execution, implicit global state, and ad-hoc setup logic. Every data science team faces the same roadblock: the prototype works, but the path to production is unclear. [Studies show a minority of ML projects ever reach production](https://mljourney.com/notebook-to-pipeline-taking-ml-from-jupyter-to-production/), and notebooks in CI/CD pipelines fail 40% more often than modular code.
- **Resource leaks are the norm.** Python's `with` statement is the gold standard, but in practice complex codebases with multiple resource types (filesystems, clients, connections) still accumulate leaky abstractions. The python-docs community consistently ranks resource management as the #1 best practice violation.
- **Path traversal vulnerabilities are active and unpatched.** In 2025-2026 alone, critical path-traversal CVEs were disclosed in MindsDB (CVE-2025-68472), setuptools (CVE-2025-47273), Python's own tarfile module (CVE-2025-4330), and Werkzeug (CVE-2024-49766). Most Python services that handle file paths have no sandbox at all.
- **`multiprocessing.Pool` breaks on the simplest patterns.** `PicklingError` is the most-asked multiprocessing question on StackOverflow. Shipping configuration to worker processes requires manual pickle gating that most teams get wrong.
- **Environment loading is duplicated across every script.** Project-root detection, `.env` loading, and environment-specific configuration are re-implemented ad-hoc in every notebook and script — often subtly differently.
- **Data pipelines accumulate technical debt.** Over time, ad-hoc cleanup, hardcoded paths, and inconsistent logging turn reliable pipelines into brittle, untouchable systems.

`boti` gives those projects a small set of **opinionated runtime primitives** so the same code can move cleanly between local development, automation, and production workflows without re-inventing the same infrastructure.

## Why `boti` is useful

`boti` is useful when you want transformation code to behave like a real software component instead of a collection of one-off scripts.

It helps by:

- standardising resource lifecycle with `ManagedResource`
- making constrained file access explicit with `SecureResource`
- centralising project-root and environment discovery with `ProjectService`
- giving the codebase a shared logging model with `Logger`

This is especially valuable when multiple teams or notebooks interact with the same codebase, because it reduces hidden assumptions and makes behaviour more predictable.

## Real-world use cases

### Moving notebooks to production

The most common path to Boti: a team has working notebooks spread across contributors, but the code runs differently on each machine. `ManagedResource` provides deterministic lifecycle hooks, `ProjectService` anchors every environment to the same project root, and `Logger` gives consistent diagnostics. The same code now runs in a notebook, a CI pipeline, and a scheduled batch job — unchanged.

### Safe file access in multi-tenant services

Services that accept file paths from user input or external APIs are a recurring source of CVEs (CVE-2025-68472 in MindsDB, CVE-2025-47273 in setuptools, CVE-2025-4330 in Python tarfile). `SecureResource` resolves every path to its canonical form and rejects anything outside the configured sandbox. This is defense-in-depth that catches path-traversal bugs regardless of how the path was constructed.

### Multiprocessing workloads

Shipping resource configuration to a `multiprocessing.Pool` worker is one of Python's most common pain points. Boti's two-factor pickle gating (`allow_pickle` in config + `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE` env var) lets you serialize a resource and send it to a worker without manual pickle shims. The `_restore_runtime_state` hook re-establishes transient connections on the worker side.

### ETL pipelines with hybrid local/remote storage

ETL jobs that switch between local files, S3, GCS, and in-memory scratch space benefit from Boti's `FilesystemConfig` abstraction. A single typed config controls the connection, and `ManagedResource._ensure_fs()` provides lazy, thread-safe filesystem access on demand.

### Shared library development

When multiple internal packages or teams depend on the same transformation logic, Boti enforces a consistent contract: every resource opens and closes the same way, every file access is validated, every environment is discovered from the same root. This eliminates the "works on my machine" problem at the architectural level.

## Packages

### Core package

```bash
pip install boti
```

Core imports:

```python
from boti import Logger, ManagedResource, ProjectService, SecureResource
from boti.core import is_secure_path
```

You can also import from `boti.core` directly:

```python
from boti.core import Logger, ManagedResource, ProjectService, SecureResource
```

## Quick start

### Managed resource

```python
from boti import ManagedResource


class MyResource(ManagedResource):
    def _cleanup(self) -> None:
        print("cleaning up")


with MyResource() as resource:
    print(resource.closed)  # False
```

### Filesystem configuration

`FilesystemConfig` provides a typed way to describe where a resource should read and write data. It uses `fsspec` underneath, so `boti` can work with the local filesystem, S3-compatible object storage, and any other backend supported by your installed `fsspec` drivers.

#### Local files

```python
from boti.core.filesystem import FilesystemConfig, create_filesystem

config = FilesystemConfig(
    fs_type="file",
    fs_path="/srv/boti/data",
)

fs = create_filesystem(config)
with fs.open("/srv/boti/data/example.txt", "w") as handle:
    handle.write("hello")
```

#### S3 server connections

Use this pattern when connecting to AWS S3 or to an S3-compatible server such as MinIO, Ceph, or another internal object-storage endpoint.

```python
from boti.core.filesystem import FilesystemConfig, FilesystemAdapter

config = FilesystemConfig(
    fs_type="s3",
    fs_path="analytics-bucket/raw/events",
    fs_key="ACCESS_KEY",
    fs_secret="SECRET_KEY",
    fs_endpoint="https://minio.internal.example",
    fs_region="eu-west-1",
)

adapter = FilesystemAdapter(config)
fs = adapter.get_filesystem()

with fs.open("analytics-bucket/raw/events/2026-04-15.json", "rb") as handle:
    payload = handle.read()
```

`fs_endpoint` points at the S3 server, while `fs_path` identifies the bucket and prefix you want to work with.

#### Other supported filesystems

Any backend recognised by the installed `fsspec` stack can be used through `fs_type`. Common examples include:

- `memory` for tests and ephemeral workflows
- `gcs` for Google Cloud Storage
- `az` or `abfs` for Azure storage
- `ftp`, `sftp`, or `http` where the relevant driver is installed

```python
from boti.core.filesystem import FilesystemConfig

memory_config = FilesystemConfig(fs_type="memory", fs_path="scratch")
gcs_config = FilesystemConfig(fs_type="gcs", fs_path="my-bucket/datasets")
azure_config = FilesystemConfig(fs_type="az", fs_path="container/path")
```

### Project service

```python
from boti import ProjectService

project_root = ProjectService.detect_project_root()
env_file = ProjectService.setup_environment(project_root)
```

### Secure file access

`SecureResource` wraps file operations in a sandbox. By default it allows paths under the detected project root and the system temporary directory, and you can add extra allowlisted paths explicitly.

```python
from pathlib import Path

from boti import SecureResource
from boti.core.models import ResourceConfig

config = ResourceConfig(project_root=Path.cwd())

with SecureResource(config=config) as resource:
    contents = resource.read_text_secure("README.md")
```

#### Allow an additional trusted directory

```python
from pathlib import Path

from boti import SecureResource
from boti.core.models import ResourceConfig

config = ResourceConfig(
    project_root=Path("/workspace/project"),
    extra_allowed_paths=[Path("/srv/shared/reference-data")],
)

with SecureResource(config=config) as resource:
    reference = resource.read_text_secure("/srv/shared/reference-data/lookup.csv")
```

#### Block unsafe paths

```python
from pathlib import Path

from boti import SecureResource
from boti.core.models import ResourceConfig

config = ResourceConfig(project_root=Path("/workspace/project"))

with SecureResource(config=config) as resource:
    try:
        resource.read_text_secure("/etc/passwd")
    except PermissionError:
        print("outside the configured sandbox roots")
```

### Logger

`Logger` provides a thread-safe, non-blocking logging layer with secure file handling and sensitive-data redaction.

#### Quick logger

```python
from pathlib import Path

from boti import Logger

logger = Logger.default_logger(
    logger_name="daily_job",
    log_file="daily_job",
    base_dir=Path("/workspace/project"),
)

logger.info("starting extraction")
logger.warning("retrying after transient error")
```

#### Explicit logger configuration

```python
from pathlib import Path

from boti.core.logger import Logger
from boti.core.models import LoggerConfig

config = LoggerConfig(
    log_dir=Path("/workspace/project/logs"),
    logger_name="etl.pipeline",
    log_file="etl_pipeline",
    verbose=True,
)

logger = Logger(config)
logger.set_level(Logger.INFO)
logger.info("rows loaded=%s", 1200)
```

### Subclassing `ManagedResource`

`ManagedResource` supports both synchronous and asynchronous cleanup patterns, so custom resources can expose the same lifecycle contract whether they wrap filesystems, clients, sockets, or other runtime state.

#### Synchronous resource

```python
from boti import ManagedResource


class FilesystemResource(ManagedResource):
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
```

```python
import fsspec

resource = FilesystemResource(fs_factory=lambda: fsspec.filesystem("memory"))

with resource:
    resource.write_text("memory://example.txt", "hello from fsspec")
    print(resource.read_text("memory://example.txt"))
```

#### Asynchronous resource

```python
import asyncio

from boti import ManagedResource


class AsyncClientResource(ManagedResource):
    def __init__(self, client) -> None:
        super().__init__()
        self.client = client

    async def _acleanup(self) -> None:
        await self.client.aclose()


async def main(client) -> None:
    async with AsyncClientResource(client) as resource:
        await asyncio.sleep(0)
```

If a subclass only implements `_cleanup()`, `await resource.aclose()` will fall back to running the synchronous cleanup safely.

### Pickleable resources

By default, `ManagedResource` refuses to be pickled. Pickling is an explicit opt-in that you should only enable when both the serialization site and the deserialization site are in runtimes you control.

This is useful when you need to distribute work across processes or machines and want to carry resource configuration — connection parameters, paths, operational settings — alongside the task rather than re-building it from scratch in each worker.

Typical use cases:

- **multiprocessing** — sending a configured resource into a `Pool` worker
- **distributed computing** — shipping resource configuration to Dask, Ray, or Spark workers
- **task queues** — checkpointing resource state across Celery or RQ tasks

#### How the opt-in works

There are two independent gates that both must be open for pickling to work:

1. `allow_pickle=True` in the resource's `ResourceConfig` — set at construction time, travels with the pickled payload
2. The environment variable `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE=1` present in the worker process at unpickle time

This two-factor design means a serialized resource cannot be silently loaded in an environment that has not been explicitly configured to trust it.

#### What is and is not preserved

When a resource is pickled, `ManagedResource` automatically strips state that cannot cross a process boundary:

- thread locks and asyncio locks (recreated on the other side)
- the finalizer (reattached on the other side)
- the logger instance (rebuilt from config on the other side)
- the live filesystem handle and factory (cleared; see `_restore_runtime_state` below)

Configuration values such as `ResourceConfig` fields and any subclass attributes that are themselves pickleable are preserved intact.

#### Basic example

```python
import pickle
from pathlib import Path

from boti import ManagedResource
from boti.core.models import ResourceConfig


class ReportResource(ManagedResource):
    def __init__(self, output_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.output_dir = output_dir

    def _cleanup(self) -> None:
        pass


# --- serialization side ---
config = ResourceConfig(allow_pickle=True)
resource = ReportResource(output_dir=Path("/srv/reports"), config=config)

payload = pickle.dumps(resource)
resource.close()

# --- deserialization side (worker process) ---
with ManagedResource.trusted_unpickle_scope():
    restored = pickle.loads(payload)

print(restored.output_dir)  # /srv/reports
print(restored.closed)      # False
restored.close()
```

`trusted_unpickle_scope()` is a context manager that sets `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE=1` for its duration and restores the original value on exit. Use it at the worker entry point rather than setting the variable globally whenever possible.

#### Rebuilding transient connections after unpickling

If your resource holds a live connection object — a database session, an HTTP client, an open file handle — that connection will not survive pickling. Override `_restore_runtime_state()` to re-establish it on the worker side.

```python
import pickle
from pathlib import Path

from boti import ManagedResource
from boti.core.models import ResourceConfig


class CsvResource(ManagedResource):
    def __init__(self, data_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.data_dir = data_dir
        self._handle = None  # opened lazily or restored after unpickling

    def _restore_runtime_state(self) -> None:
        # Called automatically by __setstate__ after the object is unpickled.
        # Re-open connections or re-initialise state that cannot be transferred.
        self._handle = None  # will be opened on first use

    def read(self, filename: str) -> str:
        path = self.data_dir / filename
        with open(path) as f:
            return f.read()

    def _cleanup(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


# --- main process: create and pickle ---
config = ResourceConfig(allow_pickle=True)
resource = CsvResource(data_dir=Path("/srv/data"), config=config)
payload = pickle.dumps(resource)
resource.close()

# --- worker process: restore and use ---
with ManagedResource.trusted_unpickle_scope():
    worker_resource = pickle.loads(payload)

with worker_resource:
    content = worker_resource.read("summary.csv")
    print(content)
```

#### Using with multiprocessing

The most common use is sending resource configuration to a pool of workers. Set the environment variable in the worker initialiser so it is present before any task unpickles a resource.

```python
import os
import pickle
import multiprocessing
from pathlib import Path

from boti import ManagedResource
from boti.core.models import ResourceConfig


class WorkerResource(ManagedResource):
    def __init__(self, data_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.data_dir = data_dir

    def process(self, filename: str) -> int:
        return len((self.data_dir / filename).read_bytes())

    def _cleanup(self) -> None:
        pass


def worker_init():
    os.environ[ManagedResource._TRUSTED_UNPICKLE_ENV] = "1"


def run_task(payload: bytes, filename: str) -> int:
    resource = pickle.loads(payload)
    with resource:
        return resource.process(filename)


if __name__ == "__main__":
    config = ResourceConfig(allow_pickle=True)
    resource = WorkerResource(data_dir=Path("/srv/data"), config=config)
    payload = pickle.dumps(resource)
    resource.close()

    with multiprocessing.Pool(initializer=worker_init) as pool:
        sizes = pool.starmap(run_task, [(payload, f) for f in ["a.bin", "b.bin"]])

    print(sizes)
```

#### Security note

Enable `allow_pickle` only when you control both ends of the serialization channel. Unpickling data from untrusted sources can execute arbitrary code. The `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE` environment variable is the last line of defense: do not set it globally in environments that process data from external sources.

## More docs

- [`examples/`](examples/)

  **Lifecycle — resources, cleanup, and pickle gating**

  - [`simple_resource.py`](examples/simple_resource.py) — minimal `ManagedResource` with synchronous `_cleanup`, context-manager usage, close idempotency, GC leak detection via `weakref.finalize`, and `_restore_runtime_state()`.
  - [`filesystem_resource.py`](examples/filesystem_resource.py) — `ManagedResource` subclass backed by an fsspec filesystem. Shows `require_fs()`, lazy filesystem initialisation, and cleanup.
  - [`async_resource.py`](examples/async_resource.py) — `ManagedResource` with native `_acleanup` for asynchronous cleanup without a synchronous fallback.
  - [`managed_resource_pickle.py`](examples/managed_resource_pickle.py) — pickle denial by default, `trusted_unpickle_scope()` context manager, `_restore_runtime_state()` after unpickling, and the `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE` environment variable.

  **Filesystem abstractions**

  - [`filesystem_config.py`](examples/filesystem_config.py) — `FilesystemConfig` for local, in-memory, and S3-compatible backends using `create_filesystem()` and `FilesystemAdapter`.
  - [`filesystem_from_env.py`](examples/filesystem_from_env.py) — `FilesystemConfig.from_settings()` and `from_env_prefix()` to build typed filesystem profiles from environment variables or pydantic models.
  - [`filesystem_pyarrow.py`](examples/filesystem_pyarrow.py) — PyArrow integration: `FilesystemAdapter.get_pyarrow_filesystem()`, reading/writing Parquet through fsspec, and adapter caching behaviour.
  - [`filesystem_supported_backends.py`](examples/filesystem_supported_backends.py) — constructor-level test for every fsspec backend boti supports. Useful to quickly identify missing optional driver packages.

  **Logging**

  - [`logger.py`](examples/logger.py) — `Logger` with secure file handling, PII redaction (passwords, tokens, API keys), structured logging, `default_logger()` factory with LRU caching, and per-namespace loggers.
  - [`logger_runtime.py`](examples/logger_runtime.py) — `LoggerRuntime` background listener, multi-destination logging (file + stderr), `SafeRotatingFileHandler`, and graceful shutdown.

  **Security — sandboxed I/O and validation**

  - [`secure_resource.py`](examples/secure_resource.py) — `SecureResource` sandbox with `read_text_secure`, `write_text_secure`, `open_secure`, path-traversal rejection, `extra_allowed_paths`, and symlink detection.
  - [`security_extended.py`](examples/security_extended.py) — `validate_environment_bindings()`, `is_valid_env_var_name()`, `is_valid_identifier()`, `is_valid_dotted_identifier()`, and `is_secure_path()` edge cases.

  **Project and environment discovery**

  - [`project_environment.py`](examples/project_environment.py) — `ProjectService.detect_project_root()` and `.env` file loading with `setup_environment()`.
  - [`project_service_runtime.py`](examples/project_service_runtime.py) — runtime-focused use of `ProjectService`: detecting a service root, loading a runtime.env file, and reading config values.
  - [`settings.py`](examples/settings.py) — typed settings models (`SqlDatabaseSettings`, `FilesystemSettings`), `load_prefixed_model()`, `load_dotenv_values()`, and dotenv vs process-env override precedence.

  **End-to-end pipeline**

  - [`end_to_end_pipeline.py`](examples/end_to_end_pipeline.py) — combines `ProjectService`, `SecureResource`, `Logger`, `FilesystemAdapter`, and `ManagedResource` in a single workflow: project-root detection, `.env` loading, sandboxed input, record processing with structured logging, and managed-storage output.

  **Concurrent and parallel ETL**

  - [`etl_multiprocessing_pool.py`](examples/etl_multiprocessing_pool.py) — fan-out ETL across a `multiprocessing.Pool`. Pickles a `ManagedResource` with `allow_pickle=True`, ships it to workers via `pool.map`, and calls `_restore_runtime_state()` on the worker side. Useful for splitting a large dataset into batches and processing them in parallel without re-building configuration per worker.
  - [`etl_concurrent_threads.py`](examples/etl_concurrent_threads.py) — threaded ETL with `ThreadPoolExecutor`. Multiple pipeline tasks (orders, returns, refunds) run concurrently, each reading from a shared `FilesystemAdapter`, transforming records, and logging per-task progress. Shows thread-safe `ManagedResource` access and structured logging with phase-level context.
  - [`etl_async_pipeline.py`](examples/etl_async_pipeline.py) — async ETL pipeline with `asyncio.gather`. Sources (users, products, events) are extracted, transformed, and loaded concurrently using async/await. Native `_acleanup` hook ensures proper teardown. Useful for I/O-bound workloads where `asyncio` gives higher concurrency than threads.
  - [`etl_concurrent_multiple.py`](examples/etl_concurrent_multiple.py) — multiple concurrent ETL sources with error isolation. A single `ManagedResource` manages two filesystem adapters (file + memory) and processes sales, inventory, and analytics data in parallel via `ThreadPoolExecutor`. Per-source error handling means one failure does not stop the others.

  **Performance profiles**

  - [`profile_logger_load.py`](examples/profile_logger_load.py) — Logger end-to-end throughput benchmark under single-threaded and concurrent load. Measures clean records, PII-heavy records, and thread-contention patterns.
  - [`profile_path_validation.py`](examples/profile_path_validation.py) — bulk `is_secure_path()`, `is_valid_identifier()`, and `is_valid_dotted_identifier()` benchmark. Reveals the cost of `Path.resolve()` (syscall-bound) vs pure regex validation (CPU-bound).
  - [`profile_pii_redaction.py`](examples/profile_pii_redaction.py) — `PIISecretFilter.filter()` hot-path benchmark with deeply nested, PII-heavy payloads to stress recursive traversal and string-scanning logic.

## Development

Run tests with the project interpreter:

```bash
uv run pytest
```
