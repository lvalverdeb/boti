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

Python es el lenguaje dominante para ingeniería de datos, automatización y ML — pero el camino del notebook exploratorio al pipeline de producción está lleno de modos de fallo bien documentados:

- **Los notebooks no se despliegan.** Los notebooks Jupyter fomentan la ejecución no lineal de celdas, el estado global implícito y la lógica de configuración improvisada. Todo equipo de ciencia de datos se enfrenta al mismo obstáculo: el prototipo funciona, pero el camino a producción no está claro. [Los estudios muestran que una minoría de proyectos de ML llegan a producción](https://mljourney.com/notebook-to-pipeline-taking-ml-from-jupyter-to-production/), y los notebooks en pipelines CI/CD fallan un 40% más a menudo que el código modular.
- **Las fugas de recursos son la norma.** La sentencia `with` de Python es el estándar de oro, pero en la práctica las bases de código complejas con múltiples tipos de recursos (sistemas de archivos, clientes, conexiones) siguen acumulando abstracciones con fugas.
- **Las vulnerabilidades de path traversal están activas y sin parche.** Solo en 2025-2026, se divulgaron CVEs críticos de path traversal en MindsDB (CVE-2025-68472), setuptools (CVE-2025-47273), el propio módulo tarfile de Python (CVE-2025-4330) y Werkzeug (CVE-2024-49766). La mayoría de los servicios Python que manejan rutas de archivo no tienen ningún sandbox.
- **`multiprocessing.Pool` falla en los patrones más simples.** `PicklingError` es la pregunta de multiprocessing más frecuente en StackOverflow. Enviar configuración a procesos worker requiere un control manual del pickle que la mayoría de los equipos hace mal.
- **La carga de entorno está duplicada en cada script.** La detección de la raíz del proyecto, la carga de `.env` y la configuración específica del entorno se reimplementan de forma improvisada en cada notebook y script — a menudo con diferencias sutiles.
- **Los pipelines de datos acumulan deuda técnica.** Con el tiempo, la limpieza improvisada, las rutas hardcodeadas y el logging inconsistente convierten pipelines fiables en sistemas frágiles e intocables.

`boti` proporciona a esos proyectos un pequeño conjunto de **primitivas de ejecución con opinión** para que el mismo código pueda moverse limpiamente entre desarrollo local, automatización y flujos de trabajo en producción sin reinventar la misma infraestructura.

## Por qué `boti` es útil

`boti` es útil cuando quieres que tu código de transformación se comporte como un componente de software real en lugar de una colección de scripts de un solo uso.

Ayuda mediante:

- estandarización del ciclo de vida de recursos con `ManagedResource`
- acceso explícito y restringido a archivos con `SecureResource`
- centralización del descubrimiento de raíz de proyecto y entorno con `ProjectService`
- un modelo de logging compartido para el código base con `Logger`

Esto es especialmente valioso cuando múltiples equipos o notebooks interactúan con el mismo código base, porque reduce las suposiciones implícitas y hace el comportamiento más predecible.

## Casos de uso reales

### Migración de notebooks a producción

El camino más común a Boti: un equipo tiene notebooks funcionales repartidos entre colaboradores, pero el código se ejecuta de forma diferente en cada máquina. `ManagedResource` proporciona hooks de ciclo de vida deterministas, `ProjectService` ancla cada entorno a la misma raíz del proyecto, y `Logger` ofrece diagnósticos consistentes. El mismo código ahora se ejecuta en un notebook, un pipeline CI/CD y un trabajo por lotes programado — sin cambios.

### Acceso seguro a archivos en servicios multi-tenant

Los servicios que aceptan rutas de archivo de entrada del usuario o APIs externas son una fuente recurrente de CVEs (CVE-2025-68472 en MindsDB, CVE-2025-47273 en setuptools, CVE-2025-4330 en tarfile de Python). `SecureResource` resuelve cada ruta a su forma canónica y rechaza cualquier cosa fuera del sandbox configurado. Esto es defensa en profundidad que detecta bugs de path traversal independientemente de cómo se construyó la ruta.

### Trabajos multiprocesamiento

Enviar configuración de recursos a un worker de `multiprocessing.Pool` es uno de los puntos débiles más comunes de Python. El doble gateo de pickle de Boti (`allow_pickle` en la configuración + variable de entorno `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE`) permite serializar un recurso y enviarlo a un worker sin shims manuales de pickle. El hook `_restore_runtime_state` restablece las conexiones transitorias en el lado del worker.

### Pipelines ETL con almacenamiento híbrido local/remoto

Los trabajos ETL que cambian entre archivos locales, S3, GCS y espacio temporal en memoria se benefician de la abstracción `FilesystemConfig` de Boti. Una única configuración tipada controla la conexión, y `ManagedResource._ensure_fs()` proporciona acceso lazy y thread-safe al sistema de archivos bajo demanda.

### Desarrollo de bibliotecas compartidas

Cuando múltiples paquetes internos o equipos dependen de la misma lógica de transformación, Boti impone un contrato consistente: cada recurso se abre y cierra de la misma manera, cada acceso a archivos se valida, cada entorno se descubre desde la misma raíz. Esto elimina el problema de "funciona en mi máquina" a nivel arquitectónico.

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

## Más documentación

- [`examples/`](examples/)

  **Ciclo de vida — recursos, limpieza y control de pickle**

  - [`simple_resource.py`](examples/simple_resource.py) — `ManagedResource` mínimo con `_cleanup` síncrono, uso de context-manager, idempotencia de cierre, detección de fugas GC mediante `weakref.finalize` y `_restore_runtime_state()`.
  - [`filesystem_resource.py`](examples/filesystem_resource.py) — subclase de `ManagedResource` respaldada por un sistema de archivos fsspec. Muestra `require_fs()`, inicialización lazy del sistema de archivos y limpieza.
  - [`async_resource.py`](examples/async_resource.py) — `ManagedResource` con `_acleanup` nativo para limpieza asíncrona sin fallback síncrono.
  - [`managed_resource_pickle.py`](examples/managed_resource_pickle.py) — denegación de pickle por defecto, gestor de contexto `trusted_unpickle_scope()`, `_restore_runtime_state()` tras la deserialización y la variable de entorno `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE`.

  **Abstracciones del sistema de archivos**

  - [`filesystem_config.py`](examples/filesystem_config.py) — `FilesystemConfig` para backends local, en memoria y compatible con S3 usando `create_filesystem()` y `FilesystemAdapter`.
  - [`filesystem_from_env.py`](examples/filesystem_from_env.py) — `FilesystemConfig.from_settings()` y `from_env_prefix()` para construir perfiles tipados del sistema de archivos desde variables de entorno o modelos pydantic.
  - [`filesystem_pyarrow.py`](examples/filesystem_pyarrow.py) — integración PyArrow: `FilesystemAdapter.get_pyarrow_filesystem()`, lectura/escritura de Parquet a través de fsspec y comportamiento de caché del adaptador.
  - [`filesystem_supported_backends.py`](examples/filesystem_supported_backends.py) — prueba a nivel de constructor para cada backend fsspec que soporta boti. Útil para identificar rápidamente paquetes de drivers opcionales faltantes.

  **Registro (logging)**

  - [`logger.py`](examples/logger.py) — `Logger` con manejo seguro de archivos, redacción de PII (contraseñas, tokens, claves API), logging estructurado, fábrica `default_logger()` con caché LRU y loggers por espacio de nombres.
  - [`logger_runtime.py`](examples/logger_runtime.py) — listener de fondo `LoggerRuntime`, logging multi-destino (archivo + stderr), `SafeRotatingFileHandler` y apagado graceful.

  **Seguridad — E/S en sandbox y validación**

  - [`secure_resource.py`](examples/secure_resource.py) — sandbox `SecureResource` con `read_text_secure`, `write_text_secure`, `open_secure`, rechazo de path traversal, `extra_allowed_paths` y detección de symlinks.
  - [`security_extended.py`](examples/security_extended.py) — `validate_environment_bindings()`, `is_valid_env_var_name()`, `is_valid_identifier()`, `is_valid_dotted_identifier()` y casos extremos de `is_secure_path()`.

  **Descubrimiento de proyecto y entorno**

  - [`project_environment.py`](examples/project_environment.py) — `ProjectService.detect_project_root()` y carga de archivos `.env` con `setup_environment()`.
  - [`project_service_runtime.py`](examples/project_service_runtime.py) — uso de `ProjectService` centrado en tiempo de ejecución: detección de raíz de servicio, carga de un archivo runtime.env y lectura de valores de configuración.
  - [`settings.py`](examples/settings.py) — modelos de configuración tipados (`SqlDatabaseSettings`, `FilesystemSettings`), `load_prefixed_model()`, `load_dotenv_values()` y precedencia de anulación dotenv vs entorno de proceso.

  **Pipeline integral**

  - [`end_to_end_pipeline.py`](examples/end_to_end_pipeline.py) — combina `ProjectService`, `SecureResource`, `Logger`, `FilesystemAdapter` y `ManagedResource` en un solo flujo de trabajo: detección de raíz de proyecto, carga de `.env`, entrada en sandbox, procesamiento de registros con logging estructurado y salida a almacenamiento gestionado.

  **ETL concurrente y paralelo**

  - [`etl_multiprocessing_pool.py`](examples/etl_multiprocessing_pool.py) — ETL distribuido con `multiprocessing.Pool`. Serializa un `ManagedResource` con `allow_pickle=True`, lo envía a workers mediante `pool.map` y llama a `_restore_runtime_state()` en el lado del worker. Útil para dividir un conjunto de datos grande en lotes y procesarlos en paralelo sin reconstruir la configuración por worker.
  - [`etl_concurrent_threads.py`](examples/etl_concurrent_threads.py) — ETL con hilos y `ThreadPoolExecutor`. Múltiples tareas de pipeline (pedidos, devoluciones, reembolsos) se ejecutan concurrentemente, cada una leyendo de un `FilesystemAdapter` compartido, transformando registros y registrando el progreso por tarea. Muestra acceso thread-safe a `ManagedResource` y logging estructurado con contexto de fase.
  - [`etl_async_pipeline.py`](examples/etl_async_pipeline.py) — pipeline ETL asíncrono con `asyncio.gather`. Las fuentes (usuarios, productos, eventos) se extraen, transforman y cargan concurrentemente usando async/await. El hook nativo `_acleanup` garantiza una limpieza adecuada. Útil para cargas de trabajo ligadas a E/S donde `asyncio` ofrece mayor concurrencia que los hilos.
  - [`etl_concurrent_multiple.py`](examples/etl_concurrent_multiple.py) — múltiples fuentes ETL concurrentes con aislamiento de errores. Un solo `ManagedResource` gestiona dos adaptadores de sistema de archivos (archivo + memoria) y procesa datos de ventas, inventario y analítica en paralelo mediante `ThreadPoolExecutor`. El manejo de errores por fuente significa que un fallo no detiene a los demás.

  **Perfiles de rendimiento**

  - [`profile_logger_load.py`](examples/profile_logger_load.py) — benchmark de rendimiento integral del Logger bajo carga monohilo y concurrente. Mide registros limpios, registros con muchos PII y patrones de contención de hilos.
  - [`profile_path_validation.py`](examples/profile_path_validation.py) — benchmark masivo de `is_secure_path()`, `is_valid_identifier()` y `is_valid_dotted_identifier()`. Revela el coste de `Path.resolve()` (ligado a syscalls) frente a la validación pura con regex (ligada a CPU).
  - [`profile_pii_redaction.py`](examples/profile_pii_redaction.py) — benchmark del hot-path `PIISecretFilter.filter()` con cargas útiles profundamente anidadas y con muchos PII para estresar el recorrido recursivo y la lógica de escaneo de cadenas.

## Desarrollo

Ejecuta las pruebas con el intérprete del proyecto:

```bash
uv run pytest
```
