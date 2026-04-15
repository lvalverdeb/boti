"""
Tests for security and path sandboxing.
"""
import os
import tempfile
from types import SimpleNamespace

import pytest
from pathlib import Path
from boti.core import SecureResource, ProjectService
from boti.core import project as project_module
from boti.core.models import ResourceConfig


def test_project_root_detection(temp_project_root):
    """Verify that ProjectService correctly finds the root marker."""
    # Test from within a subdirectory
    sub = temp_project_root / "src" / "deep" / "path"
    sub.mkdir(parents=True)
    
    root = ProjectService.detect_project_root(start_path=sub)
    assert root == temp_project_root.resolve()


def test_project_root_detection_uses_caller_frame_when_cwd_is_root(monkeypatch, temp_project_root):
    """Verify auto-detection can recover from a root cwd by inspecting caller frames."""
    notebook_dir = temp_project_root / "notebooks"
    notebook_dir.mkdir()
    notebook_file = notebook_dir / "example_notebook.py"
    notebook_file.touch()

    monkeypatch.setattr(project_module.os, "getcwd", lambda: "/")
    monkeypatch.delenv("PWD", raising=False)
    monkeypatch.setattr(
        project_module.inspect,
        "stack",
        lambda: [
            SimpleNamespace(filename="<frame>"),
            SimpleNamespace(filename=str(notebook_file)),
        ],
    )

    root = ProjectService.detect_project_root()
    assert root == temp_project_root.resolve()


def test_project_root_detection_supports_custom_markers(tmp_path):
    project_root = tmp_path / "workspace"
    nested = project_root / "deep" / "inside"
    nested.mkdir(parents=True)
    (project_root / ".workspace-root").touch()

    root = ProjectService.detect_project_root(start_path=nested, markers=[".workspace-root"])

    assert root == project_root.resolve()


def test_secure_resource_sandboxing(temp_project_root):
    """Verify that SecureResource blocks path traversal."""
    config = ResourceConfig(project_root=temp_project_root)
    with SecureResource(config=config) as res:
        # Valid path
        valid = temp_project_root / "valid.txt"
        assert res.get_secure_path(valid) == valid.resolve()
        
        # Invalid path (outside root)
        with pytest.raises(PermissionError, match="outside the configured sandbox roots"):
            res.get_secure_path("/etc/passwd")
            
        # Traversal attempt (definitely outside)
        with pytest.raises(PermissionError):
            res.get_secure_path("/System/not_allowed")


def test_secure_resource_temp_allowed(temp_project_root):
    """Verify the system temp root is allowlisted for notebook-style workflows."""
    config = ResourceConfig(project_root=temp_project_root)
    with SecureResource(config=config) as res:
        tmp = Path(tempfile.gettempdir()).resolve()
        assert res.get_secure_path(tmp) == tmp


def test_secure_resource_extra_allowed(temp_project_root):
    """Verify that explicitly added paths are allowed."""
    extra = Path("/tmp/sibi_extra_test").resolve()
    # Mock existence if needed, but resolve() works regardless of existence
    config = ResourceConfig(project_root=temp_project_root, extra_allowed_paths=[extra])
    with SecureResource(config=config) as res:
        assert res.get_secure_path(extra) == extra


def test_setup_environment_loads_in_project_env_file(monkeypatch, temp_project_root):
    """Verify setup_environment loads files inside the project root."""
    env_file = temp_project_root / ".env.local"
    env_file.write_text("BOTI_TEST_ENV='loaded'\n", encoding="utf-8")
    monkeypatch.delenv("BOTI_TEST_ENV", raising=False)

    used = ProjectService.setup_environment(temp_project_root, ".env.local")

    assert used == env_file.resolve()
    assert os.environ["BOTI_TEST_ENV"] == "loaded"


def test_setup_environment_rejects_invalid_env_var_name(temp_project_root):
    """Verify setup_environment rejects dotenv keys that are not valid environment names."""
    env_file = temp_project_root / ".env.local"
    env_file.write_text("BAD-NAME=loaded\n", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid environment bindings"):
        ProjectService.setup_environment(temp_project_root, ".env.local")


def test_setup_environment_rejects_nul_bytes_in_value(temp_project_root):
    """Verify setup_environment rejects dotenv values that contain NUL bytes."""
    env_file = temp_project_root / ".env.local"
    env_file.write_bytes(b"BOTI_TEST_ENV=bad\x00value\n")

    with pytest.raises(ValueError, match="Invalid environment bindings"):
        ProjectService.setup_environment(temp_project_root, ".env.local")


def test_setup_environment_rejects_external_absolute_path(temp_project_root):
    """Verify setup_environment rejects env files outside the project root."""
    outside_dir = temp_project_root.parent / "outside-envs"
    outside_dir.mkdir(exist_ok=True)
    env_file = outside_dir / "evil.env"
    env_file.write_text("BOTI_TEST_ENV='blocked'\n", encoding="utf-8")

    with pytest.raises(PermissionError, match="must be inside project root"):
        ProjectService.setup_environment(temp_project_root, env_file)


def test_setup_environment_rejects_relative_traversal(temp_project_root):
    """Verify setup_environment rejects traversal paths that escape the project root."""
    outside_dir = temp_project_root.parent / "outside-traversal"
    outside_dir.mkdir(exist_ok=True)
    env_file = outside_dir / "evil.env"
    env_file.write_text("BOTI_TEST_ENV='blocked'\n", encoding="utf-8")

    traversal = Path("..") / outside_dir.name / env_file.name
    with pytest.raises(PermissionError, match="must be inside project root"):
        ProjectService.setup_environment(temp_project_root, traversal)


def test_setup_environment_supports_custom_candidate_files(monkeypatch, temp_project_root):
    env_file = temp_project_root / "settings.env"
    env_file.write_text("BOTI_TEST_ENV='custom'\n", encoding="utf-8")
    monkeypatch.delenv("BOTI_TEST_ENV", raising=False)

    used = ProjectService.setup_environment(
        temp_project_root,
        candidate_files=["settings.env"],
    )

    assert used == env_file.resolve()
    assert os.environ["BOTI_TEST_ENV"] == "custom"


def test_secure_resource_default_logger_uses_project_root(temp_project_root):
    """Verify default logger paths are anchored to the configured project root."""
    config = ResourceConfig(project_root=temp_project_root)

    with SecureResource(config=config) as res:
        assert res.logger.log_dir == (temp_project_root / "logs").resolve()
