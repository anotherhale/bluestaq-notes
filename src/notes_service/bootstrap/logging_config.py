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

# The redaction judgement lives in the domain (``notes_service.domain.redaction``)
# because the tool gateway's redaction obligation needs the same answer as the log
# formatter. Re-exported here so existing importers keep working.
from notes_service.domain.redaction import REDACTED as REDACTED
from notes_service.domain.redaction import redact as redact

#: Attributes every :class:`logging.LogRecord` carries. Anything else on a record
#: was attached deliberately by the caller via ``extra={...}`` and is a structured
#: field, not formatting state.
_RESERVED_RECORD_KEYS = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "message",
        "module",
        "msecs",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)


def _structured_extras(record: logging.LogRecord) -> dict[str, Any]:
    """Every caller-supplied ``extra`` field on ``record``, redacted.

    Deliberately a denylist of reserved attributes rather than an allowlist of
    known keys. The allowlist this replaces named six fields, so any other
    structured value a caller attached — an over-cap count, a capability name, a
    correlation id under a new key — was emitted into a log call and then
    silently dropped by the formatter. A field that a caller cannot read back is
    not telemetry; it is the appearance of it.
    """
    extras = {
        key: value
        for key, value in record.__dict__.items()
        if key not in _RESERVED_RECORD_KEYS and not key.startswith("_")
    }
    return redact(extras)


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
        payload.update(_structured_extras(record))
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
