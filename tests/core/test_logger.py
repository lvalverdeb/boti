"""
Tests for the non-blocking Logger and PII redaction.
"""
import logging
import os
import time
import threading
from pathlib import Path
import pytest
from boti.core import Logger
from boti.core import logger as logger_module
from boti.core.logger import PIISecretFilter
from boti.core.logger_handlers import SafeRotatingFileHandler
from boti.core.models import LoggerConfig


def test_logger_pii_redaction(temp_log_dir):
    """Verify that sensitive information is redacted from logs."""
    config = LoggerConfig(
        log_dir=temp_log_dir,
        logger_name="pii_test",
        log_file="pii_test"
    )
    logger = Logger(config)
    
    # Log something sensitive
    logger.info("User password is: secret123")
    logger.info("My api_key is 'abc-xyz'")
    
    # Give the listener a moment to process the queue
    time.sleep(0.5)
    
    log_file = temp_log_dir / "pii_test.log"
    content = log_file.read_text()
    
    assert "[REDACTED SENSITIVE DATA]" in content
    assert "secret123" not in content
    assert "abc-xyz" not in content


def test_logger_pii_redaction_clears_sensitive_args(temp_log_dir):
    """Verify format arguments containing secrets are redacted without breaking logging."""
    config = LoggerConfig(
        log_dir=temp_log_dir,
        logger_name="pii_args_test",
        log_file="pii_args_test"
    )
    logger = Logger(config)

    logger.info("credential=%s", "api_key=abc-xyz")
    logger.info("authorization=%s", "Bearer secret123")

    time.sleep(0.5)

    log_file = temp_log_dir / "pii_args_test.log"
    content = log_file.read_text()

    assert content.count("[REDACTED SENSITIVE DATA]") == 2
    assert "abc-xyz" not in content
    assert "secret123" not in content


def test_logger_pii_redaction_masks_sensitive_extra_fields():
    """Verify sensitive extra fields are redacted on the log record itself."""
    record = logging.LogRecord(
        name="pii_extra_test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="safe message",
        args=(),
        exc_info=None,
    )
    record.token = "secret123"

    pii_filter = PIISecretFilter()
    assert pii_filter.filter(record) is True
    assert record.token == "[REDACTED]"


def test_logger_non_blocking(temp_log_dir):
    """
    Verify that logging doesn't block the main execution flow 
    even with multiple threads.
    """
    config = LoggerConfig(
        log_dir=temp_log_dir,
        logger_name="stress_test",
        log_file="stress_test"
    )
    logger = Logger(config)
    
    def log_task(n):
        for i in range(n):
            logger.info(f"Thread {threading.current_thread().name} log {i}")

    threads = []
    for i in range(10):
        t = threading.Thread(target=log_task, args=(100,))
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

    # If we reached here without hanging, it's a good sign.
    # Check that file exists and has content.
    time.sleep(0.5)
    log_file = temp_log_dir / "stress_test.log"
    assert log_file.exists()
    assert len(log_file.read_text().splitlines()) >= 1000


def test_default_logger_anchors_relative_log_dir_to_base_dir(temp_project_root):
    """Verify default_logger resolves relative log_dir from the provided base dir."""
    logger = Logger.default_logger(
        log_dir="logs",
        logger_name="base_dir_test",
        log_file="base_dir_test",
        base_dir=temp_project_root,
    )

    logger.info("anchored log")
    time.sleep(0.5)

    log_file = temp_project_root / "logs" / "base_dir_test.log"
    assert logger.log_dir == (temp_project_root / "logs").resolve()
    assert log_file.exists()
    assert "anchored log" in log_file.read_text()


def test_default_logger_reuses_cached_instance(temp_project_root):
    logger_one = Logger.default_logger(
        log_dir="logs",
        logger_name="cache_test",
        log_file="cache_test",
        base_dir=temp_project_root,
    )
    logger_two = Logger.default_logger(
        log_dir="logs",
        logger_name="cache_test",
        log_file="cache_test",
        base_dir=temp_project_root,
    )

    assert logger_one is logger_two


def test_default_logger_uses_boti_fallback_name(monkeypatch, temp_project_root):
    monkeypatch.setattr(logger_module.sys, "_getframe", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError()))

    logger = Logger.default_logger(base_dir=temp_project_root)

    assert logger.logger_name == "boti"


def test_logger_config_preserves_none_log_file_and_logger_uses_name_fallback(temp_log_dir):
    """Verify config preserves log_file=None while Logger falls back to logger_name."""
    config = LoggerConfig(
        log_dir=temp_log_dir,
        logger_name="implicit_file_test",
    )

    assert config.log_file is None

    logger = Logger(config)
    logger.info("implicit filename")
    time.sleep(0.5)

    log_file = temp_log_dir / "implicit_file_test.log"
    assert log_file.exists()
    assert "implicit filename" in log_file.read_text()


def test_logger_uses_restrictive_permissions_on_posix(temp_log_dir):
    """Verify logger-created log paths are user-only on POSIX systems."""
    if os.name != "posix":
        return

    config = LoggerConfig(
        log_dir=temp_log_dir / "nested_logs",
        logger_name="permissions_test",
        log_file="permissions_test"
    )
    logger = Logger(config)
    logger.info("permissions check")

    time.sleep(0.5)

    log_dir = temp_log_dir / "nested_logs"
    log_file = log_dir / "permissions_test.log"

    assert log_file.exists()
    assert log_dir.stat().st_mode & 0o777 == 0o700
    assert log_file.stat().st_mode & 0o777 == 0o600


def test_logger_rejects_path_like_log_file_names(temp_log_dir):
    """Verify log_file cannot escape the configured log directory."""
    with pytest.raises(ValueError, match="simple base name"):
        LoggerConfig(
            log_dir=temp_log_dir,
            logger_name="traversal_test",
            log_file="../traversal_test",
        )


def test_logger_rejects_unsafe_logger_name(temp_log_dir):
    """Verify logger names reject path-like and shell-hostile characters."""
    with pytest.raises(ValueError, match="logger_name must contain only"):
        LoggerConfig(
            log_dir=temp_log_dir,
            logger_name="../escape",
            log_file="safe_name",
        )

def test_logger_pii_redaction_circular_reference():
    """Verify that circular references in extra fields do not cause infinite recursion DoS."""
    record = logging.LogRecord(
        name="pii_circular_test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="safe message",
        args=(),
        exc_info=None,
    )
    
    # Create the cyclic/circular object
    a_dict = {}
    a_dict["self"] = a_dict
    a_dict["secret"] = "should_redact"
    
    record.payload = a_dict

    pii_filter = PIISecretFilter()
    assert pii_filter.filter(record) is True
    
    # Verify cyclic reduction succeeded without RecursionError
    assert "self" in record.payload
    assert record.payload["secret"] == "[REDACTED]"

def test_logger_toctou_symlink_attack_rejected(temp_log_dir):
    """Verify that the logger refuses to write to a planted symlink log file."""
    # Plant a symlink first
    log_dir = temp_log_dir / "toctou_logs"
    log_dir.mkdir(parents=True)
    symlink_path = log_dir / "toctou_test.log"
    fake_target = log_dir / "target.log"
    fake_target.touch()
    
    # Create the malicious symlink
    if os.name == "posix":
        os.symlink(fake_target, symlink_path)
    
        config = LoggerConfig(
            log_dir=log_dir,
            logger_name="toctou_test",
            log_file="toctou_test",
        )
        # Assert logger initialization raises ValueError tracking the symlink block
        with pytest.raises(ValueError, match="must not be a symlink"):
            logger = Logger(config)


def test_safe_rotating_file_handler_rejects_symlink_swap(temp_log_dir):
    """Verify delayed handler opens still reject symlink swaps at emit time."""
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        return

    log_dir = temp_log_dir / "delayed_open_logs"
    log_dir.mkdir(parents=True)
    log_path = log_dir / "delayed.log"
    target = log_dir / "target.log"
    target.touch()
    log_path.touch()

    handler = SafeRotatingFileHandler(log_path, maxBytes=1024, backupCount=1, delay=True)
    try:
        log_path.unlink()
        os.symlink(target, log_path)

        with pytest.raises(ValueError, match="must not be a symlink"):
            handler._open()
    finally:
        handler.close()
