"""The structured log formatter is the only path a structured field takes.

A log call is not telemetry until the formatter emits it. ``JsonFormatter``
previously built its payload from a fixed six-key allowlist, so every other
``extra={...}`` value — an over-cap count, a capability name, a correlation id
under a new key — was attached to the record and then discarded. The call looked
successful and the field never reached the log, which is the worst shape for an
observability defect: silence that reads as health.

These tests pin the two properties that make the formatter trustworthy:

1. **Nothing is dropped.** An arbitrary structured field a caller attaches is
   emitted. Pinned with a key that did not exist when the allowlist was written.
2. **Nothing sensitive leaks.** ``redact`` is now on the formatting path, so a
   secret in ``extra`` is emitted as ``<redacted>``, including one level down in
   a nested mapping. ``redact`` had zero callers before this slice, so the
   module docstring's "sensitive data must never be logged" was a claim with no
   enforcement behind it.
"""

from __future__ import annotations

import json
import logging

from notes_service.bootstrap.logging_config import REDACTED, JsonFormatter, redact


def _record(**extra: object) -> logging.LogRecord:
    record = logging.LogRecord(
        name="notes_service.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="something happened",
        args=(),
        exc_info=None,
    )
    for key, value in extra.items():
        setattr(record, key, value)
    return record


def _formatted(**extra: object) -> dict[str, object]:
    return json.loads(JsonFormatter().format(_record(**extra)))


def test_formatter_emits_an_arbitrary_structured_field() -> None:
    """A field the allowlist never knew about must still reach the log.

    ``over_cap_fields`` is exactly the kind of value the old six-key allowlist
    silently dropped: attached by the advisory use case, absent from the output.
    """
    payload = _formatted(over_cap_fields=2, capability="share_note")

    assert payload["over_cap_fields"] == 2
    assert payload["capability"] == "share_note"


def test_formatter_keeps_the_envelope_fields() -> None:
    """The fix must not cost the fields that already worked."""
    payload = _formatted()

    assert payload["message"] == "something happened"
    assert payload["level"] == "INFO"
    assert payload["logger"] == "notes_service.test"
    assert "timestamp" in payload


def test_formatter_redacts_a_sensitive_field() -> None:
    """``redact`` is on the formatting path, so a token cannot be logged raw."""
    payload = _formatted(token="super-secret-value", over_cap_fields=1)

    assert payload["token"] == REDACTED
    assert "super-secret-value" not in json.dumps(payload)
    # Redaction is surgical: the non-sensitive sibling survives.
    assert payload["over_cap_fields"] == 1


def test_redact_reaches_into_nested_mappings() -> None:
    """A credential one level down is still a credential.

    A shallow pass would leave ``{"auth": {"token": ...}}`` in the output, which
    is the common shape for a client object attached to a log line.
    """
    redacted = redact({"auth": {"token": "abc", "user": "alice"}})

    assert redacted["auth"]["token"] == REDACTED
    assert redacted["auth"]["user"] == "alice"


def test_redact_reaches_into_sequences() -> None:
    """A list of secrets is a list of secrets."""
    redacted = redact({"items": [{"api_key": "k1"}, {"api_key": "k2"}]})

    assert redacted["items"] == [{"api_key": REDACTED}, {"api_key": REDACTED}]


def test_formatter_does_not_emit_logging_internals() -> None:
    """Reserved attributes are formatting state, not structured fields."""
    payload = _formatted()

    for reserved in ("msg", "args", "levelno", "pathname", "exc_info", "lineno"):
        assert reserved not in payload
