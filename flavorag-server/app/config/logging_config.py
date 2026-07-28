"""Structured logging configuration for flavor-rag.

Provides component-level loggers with consistent formatting that captures:
  - Timestamps with millisecond precision
  - Component/module name
  - Log level
  - Structured key=value pairs for machine parsing

Usage per module:
    from app.config.logging_config import get_logger
    _log = get_logger("flavorag.auth")
    _log.info("user_lookup", username="admin", found=True)
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any


class StructuredFormatter(logging.Formatter):
    """Custom formatter that emits key=value structured logs.

    Output format:
        2026-07-25T10:30:00.123Z [INFO ] flavorag.auth: user_lookup username=admin found=true took_ms=2.3
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.") + \
             f"{datetime.now(timezone.utc).microsecond // 1000:03d}Z"
        level = record.levelname[:5].ljust(5)

        # Build structured message from extra fields
        msg = record.getMessage()

        # Collect structured key=value pairs from extra
        extra_pairs = []
        reserved = {
            "name", "msg", "args", "levelname", "levelno", "pathname",
            "filename", "module", "exc_info", "exc_text", "stack_info",
            "lineno", "funcName", "created", "msecs", "relativeCreated",
            "thread", "threadName", "processName", "process",
            "message", "asctime", "taskName",
        }
        for key, value in record.__dict__.items():
            if key not in reserved and not key.startswith("_"):
                extra_pairs.append(f"{key}={self._format_value(value)}")

        if extra_pairs:
            return f"{ts} [{level}] {record.name}: {msg} {' '.join(extra_pairs)}"
        else:
            return f"{ts} [{level}] {record.name}: {msg}"

    @staticmethod
    def _format_value(value: Any) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            # Quote strings with spaces
            if " " in value or "=" in value:
                return json.dumps(value, ensure_ascii=False)
            return value
        if value is None:
            return "null"
        return json.dumps(value, ensure_ascii=False)


class PersistentErrorAuditHandler(logging.Handler):
    """Mirror ERROR/CRITICAL log records into the durable error audit.

    Records are placed on the application-owned audit queue so logging stays
    non-blocking and database work is drained before shutdown.
    """

    def __init__(self):
        super().__init__(level=logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        event = record.getMessage().lower()
        if record.levelno < logging.ERROR and not any(
            marker in event for marker in ("failed", "error", "timeout")
        ):
            return
        if _error_audit_queue is None:
            return

        error_value = getattr(record, "error", None)
        exc = RuntimeError(str(error_value or record.getMessage()))
        context = {
            "logger": record.name,
            "module": record.module,
            "method": record.funcName,
            "line": record.lineno,
            "event": record.getMessage(),
        }
        error_type = getattr(record, "error_type", None)
        if error_type:
            context["error_type"] = error_type
        _error_audit_queue.put_nowait((exc, record.name, context))


class _StructuredAdapter(logging.LoggerAdapter):
    """Adapter that converts keyword arguments into the 'extra' dict.

    Standard logging.Logger only accepts 'exc_info', 'extra', 'stack_info',
    and 'stacklevel' as keyword arguments. This adapter intercepts all other
    kwargs and moves them into 'extra' so the StructuredFormatter can render
    them as key=value pairs.
    """

    @staticmethod
    def _sanitize_kwargs(kwargs: dict) -> None:
        """Strip positional-or-keyword params that third-party callers may
        inject (e.g. ``level``, ``msg``), preventing ``TypeError: got
        multiple values for argument 'level'`` on Python 3.14+."""
        kwargs.pop("level", None)
        kwargs.pop("msg", None)

    def log(self, *args, **kwargs):
        self._sanitize_kwargs(kwargs)
        return super().log(*args, **kwargs)

    def info(self, *args, **kwargs):
        self._sanitize_kwargs(kwargs)
        return super().info(*args, **kwargs)

    def warning(self, *args, **kwargs):
        self._sanitize_kwargs(kwargs)
        return super().warning(*args, **kwargs)

    def error(self, *args, **kwargs):
        self._sanitize_kwargs(kwargs)
        return super().error(*args, **kwargs)

    def debug(self, *args, **kwargs):
        self._sanitize_kwargs(kwargs)
        return super().debug(*args, **kwargs)

    def critical(self, *args, **kwargs):
        self._sanitize_kwargs(kwargs)
        return super().critical(*args, **kwargs)

    def exception(self, *args, **kwargs):
        self._sanitize_kwargs(kwargs)
        return super().exception(*args, **kwargs)

    def process(self, msg, kwargs):
        # Separate logging-internal kwargs from user-supplied structured fields
        logging_keys = {"exc_info", "extra", "stack_info", "stacklevel"}
        extra = dict(kwargs.pop("extra", {}))
        for k in list(kwargs):
            if k not in logging_keys:
                extra[k] = kwargs.pop(k)
        kwargs["extra"] = extra
        return msg, kwargs


_loggers: dict[str, logging.LoggerAdapter] = {}
_error_audit_queue: asyncio.Queue | None = None
_error_audit_task: asyncio.Task | None = None


async def start_error_audit_worker() -> None:
    """Start the single database writer used by persistent log auditing."""
    global _error_audit_queue, _error_audit_task
    if _error_audit_task is not None and not _error_audit_task.done():
        return
    _error_audit_queue = asyncio.Queue()

    async def drain() -> None:
        from app.error_handling import record_system_error

        assert _error_audit_queue is not None
        while True:
            item = await _error_audit_queue.get()
            try:
                if item is None:
                    return
                exc, component, context = item
                await record_system_error(
                    exc,
                    component=component,
                    context=context,
                )
            finally:
                _error_audit_queue.task_done()

    _error_audit_task = asyncio.create_task(drain())


async def stop_error_audit_worker() -> None:
    """Flush queued audits and stop the writer before the DB engine closes."""
    global _error_audit_queue, _error_audit_task
    if _error_audit_queue is None or _error_audit_task is None:
        return
    await _error_audit_queue.join()
    _error_audit_queue.put_nowait(None)
    await _error_audit_task
    _error_audit_queue = None
    _error_audit_task = None


def get_logger(name: str) -> logging.LoggerAdapter:
    """Get or create a structured logger for a component.

    Returns a LoggerAdapter that accepts structured fields as keyword
    arguments and renders them via StructuredFormatter:
        _log = get_logger("flavorag.auth")
        _log.info("login_attempt", username="admin", success=True, took_ms=12.5)
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)

    # Avoid duplicate handlers
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
        logger.addHandler(PersistentErrorAuditHandler())
        logger.setLevel(logging.DEBUG)
        # Prevent propagation to root logger to avoid duplicate output
        logger.propagate = False

    adapter = _StructuredAdapter(logger, {})
    _loggers[name] = adapter
    return adapter


def configure_root_logger(level: int = logging.INFO):
    """Configure the root logger with structured formatting."""
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter())
        root.addHandler(handler)
        root.addHandler(PersistentErrorAuditHandler())
        root.setLevel(level)
