"""
Tests for the non-blocking Logger. Redaction tests live in test_logger_filters.py.
"""

import logging
import os
import threading
import time

import pytest

from boti.core import Logger
from boti.core import logger as logger_module
from boti.core.logger_handlers import SafeRotatingFileHandler
from boti.core.logger_runtime import LoggerRuntime
from boti.core.models import LoggerConfig


def test_logger_non_blocking(temp_log_dir):
    """
    Verify that logging doesn't block the main execution flow
    even with multiple threads.
    """
    config = LoggerConfig(log_dir=temp_log_dir, logger_name="stress_test", log_file="stress_test")
    logger = Logger(config)

    def log_task(n):
        for i in range(n):
            logger.info(f"Thread {threading.current_thread().name} log {i}")

    threads = []
    for _i in range(10):
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
    monkeypatch.setattr(
        logger_module.sys,
        "_getframe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError()),
    )

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
        log_file="permissions_test",
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
            Logger(config)


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


def test_logger_renders_bound_fields_in_text_output(temp_log_dir):
    """Verify .bind() fields appear as a key=value tail in the plain-text log line."""
    config = LoggerConfig(log_dir=temp_log_dir, logger_name="bind_test", log_file="bind_test")
    logger = Logger(config).bind(run_id="abc123")

    logger.info("did thing")

    time.sleep(0.5)

    log_file = temp_log_dir / "bind_test.log"
    content = log_file.read_text()

    assert "did thing" in content
    assert "run_id='abc123'" in content


def test_logger_renders_extra_kwarg_fields_in_text_output(temp_log_dir):
    """Verify a per-call extra= dict appears as a key=value tail in the plain-text log line."""
    config = LoggerConfig(log_dir=temp_log_dir, logger_name="extra_test", log_file="extra_test")
    logger = Logger(config)

    logger.info("processed period", extra={"period": "2026-01"})

    time.sleep(0.5)

    log_file = temp_log_dir / "extra_test.log"
    content = log_file.read_text()

    assert "processed period" in content
    assert "period='2026-01'" in content


def test_logger_plain_message_has_no_trailing_tail(temp_log_dir):
    """Verify a call with no bound/extra fields produces an unchanged line, no trailing '|'."""
    config = LoggerConfig(log_dir=temp_log_dir, logger_name="plain_test", log_file="plain_test")
    logger = Logger(config)

    logger.info("nothing extra here")

    time.sleep(0.5)

    log_file = temp_log_dir / "plain_test.log"
    content = log_file.read_text()

    assert "nothing extra here" in content
    assert "|" not in content


def test_extra_fields_formatter_ignores_asctime_set_by_earlier_handler():
    """The file formatter sets ``asctime`` on the shared record before the console
    formatter runs; the console line must not render it as a ``| asctime=`` tail."""
    record = logging.LogRecord("fmt", logging.INFO, __file__, 1, "hello", (), None)
    first = logger_module._default_formatter()
    second = logger_module._default_formatter()

    first.format(record)
    assert hasattr(record, "asctime")
    assert second.format(record).endswith("hello")


def test_child_logger_records_do_not_reach_parent_destinations(temp_log_dir, capsys):
    """``app`` and ``app.child`` each get their own file and exactly one console line."""
    parent = Logger(LoggerConfig(log_dir=temp_log_dir, logger_name="app", log_file="app"))
    child = Logger(
        LoggerConfig(log_dir=temp_log_dir, logger_name="app.child", log_file="app_child")
    )
    parent.warning("from-parent")
    child.warning("from-child")
    time.sleep(0.5)

    assert "from-child" not in (temp_log_dir / "app.log").read_text()
    assert "from-child" in (temp_log_dir / "app_child.log").read_text()
    assert capsys.readouterr().out.count("from-child") == 1


class _RecordCapture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


def test_logger_stacklevel_attributes_records_to_the_caller(temp_log_dir):
    """``_CALLER_STACKLEVEL`` must skip Logger's own frames so filename/funcName
    point at user code — on both the plain path and the LoggerAdapter (extras) path."""
    logger = Logger(LoggerConfig(log_dir=temp_log_dir, logger_name="stack_test", log_file="s"))
    capture = _RecordCapture()
    logger.core.addHandler(capture)
    try:
        logger.info("plain")
        logger.bind(run="r1").info("with extras")
    finally:
        logger.core.removeHandler(capture)

    assert [r.getMessage() for r in capture.records] == ["plain", "with extras"]
    for record in capture.records:
        assert record.filename == os.path.basename(__file__)
        assert record.funcName == "test_logger_stacklevel_attributes_records_to_the_caller"


def test_logger_falls_back_to_stderr_when_log_dir_cannot_be_created(tmp_path, capsys):
    """An uncreatable log_dir must not abort logger init: it warns and routes to stderr."""
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("")
    log_dir = blocker / "logs"  # mkdir -> NotADirectoryError (an OSError)

    with pytest.warns(RuntimeWarning, match="Falling back to stderr"):
        logger = Logger(LoggerConfig(log_dir=log_dir, logger_name="stderr_test", log_file="x"))

    logger.warning("went to stderr")
    time.sleep(0.5)

    assert not log_dir.exists()
    assert "went to stderr" in capsys.readouterr().err


def test_default_logger_same_name_different_level_returns_shared_instance(temp_log_dir):
    """All Loggers with one name share one stdlib logger, so level cannot be
    per-object. default_logger() returns the one instance and applies the
    latest requested level to it, rather than handing back a second object
    that silently shares (and clobbers) the first's level."""
    first = Logger.default_logger(log_dir=temp_log_dir, logger_name="lvl", log_level=logging.DEBUG)
    second = Logger.default_logger(log_dir=temp_log_dir, logger_name="lvl", log_level=logging.ERROR)

    assert first is second
    assert first.log_level == logging.ERROR
    assert first.core.level == logging.ERROR


def test_logger_close_releases_destinations_queue_handler_and_cache_entry(temp_log_dir):
    from logging.handlers import QueueHandler

    from boti.core.logger_runtime import LoggerRuntime

    logger = Logger.default_logger(log_dir=temp_log_dir, logger_name="closeme")
    logger.warning("before close")
    time.sleep(0.3)
    log_file = temp_log_dir / "closeme.log"
    assert "before close" in log_file.read_text()
    file_handlers = [
        h
        for h in LoggerRuntime._destinations[logger._sink]
        if isinstance(h, SafeRotatingFileHandler)
    ]
    assert len(file_handlers) == 1 and file_handlers[0].stream is not None

    logger.close()
    logger.close()  # idempotent

    assert logger._sink not in LoggerRuntime._destinations
    assert not any(k[0] == "closeme" for k in LoggerRuntime._attached_keys)
    assert not any(isinstance(h, QueueHandler) for h in logger.core.handlers)
    assert file_handlers[0].stream is None  # file handle closed
    assert logger not in Logger._default_logger_cache.values()

    # A fresh default_logger() with the same arguments rebuilds a working logger.
    reborn = Logger.default_logger(log_dir=temp_log_dir, logger_name="closeme")
    assert reborn is not logger
    reborn.warning("after rebuild")
    time.sleep(0.3)
    assert "after rebuild" in log_file.read_text()


def test_logger_runtime_destinations_survive_listener_restart(temp_log_dir):
    """stop_listener()/ensure_listener() must not strand loggers created before
    the stop: destinations live in the registry, not on the dead listener."""
    from boti.core.logger_runtime import LoggerRuntime

    survivor = Logger(LoggerConfig(log_dir=temp_log_dir, logger_name="survivor", log_file="s"))
    LoggerRuntime.stop_listener()
    # Creating any logger restarts the listener.
    Logger(LoggerConfig(log_dir=temp_log_dir, logger_name="restarter", log_file="r"))

    survivor.warning("after restart")
    time.sleep(0.3)
    assert "after restart" in (temp_log_dir / "s.log").read_text()


def test_logger_runtime_registry_and_destinations_stay_consistent(temp_log_dir):
    """No hidden cap: N distinct loggers → N registry entries and 2N destinations
    (file + console), and closing them all releases every one."""
    from boti.core.logger_runtime import LoggerRuntime

    names = [f"bulk{i}" for i in range(40)]
    loggers = [Logger.default_logger(log_dir=temp_log_dir, logger_name=n) for n in names]
    assert all(lg._sink in LoggerRuntime._destinations for lg in loggers)
    assert sum(len(LoggerRuntime._destinations[lg._sink]) for lg in loggers) == 2 * len(names)
    assert all(lg in Logger._default_logger_cache.values() for lg in loggers)

    for lg in loggers:
        lg.close()

    assert not any(lg._sink in LoggerRuntime._destinations for lg in loggers)
    assert not any(lg in Logger._default_logger_cache.values() for lg in loggers)


def test_same_name_loggers_in_different_dirs_do_not_cross_write(tmp_path, capsys):
    """Two ManagedResource instances of one class with different project roots
    share logging.getLogger(ClassName); each must still write only to its own
    file — records are routed by sink, not by name."""
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    a = Logger.default_logger(log_dir=dir_a, logger_name="Shared")
    b = Logger.default_logger(log_dir=dir_b, logger_name="Shared")
    assert a is not b and a.core is b.core

    a.warning("from-a")
    b.warning("from-b")
    time.sleep(0.3)

    a_log, b_log = (dir_a / "Shared.log").read_text(), (dir_b / "Shared.log").read_text()
    assert "from-a" in a_log and "from-b" not in a_log
    assert "from-b" in b_log and "from-a" not in b_log
    out = capsys.readouterr().out
    assert out.count("from-a") == 1 and out.count("from-b") == 1

    # Closing one must not detach the queue handler the other still relies on.
    a.close()
    b.warning("still-b")
    time.sleep(0.3)
    assert "still-b" in (dir_b / "Shared.log").read_text()
    b.close()


def test_raw_stdlib_logger_destinations_still_route_by_name(tmp_path):
    """add_destination() without a sink serves plain logging.getLogger() users
    (the documented LoggerRuntime example) — their records carry no sink stamp."""
    from logging.handlers import QueueHandler

    from boti.core.logger_runtime import LoggerRuntime

    LoggerRuntime.ensure_listener()
    path = tmp_path / "raw.log"
    LoggerRuntime.add_destination(
        ("raw_app", str(path)),
        SafeRotatingFileHandler(path, delay=True),
        logging.Formatter("%(message)s"),
    )
    raw = logging.getLogger("raw_app")
    raw.setLevel(logging.INFO)
    raw.addHandler(QueueHandler(LoggerRuntime.ensure_queue()))
    try:
        raw.info("hello raw")
        time.sleep(0.3)
        assert "hello raw" in path.read_text()
    finally:
        raw.handlers.clear()
        LoggerRuntime.remove_destinations(("raw_app", ""), {("raw_app", str(path))})


def test_closed_file_handler_drops_records_still_in_flight(temp_log_dir):
    """Logger.close() closes the file handler outside the listener lock, so the
    listener may already hold that handler for an in-flight record. The stdlib
    FileHandler.emit would re-open the file in append mode; ours must not."""
    config = LoggerConfig(log_dir=temp_log_dir, logger_name="late", log_file="late")
    logger = Logger(config)
    handlers = LoggerRuntime._destinations[logger._sink]
    file_handler = next(h for h in handlers if isinstance(h, SafeRotatingFileHandler))
    log_path = temp_log_dir / "late.log"
    logger.info("before close")
    LoggerRuntime.stop_listener()
    before = log_path.read_bytes()

    logger.close()
    late = logging.LogRecord("late", logging.INFO, __file__, 1, "after close", None, None)
    file_handler.handle(late)

    assert file_handler.stream is None
    assert log_path.read_bytes() == before
