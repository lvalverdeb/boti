# boti

[English](README.md) · [Español](README.es.md) · [Français](README.fr.md)

`boti` est l'acronyme de **Base Object Transformation Interface** (Interface de Base pour la Transformation d'Objets).

C'est une bibliothèque Python pour construire des **logiciels de transformation fiables et réutilisables** : scripts, services, pipelines de données, traitements par lots, utilitaires pour notebooks et outillage interne qui partagent tous les mêmes fondations opérationnelles.

À son cœur, `boti` donne au code de transformation un modèle d'exécution cohérent :

- comment les ressources sont ouvertes et fermées
- comment l'accès aux fichiers est contraint et validé
- comment les projets découvrent leur racine et leur configuration d'exécution
- comment les logs sont émis de manière prévisible

## Quel problème `boti` résout

Python est le langage dominant pour l'ingénierie des données, l'automatisation et le ML — mais le chemin du notebook exploratoire au pipeline de production est jonché de modes de défaillance bien documentés :

- **Les notebooks ne se déploient pas.** Les notebooks Jupyter encouragent l'exécution non linéaire des cellules, l'état global implicite et la logique de configuration ad-hoc. Chaque équipe de science des données fait face au même obstacle : le prototype fonctionne, mais le chemin vers la production n'est pas clair. [Les études montrent qu'une minorité de projets ML atteignent la production](https://mljourney.com/notebook-to-pipeline-taking-ml-from-jupyter-to-production/), et les notebooks dans les pipelines CI/CD échouent 40% plus souvent que le code modulaire.
- **Les fuites de ressources sont la norme.** L'instruction `with` de Python est l'étalon-or, mais en pratique les bases de code complexes avec plusieurs types de ressources (systèmes de fichiers, clients, connexions) accumulent encore des abstractions qui fuient.
- **Les vulnérabilités de path traversal sont actives et non corrigées.** Rien qu'en 2025-2026, des CVEs critiques de path traversal ont été divulgués dans MindsDB (CVE-2025-68472), setuptools (CVE-2025-47273), le propre module tarfile de Python (CVE-2025-4330) et Werkzeug (CVE-2024-49766). La plupart des services Python qui manipulent des chemins de fichiers n'ont aucun sandbox.
- **`multiprocessing.Pool` échoue sur les motifs les plus simples.** `PicklingError` est la question multiprocessing la plus posée sur StackOverflow. Envoyer la configuration aux processus workers nécessite un contrôle manuel du pickle que la plupart des équipes font mal.
- **Le chargement de l'environnement est dupliqué dans chaque script.** La détection de la racine du projet, le chargement de `.env` et la configuration spécifique à l'environnement sont réimplémentés ad-hoc dans chaque notebook et script — souvent avec des différences subtiles.
- **Les pipelines de données accumulent de la dette technique.** Avec le temps, le nettoyage ad-hoc, les chemins codés en dur et la journalisation incohérente transforment des pipelines fiables en systèmes fragiles et intouchables.

`boti` fournit à ces projets un petit ensemble de **primitives d'exécution avec des opinions arrêtées** pour que le même code puisse se déplacer proprement entre le développement local, l'automatisation et les flux de travail en production sans réinventer la même infrastructure.

## Pourquoi `boti` est utile

`boti` est utile quand vous voulez que votre code de transformation se comporte comme un vrai composant logiciel plutôt qu'une collection de scripts à usage unique.

Il aide en :

- standardisant le cycle de vie des ressources avec `ManagedResource`
- rendant l'accès aux fichiers contraint et explicite avec `SecureResource`
- centralisant la découverte de la racine du projet et de l'environnement avec `ProjectService`
- donnant au code base un modèle de journalisation partagé avec `Logger`

C'est particulièrement précieux quand plusieurs équipes ou notebooks interagissent avec le même code base, car cela réduit les hypothèses cachées et rend le comportement plus prévisible.

## Cas d'utilisation concrets

### Migration des notebooks vers la production

Le chemin le plus courant vers Boti : une équipe a des notebooks fonctionnels répartis entre contributeurs, mais le code s'exécute différemment sur chaque machine. `ManagedResource` fournit des hooks de cycle de vie déterministes, `ProjectService` ancre chaque environnement à la même racine de projet, et `Logger` offre des diagnostics cohérents. Le même code s'exécute maintenant dans un notebook, un pipeline CI/CD et un traitement par lots planifié — inchangé.

### Accès sécurisé aux fichiers dans les services multi-tenant

Les services qui acceptent des chemins de fichier provenant d'entrées utilisateur ou d'API externes sont une source récurrente de CVEs (CVE-2025-68472 dans MindsDB, CVE-2025-47273 dans setuptools, CVE-2025-4330 dans tarfile de Python). `SecureResource` résout chaque chemin vers sa forme canonique et rejette tout ce qui se trouve en dehors du sandbox configuré. C'est une défense en profondeur qui détecte les bugs de path traversal quelle que soit la façon dont le chemin a été construit.

### Travaux multiprocessus

Envoyer la configuration de ressources à un worker `multiprocessing.Pool` est l'un des points faibles les plus courants de Python. Le double verrouillage pickle de Boti (`allow_pickle` dans la configuration + variable d'environnement `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE`) permet de sérialiser une ressource et de l'envoyer à un worker sans shims pickle manuels. Le hook `_restore_runtime_state` rétablit les connexions transitoires du côté du worker.

### Pipelines ETL avec stockage hybride local/distant

Les travaux ETL qui basculent entre fichiers locaux, S3, GCS et espace temporaire en mémoire bénéficient de l'abstraction `FilesystemConfig` de Boti. Une seule configuration typée contrôle la connexion, et `ManagedResource._ensure_fs()` fournit un accès lazy et thread-safe au système de fichiers à la demande.

### Développement de bibliothèques partagées

Lorsque plusieurs paquets internes ou équipes dépendent de la même logique de transformation, Boti impose un contrat cohérent : chaque ressource s'ouvre et se ferme de la même manière, chaque accès aux fichiers est validé, chaque environnement est découvert depuis la même racine. Cela élimine le problème "ça marche sur ma machine" au niveau architectural.

## Paquets

### Paquet principal

```bash
pip install boti
```

Importations principales :

```python
from boti import Logger, ManagedResource, ProjectService, SecureResource
from boti.core import is_secure_path
```

Vous pouvez également importer directement depuis `boti.core` :

```python
from boti.core import Logger, ManagedResource, ProjectService, SecureResource
```

## Démarrage rapide

### Ressource gérée

```python
from boti import ManagedResource


class MaRessource(ManagedResource):
    def _cleanup(self) -> None:
        print("nettoyage en cours")


with MaRessource() as resource:
    print(resource.closed)  # False
```

### Configuration du système de fichiers

`FilesystemConfig` fournit une manière typée de décrire où une ressource doit lire et écrire des données. Il utilise `fsspec` en dessous, donc `boti` peut fonctionner avec le système de fichiers local, le stockage d'objets compatible S3 et tout autre backend supporté par vos drivers `fsspec` installés.

#### Fichiers locaux

```python
from boti.core.filesystem import FilesystemConfig, create_filesystem

config = FilesystemConfig(
    fs_type="file",
    fs_path="/srv/boti/data",
)

fs = create_filesystem(config)
with fs.open("/srv/boti/data/exemple.txt", "w") as handle:
    handle.write("bonjour")
```

#### Connexions serveur S3

Utilisez ce modèle pour vous connecter à AWS S3 ou à un serveur compatible S3 tel que MinIO, Ceph ou un autre endpoint de stockage d'objets interne.

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

`fs_endpoint` pointe vers le serveur S3, tandis que `fs_path` identifie le bucket et le préfixe avec lesquels vous souhaitez travailler.

#### Autres systèmes de fichiers supportés

Tout backend reconnu par la pile `fsspec` installée peut être utilisé via `fs_type`. Exemples courants :

- `memory` pour les tests et les flux de travail éphémères
- `gcs` pour Google Cloud Storage
- `az` ou `abfs` pour le stockage Azure
- `ftp`, `sftp` ou `http` où le driver correspondant est installé

```python
from boti.core.filesystem import FilesystemConfig

memory_config = FilesystemConfig(fs_type="memory", fs_path="scratch")
gcs_config = FilesystemConfig(fs_type="gcs", fs_path="my-bucket/datasets")
azure_config = FilesystemConfig(fs_type="az", fs_path="container/path")
```

### Service de projet

```python
from boti import ProjectService

project_root = ProjectService.detect_project_root()
env_file = ProjectService.setup_environment(project_root)
```

### Accès sécurisé aux fichiers

`SecureResource` encapsule les opérations sur les fichiers dans un bac à sable. Par défaut, il autorise les chemins sous la racine du projet détectée et le répertoire temporaire du système, et vous pouvez ajouter explicitement des chemins de confiance supplémentaires.

```python
from pathlib import Path

from boti import SecureResource
from boti.core.models import ResourceConfig

config = ResourceConfig(project_root=Path.cwd())

with SecureResource(config=config) as resource:
    contents = resource.read_text_secure("README.md")
```

#### Autoriser un répertoire de confiance supplémentaire

```python
from pathlib import Path

from boti import SecureResource
from boti.core.models import ResourceConfig

config = ResourceConfig(
    project_root=Path("/workspace/projet"),
    extra_allowed_paths=[Path("/srv/shared/donnees-reference")],
)

with SecureResource(config=config) as resource:
    reference = resource.read_text_secure("/srv/shared/donnees-reference/lookup.csv")
```

#### Bloquer les chemins non sécurisés

```python
from pathlib import Path

from boti import SecureResource
from boti.core.models import ResourceConfig

config = ResourceConfig(project_root=Path("/workspace/projet"))

with SecureResource(config=config) as resource:
    try:
        resource.read_text_secure("/etc/passwd")
    except PermissionError:
        print("en dehors des racines du bac à sable configurées")
```

### Logger

`Logger` fournit une couche de journalisation non bloquante et sûre pour les threads, avec une gestion sécurisée des fichiers et la suppression des données sensibles.

#### Logger rapide

```python
from pathlib import Path

from boti import Logger

logger = Logger.default_logger(
    logger_name="tache_quotidienne",
    log_file="tache_quotidienne",
    base_dir=Path("/workspace/projet"),
)

logger.info("démarrage de l'extraction")
logger.warning("nouvelle tentative après une erreur transitoire")
```

#### Configuration explicite du logger

```python
from pathlib import Path

from boti.core.logger import Logger
from boti.core.models import LoggerConfig

config = LoggerConfig(
    log_dir=Path("/workspace/projet/logs"),
    logger_name="etl.pipeline",
    log_file="etl_pipeline",
    verbose=True,
)

logger = Logger(config)
logger.set_level(Logger.INFO)
logger.info("lignes chargées=%s", 1200)
```

### Sous-classement de `ManagedResource`

`ManagedResource` supporte les patterns de nettoyage synchrones et asynchrones, de sorte que les ressources personnalisées peuvent exposer le même contrat de cycle de vie qu'elles encapsulent des systèmes de fichiers, des clients, des sockets ou d'autres états d'exécution.

#### Ressource synchrone

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
    resource.write_text("memory://exemple.txt", "bonjour depuis fsspec")
    print(resource.read_text("memory://exemple.txt"))
```

#### Ressource asynchrone

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

Si une sous-classe implémente uniquement `_cleanup()`, `await resource.aclose()` se rabattra sur l'exécution du nettoyage synchrone de manière sécurisée.

### Ressources sérialisables (pickle)

Par défaut, `ManagedResource` refuse d'être sérialisé avec pickle. La sérialisation est une option explicite que vous ne devez activer que lorsque le site de sérialisation et le site de désérialisation sont tous deux dans des environnements d'exécution que vous contrôlez.

C'est utile quand vous devez distribuer du travail entre des processus ou des machines et que vous souhaitez transporter la configuration de la ressource — paramètres de connexion, chemins, paramètres opérationnels — avec la tâche plutôt que de la reconstruire depuis zéro dans chaque worker.

Cas d'utilisation typiques :

- **multiprocessing** — envoyer une ressource configurée dans un worker de `Pool`
- **calcul distribué** — transmettre la configuration de ressources aux workers Dask, Ray ou Spark
- **files de tâches** — sauvegarder l'état des ressources entre les tâches Celery ou RQ

#### Comment fonctionne l'activation

Il y a deux verrous indépendants qui doivent tous deux être ouverts pour que la sérialisation fonctionne :

1. `allow_pickle=True` dans le `ResourceConfig` de la ressource — défini au moment de la construction, voyage avec le payload sérialisé
2. La variable d'environnement `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE=1` présente dans le processus worker au moment de la désérialisation

Cette conception à double facteur signifie qu'une ressource sérialisée ne peut pas être chargée silencieusement dans un environnement qui n'a pas été explicitement configuré pour lui faire confiance.

#### Ce qui est et n'est pas préservé

Quand une ressource est sérialisée, `ManagedResource` supprime automatiquement l'état qui ne peut pas traverser une frontière de processus :

- les locks de threads et les locks asyncio (recréés de l'autre côté)
- le finaliseur (rattaché de l'autre côté)
- l'instance du logger (reconstruite depuis la configuration de l'autre côté)
- le handle du système de fichiers actif et la factory (effacés ; voir `_restore_runtime_state` ci-dessous)

Les valeurs de configuration telles que les champs `ResourceConfig` et tous les attributs de sous-classe qui sont eux-mêmes sérialisables sont préservés intacts.

#### Exemple de base

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


# --- côté sérialisation ---
config = ResourceConfig(allow_pickle=True)
resource = ReportResource(output_dir=Path("/srv/rapports"), config=config)

payload = pickle.dumps(resource)
resource.close()

# --- côté désérialisation (processus worker) ---
with ManagedResource.trusted_unpickle_scope():
    restored = pickle.loads(payload)

print(restored.output_dir)  # /srv/rapports
print(restored.closed)      # False
restored.close()
```

`trusted_unpickle_scope()` est un gestionnaire de contexte qui définit `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE=1` pour sa durée et restaure la valeur d'origine à la sortie. Utilisez-le au point d'entrée du worker plutôt que de définir la variable globalement dans la mesure du possible.

#### Reconstruction des connexions transitoires après désérialisation

Si votre ressource détient un objet de connexion actif — une session de base de données, un client HTTP, un handle de fichier ouvert — cette connexion ne survivra pas à la sérialisation. Surchargez `_restore_runtime_state()` pour la rétablir du côté worker.

```python
import pickle
from pathlib import Path

from boti import ManagedResource
from boti.core.models import ResourceConfig


class CsvResource(ManagedResource):
    def __init__(self, data_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.data_dir = data_dir
        self._handle = None  # ouvert de manière lazy ou restauré après désérialisation

    def _restore_runtime_state(self) -> None:
        # Appelé automatiquement par __setstate__ après la désérialisation de l'objet.
        # Rouvrez les connexions ou réinitialisez l'état qui ne peut pas être transféré.
        self._handle = None  # sera ouvert à la première utilisation

    def read(self, filename: str) -> str:
        path = self.data_dir / filename
        with open(path) as f:
            return f.read()

    def _cleanup(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


# --- processus principal : créer et sérialiser ---
config = ResourceConfig(allow_pickle=True)
resource = CsvResource(data_dir=Path("/srv/donnees"), config=config)
payload = pickle.dumps(resource)
resource.close()

# --- processus worker : restaurer et utiliser ---
with ManagedResource.trusted_unpickle_scope():
    worker_resource = pickle.loads(payload)

with worker_resource:
    content = worker_resource.read("resume.csv")
    print(content)
```

#### Utilisation avec multiprocessing

L'utilisation la plus courante est l'envoi de la configuration de ressources à un pool de workers. Définissez la variable d'environnement dans l'initialiseur du worker afin qu'elle soit présente avant que toute tâche désérialise une ressource.

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
    resource = WorkerResource(data_dir=Path("/srv/donnees"), config=config)
    payload = pickle.dumps(resource)
    resource.close()

    with multiprocessing.Pool(initializer=worker_init) as pool:
        sizes = pool.starmap(run_task, [(payload, f) for f in ["a.bin", "b.bin"]])

    print(sizes)
```

#### Note de sécurité

N'activez `allow_pickle` que lorsque vous contrôlez les deux extrémités du canal de sérialisation. La désérialisation de données provenant de sources non fiables peut exécuter du code arbitraire. La variable d'environnement `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE` est la dernière ligne de défense : ne la définissez pas globalement dans des environnements qui traitent des données provenant de sources externes.

## Documentation complémentaire

- [`examples/`](examples/)

  **Cycle de vie — ressources, nettoyage et contrôle pickle**

  - [`simple_resource.py`](examples/simple_resource.py) — `ManagedResource` minimal avec `_cleanup` synchrone, utilisation du context-manager, idempotence de fermeture, détection de fuite GC via `weakref.finalize` et `_restore_runtime_state()`.
  - [`filesystem_resource.py`](examples/filesystem_resource.py) — sous-classe `ManagedResource` adossée à un système de fichiers fsspec. Montre `require_fs()`, l'initialisation lazy du système de fichiers et le nettoyage.
  - [`async_resource.py`](examples/async_resource.py) — `ManagedResource` avec `_acleanup` natif pour un nettoyage asynchrone sans fallback synchrone.
  - [`managed_resource_pickle.py`](examples/managed_resource_pickle.py) — refus du pickle par défaut, gestionnaire de contexte `trusted_unpickle_scope()`, `_restore_runtime_state()` après désérialisation et la variable d'environnement `BOTI_ALLOW_TRUSTED_RESOURCE_UNPICKLE`.

  **Abstractions du système de fichiers**

  - [`filesystem_config.py`](examples/filesystem_config.py) — `FilesystemConfig` pour les backends local, en mémoire et compatible S3 en utilisant `create_filesystem()` et `FilesystemAdapter`.
  - [`filesystem_from_env.py`](examples/filesystem_from_env.py) — `FilesystemConfig.from_settings()` et `from_env_prefix()` pour construire des profils typés de système de fichiers à partir de variables d'environnement ou de modèles pydantic.
  - [`filesystem_pyarrow.py`](examples/filesystem_pyarrow.py) — intégration PyArrow : `FilesystemAdapter.get_pyarrow_filesystem()`, lecture/écriture Parquet via fsspec et comportement de cache de l'adaptateur.
  - [`filesystem_supported_backends.py`](examples/filesystem_supported_backends.py) — test au niveau constructeur pour chaque backend fsspec que boti supporte. Utile pour identifier rapidement les paquets de drivers optionnels manquants.

  **Journalisation (logging)**

  - [`logger.py`](examples/logger.py) — `Logger` avec gestion sécurisée des fichiers, suppression des PII (mots de passe, jetons, clés API), journalisation structurée, fabrique `default_logger()` avec cache LRU et loggers par espace de noms.
  - [`logger_runtime.py`](examples/logger_runtime.py) — écouteur d'arrière-plan `LoggerRuntime`, journalisation multi-destination (fichier + stderr), `SafeRotatingFileHandler` et arrêt gracieux.

  **Sécurité — E/S en sandbox et validation**

  - [`secure_resource.py`](examples/secure_resource.py) — sandbox `SecureResource` avec `read_text_secure`, `write_text_secure`, `open_secure`, rejet de path traversal, `extra_allowed_paths` et détection de symlinks.
  - [`security_extended.py`](examples/security_extended.py) — `validate_environment_bindings()`, `is_valid_env_var_name()`, `is_valid_identifier()`, `is_valid_dotted_identifier()` et cas limites de `is_secure_path()`.

  **Découverte de projet et d'environnement**

  - [`project_environment.py`](examples/project_environment.py) — `ProjectService.detect_project_root()` et chargement de fichiers `.env` avec `setup_environment()`.
  - [`project_service_runtime.py`](examples/project_service_runtime.py) — utilisation de `ProjectService` centrée sur l'exécution : détection de la racine du service, chargement d'un fichier runtime.env et lecture des valeurs de configuration.
  - [`settings.py`](examples/settings.py) — modèles de configuration typés (`SqlDatabaseSettings`, `FilesystemSettings`), `load_prefixed_model()`, `load_dotenv_values()` et précédence de surcharge dotenv vs environnement de processus.

  **Pipeline complet**

  - [`end_to_end_pipeline.py`](examples/end_to_end_pipeline.py) — combine `ProjectService`, `SecureResource`, `Logger`, `FilesystemAdapter` et `ManagedResource` en un seul flux : détection de la racine du projet, chargement de `.env`, entrée en sandbox, traitement des enregistrements avec journalisation structurée et sortie vers un stockage géré.

  **ETL concurrent et parallèle**

  - [`etl_multiprocessing_pool.py`](examples/etl_multiprocessing_pool.py) — ETL réparti avec `multiprocessing.Pool`. Sérialise un `ManagedResource` avec `allow_pickle=True`, l'envoie aux workers via `pool.map` et appelle `_restore_runtime_state()` du côté worker. Utile pour diviser un grand ensemble de données en lots et les traiter en parallèle sans reconstruire la configuration par worker.
  - [`etl_concurrent_threads.py`](examples/etl_concurrent_threads.py) — ETL avec threads et `ThreadPoolExecutor`. Plusieurs tâches de pipeline (commandes, retours, remboursements) s'exécutent en concurrence, chacune lisant depuis un `FilesystemAdapter` partagé, transformant des enregistrements et journalisant la progression par tâche. Montre l'accès thread-safe à `ManagedResource` et la journalisation structurée avec contexte de phase.
  - [`etl_async_pipeline.py`](examples/etl_async_pipeline.py) — pipeline ETL asynchrone avec `asyncio.gather`. Les sources (utilisateurs, produits, événements) sont extraites, transformées et chargées concurrentiellement en utilisant async/await. Le hook natif `_acleanup` garantit un nettoyage approprié. Utile pour les charges de travail liées aux E/S où `asyncio` offre une concurrence plus élevée que les threads.
  - [`etl_concurrent_multiple.py`](examples/etl_concurrent_multiple.py) — sources ETL concurrentes multiples avec isolation des erreurs. Un seul `ManagedResource` gère deux adaptateurs de système de fichiers (fichier + mémoire) et traite les données de ventes, d'inventaire et d'analytique en parallèle via `ThreadPoolExecutor`. La gestion des erreurs par source signifie qu'un échec n'arrête pas les autres.

  **Profils de performance**

  - [`profile_logger_load.py`](examples/profile_logger_load.py) — benchmark de débit complet du Logger sous charge mono-thread et concurrente. Mesure les enregistrements propres, les enregistrements riches en PII et les motifs de contention de threads.
  - [`profile_path_validation.py`](examples/profile_path_validation.py) — benchmark massif de `is_secure_path()`, `is_valid_identifier()` et `is_valid_dotted_identifier()`. Révèle le coût de `Path.resolve()` (lié aux appels système) par rapport à la validation pure par regex (liée au CPU).
  - [`profile_pii_redaction.py`](examples/profile_pii_redaction.py) — benchmark du hot-path `PIISecretFilter.filter()` avec des charges utiles profondément imbriquées et riches en PII pour stresser le parcours récursif et la logique de balayage de chaînes.

## Développement

Exécutez les tests avec l'interpréteur du projet :

```bash
uv run pytest
```
