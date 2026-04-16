# boti

[English](README.md) · [Español](README.es.md) · [Français](README.fr.md)

`boti` son las siglas de **Base Object Transformation Interface** (Interfaz Base de Transformación de Objetos).

Es una biblioteca de Python para construir **software de transformación fiable y reutilizable**: scripts, servicios, pipelines de datos, trabajos por lotes, ayudantes para notebooks y herramientas internas que comparten las mismas bases operativas.

En esencia, `boti` proporciona a tu código de transformación un modelo de ejecución coherente:

- cómo se abren y cierran los recursos
- cómo se restringe y valida el acceso a archivos
- cómo los proyectos descubren su raíz y su configuración de ejecución
- cómo se emiten los logs de forma predecible

## Qué problema resuelve `boti`

Mucho código de datos y automatización empieza siendo pequeño y rápidamente se vuelve operativamente caótico:

- lógica de inicio y cierre improvisada
- manejo duplicado de rutas y archivos
- carga de variables de entorno dispersa entre scripts y notebooks
- logging diagnóstico inconsistente
- suposiciones frágiles sobre desde dónde se ejecuta el código

Esto suele llevar a código que funciona en un notebook o una máquina, pero que se vuelve inestable al reutilizarse en pipelines, servicios empaquetados, bibliotecas compartidas o trabajos programados.

`boti` proporciona a esos proyectos un pequeño conjunto de **primitivas de ejecución con opinión** para que el mismo código pueda moverse más limpiamente entre desarrollo local, automatización y flujos de trabajo en producción.

## Por qué `boti` es útil

`boti` es útil cuando quieres que tu código de transformación se comporte como un componente de software real en lugar de una colección de scripts de un solo uso.

Ayuda mediante:

- estandarización del ciclo de vida de recursos con `ManagedResource`
- acceso explícito y restringido a archivos con `SecureResource`
- centralización del descubrimiento de raíz de proyecto y entorno con `ProjectService`
- un modelo de logging compartido para el código base con `Logger`

Esto es especialmente valioso cuando múltiples equipos o notebooks interactúan con el mismo código base, porque reduce las suposiciones implícitas y hace el comportamiento más predecible.

## Paquetes

### Paquete principal

```bash
pip install boti
```

Importaciones principales:

```python
from boti import Logger, ManagedResource, ProjectService, SecureResource
from boti.core import is_secure_path
```

También puedes importar directamente desde `boti.core`:

```python
from boti.core import Logger, ManagedResource, ProjectService, SecureResource
```

## Inicio rápido

### Recurso gestionado

```python
from boti import ManagedResource


class MiRecurso(ManagedResource):
    def _cleanup(self) -> None:
        print("limpiando")


with MiRecurso() as resource:
    print(resource.closed)  # False
```

### Configuración del sistema de archivos

`FilesystemConfig` proporciona una forma tipada de describir dónde debe leer y escribir datos un recurso. Usa `fsspec` internamente, por lo que `boti` puede trabajar con el sistema de archivos local, almacenamiento de objetos compatible con S3 y cualquier otro backend soportado por los drivers instalados de `fsspec`.

#### Archivos locales

```python
from boti.core.filesystem import FilesystemConfig, create_filesystem

config = FilesystemConfig(
    fs_type="file",
    fs_path="/srv/boti/data",
)

fs = create_filesystem(config)
with fs.open("/srv/boti/data/ejemplo.txt", "w") as handle:
    handle.write("hola")
```

#### Conexiones con servidor S3

Usa este patrón al conectarte a AWS S3 o a un servidor compatible con S3 como MinIO, Ceph u otro endpoint de almacenamiento de objetos interno.

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

`fs_endpoint` apunta al servidor S3, mientras que `fs_path` identifica el bucket y el prefijo con el que quieres trabajar.

#### Otros sistemas de archivos soportados

Cualquier backend reconocido por el stack de `fsspec` instalado puede usarse a través de `fs_type`. Ejemplos comunes:

- `memory` para pruebas y flujos de trabajo efímeros
- `gcs` para Google Cloud Storage
- `az` o `abfs` para almacenamiento de Azure
- `ftp`, `sftp` o `http` donde esté instalado el driver correspondiente

```python
from boti.core.filesystem import FilesystemConfig

memory_config = FilesystemConfig(fs_type="memory", fs_path="scratch")
gcs_config = FilesystemConfig(fs_type="gcs", fs_path="my-bucket/datasets")
azure_config = FilesystemConfig(fs_type="az", fs_path="container/path")
```

### Servicio de proyecto

```python
from boti import ProjectService

project_root = ProjectService.detect_project_root()
env_file = ProjectService.setup_environment(project_root)
```

### Acceso seguro a archivos

`SecureResource` envuelve las operaciones de archivos en un sandbox. Por defecto permite rutas bajo la raíz del proyecto detectada y el directorio temporal del sistema, y puedes agregar rutas de confianza adicionales de forma explícita.

```python
from pathlib import Path

from boti import SecureResource
from boti.core.models import ResourceConfig

config = ResourceConfig(project_root=Path.cwd())

with SecureResource(config=config) as resource:
    contents = resource.read_text_secure("README.md")
```

#### Permitir un directorio de confianza adicional

```python
from pathlib import Path

from boti import SecureResource
from boti.core.models import ResourceConfig

config = ResourceConfig(
    project_root=Path("/workspace/proyecto"),
    extra_allowed_paths=[Path("/srv/shared/datos-referencia")],
)

with SecureResource(config=config) as resource:
    reference = resource.read_text_secure("/srv/shared/datos-referencia/lookup.csv")
```

#### Bloquear rutas no seguras

```python
from pathlib import Path

from boti import SecureResource
from boti.core.models import ResourceConfig

config = ResourceConfig(project_root=Path("/workspace/proyecto"))

with SecureResource(config=config) as resource:
    try:
        resource.read_text_secure("/etc/passwd")
    except PermissionError:
        print("fuera de los sandbox configurados")
```

### Logger

`Logger` proporciona una capa de logging no bloqueante y segura para hilos, con manejo seguro de archivos y redacción de datos sensibles.

#### Logger rápido

```python
from pathlib import Path

from boti import Logger

logger = Logger.default_logger(
    logger_name="tarea_diaria",
    log_file="tarea_diaria",
    base_dir=Path("/workspace/proyecto"),
)

logger.info("iniciando extracción")
logger.warning("reintentando tras error transitorio")
```

#### Configuración explícita del logger

```python
from pathlib import Path

from boti.core.logger import Logger
from boti.core.models import LoggerConfig

config = LoggerConfig(
    log_dir=Path("/workspace/proyecto/logs"),
    logger_name="etl.pipeline",
    log_file="etl_pipeline",
    verbose=True,
)

logger = Logger(config)
logger.set_level(Logger.INFO)
logger.info("filas cargadas=%s", 1200)
```

### Subclasificación de `ManagedResource`

`ManagedResource` soporta patrones de limpieza síncronos y asíncronos, por lo que los recursos personalizados pueden exponer el mismo contrato de ciclo de vida tanto si envuelven sistemas de archivos, clientes, sockets u otro estado de ejecución.

#### Recurso síncrono

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
    resource.write_text("memory://ejemplo.txt", "hola desde fsspec")
    print(resource.read_text("memory://ejemplo.txt"))
```

#### Recurso asíncrono

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

Si una subclase solo implementa `_cleanup()`, `await resource.aclose()` recurrirá a ejecutar la limpieza síncrona de forma segura.

### Recursos serializables (pickle)

Por defecto, `ManagedResource` se niega a ser serializado con pickle. La serialización es una opción explícita que solo debes activar cuando tanto el sitio de serialización como el de deserialización están en entornos de ejecución que controlas.

Esto es útil cuando necesitas distribuir trabajo entre procesos o máquinas y quieres llevar la configuración del recurso — parámetros de conexión, rutas, configuraciones operativas — junto a la tarea en lugar de reconstruirla desde cero en cada worker.

Casos de uso típicos:

- **multiprocessing** — enviar un recurso configurado a un worker de `Pool`
- **computación distribuida** — enviar configuración de recursos a workers de Dask, Ray o Spark
- **colas de tareas** — guardar el estado de recursos entre tareas de Celery o RQ

#### Cómo funciona la activación

Hay dos compuertas independientes que deben estar abiertas para que la serialización funcione:

1. `allow_pickle=True` en el `ResourceConfig` del recurso — establecido en el momento de construcción, viaja con el payload serializado
2. La variable de entorno `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE=1` presente en el proceso worker en el momento de la deserialización

Este diseño de doble factor significa que un recurso serializado no puede cargarse silenciosamente en un entorno que no haya sido configurado explícitamente para confiar en él.

#### Qué se conserva y qué no

Cuando se serializa un recurso, `ManagedResource` elimina automáticamente el estado que no puede cruzar un límite de proceso:

- locks de hilos y locks de asyncio (recreados en el otro lado)
- el finalizador (reenganchado en el otro lado)
- la instancia del logger (reconstruida desde la configuración en el otro lado)
- el handle del sistema de archivos en vivo y la factory (borrados; ver `_restore_runtime_state` más abajo)

Los valores de configuración como los campos de `ResourceConfig` y cualquier atributo de subclase que sea serializable se conservan intactos.

#### Ejemplo básico

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


# --- lado de serialización ---
config = ResourceConfig(allow_pickle=True)
resource = ReportResource(output_dir=Path("/srv/informes"), config=config)

payload = pickle.dumps(resource)
resource.close()

# --- lado de deserialización (proceso worker) ---
with ManagedResource.trusted_unpickle_scope():
    restored = pickle.loads(payload)

print(restored.output_dir)  # /srv/informes
print(restored.closed)      # False
restored.close()
```

`trusted_unpickle_scope()` es un gestor de contexto que establece `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE=1` durante su duración y restaura el valor original al salir. Úsalo en el punto de entrada del worker en lugar de establecer la variable globalmente cuando sea posible.

#### Reconstrucción de conexiones transitorias tras la deserialización

Si tu recurso mantiene un objeto de conexión activo — una sesión de base de datos, un cliente HTTP, un handle de archivo abierto — esa conexión no sobrevivirá la serialización. Sobreescribe `_restore_runtime_state()` para restablecerla en el lado del worker.

```python
import pickle
from pathlib import Path

from boti import ManagedResource
from boti.core.models import ResourceConfig


class CsvResource(ManagedResource):
    def __init__(self, data_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.data_dir = data_dir
        self._handle = None  # abierto de forma lazy o restaurado tras la deserialización

    def _restore_runtime_state(self) -> None:
        # Llamado automáticamente por __setstate__ tras deserializar el objeto.
        # Reabre conexiones o reinicializa estado que no puede transferirse.
        self._handle = None  # se abrirá en el primer uso

    def read(self, filename: str) -> str:
        path = self.data_dir / filename
        with open(path) as f:
            return f.read()

    def _cleanup(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


# --- proceso principal: crear y serializar ---
config = ResourceConfig(allow_pickle=True)
resource = CsvResource(data_dir=Path("/srv/datos"), config=config)
payload = pickle.dumps(resource)
resource.close()

# --- proceso worker: restaurar y usar ---
with ManagedResource.trusted_unpickle_scope():
    worker_resource = pickle.loads(payload)

with worker_resource:
    content = worker_resource.read("resumen.csv")
    print(content)
```

#### Uso con multiprocessing

El uso más común es enviar la configuración de recursos a un pool de workers. Establece la variable de entorno en el inicializador del worker para que esté presente antes de que cualquier tarea deserialice un recurso.

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
    resource = WorkerResource(data_dir=Path("/srv/datos"), config=config)
    payload = pickle.dumps(resource)
    resource.close()

    with multiprocessing.Pool(initializer=worker_init) as pool:
        sizes = pool.starmap(run_task, [(payload, f) for f in ["a.bin", "b.bin"]])

    print(sizes)
```

#### Nota de seguridad

Activa `allow_pickle` solo cuando controles ambos extremos del canal de serialización. Deserializar datos de fuentes no confiables puede ejecutar código arbitrario. La variable de entorno `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE` es la última línea de defensa: no la establezcas globalmente en entornos que procesen datos de fuentes externas.

## Más documentación específica de paquetes

- [`packages/boti/README.md`](packages/boti/README.md)
- [`examples/`](examples/)
- [`docs/`](docs/)

## Desarrollo

Ejecuta las pruebas con el intérprete del proyecto:

```bash
PYTHONPATH=src python -m pytest -q
```
