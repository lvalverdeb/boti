"""
Project management and configuration services for Boti.

Provides utilities for project root detection and environment setup,
ensuring that toolkit operations are context-aware.
"""

from __future__ import annotations
import inspect
import os
import warnings
from pathlib import Path

__all__ = ["ProjectService"]
from typing import Iterable, Optional, Union

from boti.core.security import is_secure_path
from boti.core.settings import load_dotenv_values


class ProjectService:
    """
    Centralized service for project-level concerns like root detection and environment setup.
    """

    DEFAULT_ROOT_MARKERS: tuple[Path, ...] = (
        Path("pyproject.toml"),
        Path(".git"),
        Path(".env"),
        Path(".agent"),
        Path("src") / "boti",
    )
    DEFAULT_ENV_CANDIDATES: tuple[Path, ...] = (
        Path(".env"),
        Path(".env.linux"),
        Path(".env.local"),
    )

    @staticmethod
    def detect_project_root(
        start_path: Optional[Union[str, Path]] = None,
        *,
        markers: Optional[Iterable[Union[str, Path]]] = None,
    ) -> Path:
        """
        Heuristic to find the project root by looking for common markers.

        Searches upwards from the start_path for configurable markers like
        'pyproject.toml', '.git', or '.env'.

        Args:
            start_path: The path to start the search from. Defaults to current working directory.
            markers: Optional relative marker paths to use instead of the defaults.

        Returns:
            Path: The resolved absolute path of the project root.
        """
        resolved_markers = ProjectService._resolve_relative_markers(markers)
        if start_path is not None:
            candidate = ProjectService._normalize_search_path(start_path)
            return ProjectService._search_ancestors(candidate, markers=resolved_markers) or candidate

        candidates = list(ProjectService._candidate_search_paths())
        for candidate in candidates:
            detected = ProjectService._search_ancestors(candidate, markers=resolved_markers)
            if detected is not None:
                return detected

        for candidate in candidates:
            if candidate.parent != candidate:
                warnings.warn(
                    f"boti could not locate a project root marker "
                    f"(pyproject.toml, .git, .env, …) from any search path. "
                    f"Falling back to '{candidate}'. "
                    "Add a marker file to the project root to suppress this warning.",
                    UserWarning,
                    stacklevel=2,
                )
                return candidate

        warnings.warn(
            "boti could not detect a project root and all candidate paths are at the "
            "filesystem root. Falling back to the home directory. "
            "This is almost certainly wrong — add a project root marker.",
            UserWarning,
            stacklevel=2,
        )
        return Path.home().resolve()

    @staticmethod
    def _candidate_search_paths() -> Iterable[Path]:
        seen: set[Path] = set()
        raw_candidates: list[Union[str, Path]] = [os.getcwd()]

        pwd = os.environ.get("PWD")
        if pwd:
            raw_candidates.append(pwd)

        for frame in inspect.stack()[1:8]:
            filename = getattr(frame, "filename", None)
            if filename and not filename.startswith("<"):
                raw_candidates.append(filename)

        for raw_candidate in raw_candidates:
            candidate = ProjectService._normalize_search_path(raw_candidate)
            if candidate not in seen:
                seen.add(candidate)
                yield candidate

    @staticmethod
    def _normalize_search_path(path: Union[str, Path]) -> Path:
        candidate = Path(path).expanduser().resolve()
        return candidate.parent if candidate.is_file() else candidate

    @staticmethod
    def _resolve_relative_markers(
        markers: Optional[Iterable[Union[str, Path]]],
    ) -> tuple[Path, ...]:
        resolved = markers if markers is not None else ProjectService.DEFAULT_ROOT_MARKERS
        return tuple(Path(marker) for marker in resolved)

    @staticmethod
    def _search_ancestors(start: Path, *, markers: Iterable[Path]) -> Optional[Path]:
        for curr in [start] + list(start.parents):
            candidate_markers = [curr / marker for marker in markers]
            if any(marker.exists() for marker in candidate_markers):
                return curr

        return None

    @staticmethod
    def setup_environment(
        project_root: Path,
        env_file: Optional[Union[str, Path]] = None,
        *,
        candidate_files: Optional[Iterable[Union[str, Path]]] = None,
    ) -> Path:
        """
        Loads environment variables from a .env file into os.environ.

        Args:
            project_root: The root of the project.
            env_file: Optional explicit path to an env file.
            candidate_files: Optional relative candidate env paths to probe when env_file is omitted.

        Returns:
            Path: The path to the environment file used.
        """
        resolved_project_root = Path(project_root).expanduser().resolve()

        if env_file:
            target = Path(env_file).expanduser()
            if not target.is_absolute():
                target = resolved_project_root / target
        else:
            candidates = [
                resolved_project_root / Path(candidate)
                for candidate in (
                    candidate_files
                    if candidate_files is not None
                    else ProjectService.DEFAULT_ENV_CANDIDATES
                )
            ]
            target = candidates[0]
            for c in candidates:
                if c.exists():
                    target = c
                    break

        target = target.resolve()
        if not is_secure_path(target, [resolved_project_root]):
            raise PermissionError(
                f"Environment file {target} must be inside project root {resolved_project_root}."
            )

        if target.exists():
            try:
                dotenv_values = load_dotenv_values(target)
            except ValueError as exc:
                raise ValueError(f"Invalid environment bindings in {target}: {exc}") from exc

            for key, value in dotenv_values.items():
                os.environ[key] = value
        
        return target
