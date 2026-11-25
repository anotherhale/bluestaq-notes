"""The over-cap count: a rate, so only a count can show it.

NEX-032 made a single degraded advisory visible — the response names the fields
it withheld for size. That is necessary and not sufficient: a note title that is
*routinely* over the context cap degrades every advisory quietly, and no single
response is wrong enough to notice. The failure is a rate, so only a count
against a full denominator can show it.

The use-case half of this property (a real advisory incrementing the count) lives
in ``tests/agent/test_approval_advisory.py``, where the harness and its
``recorder`` field already are.
"""

from __future__ import annotations

import inspect

import pytest

from notes_service.infrastructure.security.withholding_recorder import (
    InMemoryWithholdingRecorder,
)


def test_an_empty_recorder_reports_a_zero_rate() -> None:
    """No advisories is not a 100% failure rate; it is no data."""
    snapshot = InMemoryWithholdingRecorder().snapshot()

    assert snapshot.advisories_total == 0
    assert snapshot.over_cap_rate == 0.0


def test_recorder_counts_fields_and_advisories() -> None:
    recorder = InMemoryWithholdingRecorder()

    recorder.record(over_cap_field_names=("note_title", "note_body"))
    recorder.record(over_cap_field_names=("note_title",))
    recorder.record(over_cap_field_names=())

    snapshot = recorder.snapshot()
    assert snapshot.advisories_total == 3
    assert snapshot.over_cap_advisories_total == 2
    assert snapshot.over_cap_fields_total == 3
    assert snapshot.over_cap_rate == pytest.approx(2 / 3)


def test_a_healthy_advisory_keeps_the_rate_honest() -> None:
    """One degraded in four is 0.25, not 1.0 — the denominator is all calls.

    A rate computed over only the degraded advisories would always be 1.0 and
    would carry no information at all.
    """
    recorder = InMemoryWithholdingRecorder()

    recorder.record(over_cap_field_names=("note_title",))
    for _ in range(3):
        recorder.record(over_cap_field_names=())

    assert recorder.snapshot().over_cap_rate == pytest.approx(0.25)


def test_the_recorder_can_only_receive_field_names() -> None:
    """The signature cannot carry a value, so a withheld value cannot leak in."""
    signature = inspect.signature(InMemoryWithholdingRecorder.record)

    assert list(signature.parameters) == ["self", "over_cap_field_names"]
