"""
Logger example with secure defaults and PII redaction.
"""

from __future__ import annotations

import time
from pathlib import Path
from tempfile import TemporaryDirectory

from boti.core.logger import Logger
from boti.core.models import LoggerConfig


def main() -> None:
    with TemporaryDirectory() as tmp_dir:
        config = LoggerConfig(
            log_dir=Path(tmp_dir) / "logs",
            logger_name="examples.logger",
            debug=True,
        )
        logger = Logger(config)
        logger.set_level(Logger.DEBUG)

        logger.info("plain message")
        logger.warning(
            "payload received",
            extra={"payload": {"token": "super-secret", "profile": {"password": "hidden"}}},
        )

        time.sleep(0.1)
        print(config.log_dir / f"{config.log_file}.log")


if __name__ == "__main__":
    main()
