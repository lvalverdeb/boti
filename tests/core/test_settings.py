from __future__ import annotations

import pytest
from pydantic import BaseModel

from boti.core.settings import load_prefixed_model


class ExampleSettings(BaseModel):
    retries: int
    labels: dict[str, int]


def test_load_prefixed_model_parses_structured_json_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "APP_RETRIES=3",
                'APP_LABELS={"urgent": 1, "normal": 2}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    settings = load_prefixed_model(ExampleSettings, "APP_", env_file=env_file)

    assert settings.retries == 3
    assert settings.labels == {"urgent": 1, "normal": 2}


def test_load_prefixed_model_raises_clear_error_for_invalid_value(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "APP_RETRIES=three",
                "APP_LABELS=not-json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="APP_RETRIES|APP_LABELS"):
        load_prefixed_model(ExampleSettings, "APP_", env_file=env_file)


# ---------------------------------------------------------------------------
# FilesystemSettings mirrors FilesystemConfig (PRD-005)
# ---------------------------------------------------------------------------


def test_filesystem_settings_covers_every_config_field():
    """Every FilesystemConfig knob must be reachable from the environment, and
    the two models cannot drift apart without this failing."""
    from boti.core.filesystem import FilesystemConfig
    from boti.core.settings import FilesystemSettings

    settings_fields = set(FilesystemSettings.model_fields)
    config_fields = set(FilesystemConfig.model_fields)
    assert settings_fields == config_fields, (
        f"only in FilesystemSettings: {sorted(settings_fields - config_fields)}; "
        f"only in FilesystemConfig: {sorted(config_fields - settings_fields)}"
    )


def test_filesystem_timeouts_from_env_prefix(monkeypatch):
    from boti.core.filesystem import FilesystemConfig

    monkeypatch.setenv("P_FS_PATH", "/data")
    monkeypatch.setenv("P_FS_CONNECT_TIMEOUT", "3")
    monkeypatch.setenv("P_FS_READ_TIMEOUT", "7.5")
    config = FilesystemConfig.from_env_prefix("P_")
    assert config.fs_connect_timeout == 3.0
    assert config.fs_read_timeout == 7.5


@pytest.mark.parametrize("token", ["none", "NONE", "null", " Null "])
def test_filesystem_timeout_none_tokens_disable_the_timeout(monkeypatch, token):
    from boti.core.filesystem import FilesystemConfig

    monkeypatch.setenv("P_FS_PATH", "/data")
    monkeypatch.setenv("P_FS_READ_TIMEOUT", token)
    config = FilesystemConfig.from_env_prefix("P_")
    assert config.fs_read_timeout is None
    assert config.fs_connect_timeout == 10.0  # untouched default


def test_filesystem_timeout_invalid_value_names_the_variable(monkeypatch):
    from boti.core.filesystem import FilesystemConfig

    monkeypatch.setenv("P_FS_PATH", "/data")
    monkeypatch.setenv("P_FS_CONNECT_TIMEOUT", "soon")
    with pytest.raises(ValueError, match="'P_FS_CONNECT_TIMEOUT'"):
        FilesystemConfig.from_env_prefix("P_")


def test_from_settings_passes_an_explicit_none_timeout_through():
    from boti.core.filesystem import FilesystemConfig
    from boti.core.settings import FilesystemSettings

    config = FilesystemConfig.from_settings(FilesystemSettings(fs_path="/d", fs_read_timeout=None))
    assert config.fs_read_timeout is None
