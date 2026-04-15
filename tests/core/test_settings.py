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
