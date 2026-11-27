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

# Words that name a credential on their own, in any position and in any
# combination with other words. Matched against the *segments* of a key, not
# against the whole key: ``client_secret``, ``accessToken`` and ``tokens`` are
# the same disclosure as ``secret``, ``token`` and ``token``.
_SENSITIVE_WORDS = frozenset(
    {
        "password",
        "passwd",
        "passphrase",
        "secret",
        "token",
        "cookie",
        "credential",
        "authorization",
        "bearer",
        "apikey",
    }
)

# Pairs where neither word is sensitive alone but the combination is. ``key`` on
# its own is ordinary (``idempotency_key``, ``partition_key``); ``private_key``
# and ``api_key`` are not.
_SENSITIVE_WORD_PAIRS = frozenset(
    {
        ("api", "key"),
        ("private", "key"),
        ("access", "key"),
        ("secret", "key"),
        ("signing", "key"),
        ("encryption", "key"),
    }
)

# ``content`` holds subject material, but the words that routinely accompany it
# in a *descriptor* do not. ``content_type`` and ``content_length`` describe a
# payload; ``content`` and ``note_content`` are the payload.
_CONTENT_WORD = "content"
_CONTENT_DESCRIPTOR_WORDS = frozenset({"type", "length", "encoding", "hash", "size"})


def _key_words(key: str) -> list[str]:
    """Split a key into lowercase words across separators and camelCase.

    ``accessToken``, ``access_token`` and ``ACCESS-TOKEN`` must all reach the
    same verdict, because a caller picks the spelling without thinking about
    redaction and a matcher that only knows one spelling is a matcher with a
    hole.
    """
    words: list[str] = []
    current = ""
    previous_lower = False
    for char in key:
        if char.isalnum():
            if char.isupper() and previous_lower:
                if current:
                    words.append(current)
                current = char.lower()
            else:
                current += char.lower()
            previous_lower = char.islower() or char.isdigit()
        else:
            if current:
                words.append(current)
            current = ""
            previous_lower = False
    if current:
        words.append(current)
    return words


def _is_sensitive_key(key: str) -> bool:
    """Whether a key names a credential, by shape rather than by exact spelling.

    The predecessor of this function was a set of eleven exact strings. Every
    credential spelled differently — ``client_secret``, ``private_key``,
    ``x-api-key``, ``accessToken``, ``session_cookie`` — passed through it
    untouched, so the module's "sensitive data must never be logged" was
    enforced only for the spellings someone had happened to list. That is the
    same "enumerate what you know, treat the unknown as safe" shape as the
    six-key log allowlist this file replaced.
    """
    words = _key_words(key)
    if not words:
        return False
    for word in words:
        if word in _SENSITIVE_WORDS:
            return True
        # A plural is the same secret: ``tokens``, ``cookies``, ``credentials``.
        if word.endswith("s") and word[:-1] in _SENSITIVE_WORDS:
            return True
    if len(words) >= 2 and tuple(words[-2:]) in _SENSITIVE_WORD_PAIRS:
        return True
    return _CONTENT_WORD in words and not _CONTENT_DESCRIPTOR_WORDS.intersection(words)


def redact(mapping: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``mapping`` with sensitive values replaced.

    Recurses into nested mappings and sequences because a structured payload is
    routinely nested (``{"auth": {"token": ...}}``) and a shallow pass would
    leave the credential one level down in the output.
    """
    return {key: _redact_value(key, value) for key, value in mapping.items()}


def _redact_value(key: str, value: Any) -> Any:
    if _is_sensitive_key(key):
        # The whole value goes, whatever its shape. A list under ``tokens`` is a
        # list of credentials, and replacing each element one at a time would
        # keep the count and the ordering — two facts about a secret set that
        # need not survive into a log line.
        return REDACTED
    if isinstance(value, dict):
        return redact(value)
    if isinstance(value, (list, tuple)):
        return [_redact_value(key, item) for item in value]
    return value


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
