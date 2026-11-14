"""Structured logging configuration.

Logs are emitted as JSON (one object per line) so they are machine-parseable in
aggregation systems, or as plain text when ``log_json`` is false for local
reading. A request-scoped correlation id is injected by middleware.

Sensitive data must never be logged. To make that enforceable, helpers here
redact known-sensitive keys, and request logging records only method, path,
status, duration, request id, and the resolved principal id — never bodies,
tokens, or note content.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

REDACTED = "<redacted>"

# Keys whose values must never appear in logs.
_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "access_token",
        "refresh_token",
        "id_token",
        "authorization",
        "api_key",
        "apikey",
        "cookie",
        "set-cookie",
        "content",
        "note_content",
    }
)


def redact(mapping: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``mapping`` with sensitive values replaced."""
    return {
        key: (REDACTED if key.lower() in _SENSITIVE_KEYS else value)
        for key, value in mapping.items()
    }


class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Structured extras attached via ``logger.info(..., extra={...})``.
        for key in ("request_id", "method", "path", "status_code", "duration_ms", "user_id"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable formatter for local development."""

    def format(self, record: logging.LogRecord) -> str:
        base = (
            f"{self.formatTime(record)} {record.levelname:<8} {record.name} {record.getMessage()}"
        )
        request_id = getattr(record, "request_id", None)
        if request_id:
            base = f"{base} [request_id={request_id}]"
        if record.exc_info:
            base = f"{base}\n{self.formatException(record.exc_info)}"
        return base


def configure_logging(*, level: str, as_json: bool) -> None:
    """Configure the root logger. Idempotent."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if as_json else TextFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Uvicorn installs its own handlers; align them with ours.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
