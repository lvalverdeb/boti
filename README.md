# boti

`boti` stands for **Base Object Transformation Interface**.

It is a Python library for building **reliable, reusable transformation-oriented software**: scripts, services, data pipelines, batch jobs, notebook helpers, and internal tooling that all need the same operational foundations.

At its core, `boti` is about giving transformation code a consistent runtime model:

- how resources are opened and closed
- how file access is constrained and validated
- how projects discover their root and runtime configuration
- how logs are emitted in a predictable way

The companion package **`boti-data`** extends that foundation with SQL, parquet, schema, gateway, and distributed data capabilities. It is a separate package with its own release cycle.

## What problem `boti` solves

A lot of data and automation code starts small and quickly becomes operationally messy:

- ad hoc setup and teardown logic
- duplicated path and file handling
- environment loading spread across scripts and notebooks
- inconsistent logging and diagnostics
- brittle assumptions about where code is running from

That usually leads to code that works in one notebook or one machine, but becomes fragile when reused in pipelines, packaged services, shared libraries, or scheduled jobs.

`boti` gives those projects a small set of **opinionated runtime primitives** so the same code can move more cleanly between local development, automation, and production workflows.

## Why `boti` is useful

`boti` is useful when you want transformation code to behave like a real software component instead of a collection of one-off scripts.

It helps by:

- standardising resource lifecycle with `ManagedResource`
- making constrained file access explicit with `SecureResource`
- centralising project-root and environment discovery with `ProjectService`
- giving the codebase a shared logging model with `Logger`

This is especially valuable when multiple teams or notebooks interact with the same codebase, because it reduces hidden assumptions and makes behaviour more predictable.

## What `boti-data` adds

`boti-data` is the data layer for the Boti ecosystem.

Where `boti` solves the runtime and application-structure problems, `boti-data` solves the **data access and data movement problems** that appear once teams need to work across databases, parquet files, schemas, and distributed workloads.

It provides:

- SQL database resources and session management
- SQLAlchemy model reflection and model registries
- connection catalogues for named data sources
- gateway-style loading APIs
- parquet resources and readers
- schema normalisation, validation, and field mapping
- filter expressions and join helpers
- partitioned and distributed loading workflows

In practice, it helps teams replace repetitive, hand-rolled access code with a consistent interface for loading, validating, shaping, and moving data.

## Where `boti-data` can make a big difference

`boti-data` is useful anywhere teams need to bridge operational systems and analytical workflows without rewriting the same infrastructure over and over.

It can be especially impactful in domains such as:

- **analytics engineering**: consistent loading from source systems into analysis-ready frames
- **business intelligence**: reusable connection catalogues, filters, and schema handling across reports
- **operations and supply chain**: joining transactional data from multiple systems with safer loading patterns
- **finance and risk**: explicit schemas, reproducible transformations, and controlled access to structured data
- **customer, product, and growth analytics**: repeatable extraction and normalisation across many upstream tables
- **ML and feature pipelines**: partitioned loads, parquet workflows, and predictable resource management
- **research and notebook-heavy teams**: moving from exploratory code to reusable library code without losing speed

The value is largest when data work sits in the gap between raw infrastructure and business logic: not just querying tables, but building maintainable, reusable data interfaces.

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

### Data package

`boti-data` is a separate package. Install it independently:

```bash
pip install boti-data
```

Data imports live under the separate top-level package:

```python
from boti_data import DataGateway, DataHelper, SqlDatabaseConfig, SqlDatabaseResource
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

## More docs

- [`examples/`](examples/)

## Development

Run tests with the project interpreter:

```bash
PYTHONPATH=src python -m pytest -q
```
