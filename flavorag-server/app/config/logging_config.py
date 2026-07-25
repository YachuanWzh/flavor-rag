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


class _StructuredAdapter(logging.LoggerAdapter):
    """Adapter that converts keyword arguments into the 'extra' dict.

    Standard logging.Logger only accepts 'exc_info', 'extra', 'stack_info',
    and 'stacklevel' as keyword arguments. This adapter intercepts all other
    kwargs and moves them into 'extra' so the StructuredFormatter can render
    them as key=value pairs.
    """

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
        root.setLevel(level)
