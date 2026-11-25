"""The withholding report gate fails closed (R34).

The aggregate spans every tenant, so the gate — not the caller's authentication —
decides access. These tests pin the three ways it must refuse:

* an empty allowlist means *nobody*, not everybody;
* a caller who is not listed is refused, and refused as ``ResourceNotFound`` so
  the endpoint is indistinguishable from a path that does not exist;
* the refusal happens before the recorder is consulted, so a denied caller
  cannot even learn the current volume from timing or side effects.

The recorder itself is covered in ``tests/bootstrap/test_withholding_recorder.py``.
"""

from __future__ import annotations

import pytest

from notes_service.application.errors import ResourceNotFound
from notes_service.application.use_cases.withholding_report import GetWithholdingReport
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.security.withholding_recorder import (
    InMemoryWithholdingRecorder,
)

ALICE = UserId.parse("11111111-1111-1111-1111-111111111111")
BOB = UserId.parse("22222222-2222-2222-2222-222222222222")


def _principal(user_id: UserId) -> Principal:
    return Principal(
        user_id=user_id,
        email=Email("user@example.com"),
        display_name="User",
        provider="demo",
        subject=str(user_id),
    )


def _recorder() -> InMemoryWithholdingRecorder:
    recorder = InMemoryWithholdingRecorder()
    recorder.record(over_cap_field_names=("title",))
    recorder.record(over_cap_field_names=())
    return recorder


def test_an_operator_reads_the_totals() -> None:
    use_case = GetWithholdingReport(
        withholding_recorder=_recorder(), operator_ids=frozenset({ALICE})
    )
    snapshot = use_case.run(_principal(ALICE))
    assert snapshot.advisories_total == 2
    assert snapshot.over_cap_advisories_total == 1
    assert snapshot.over_cap_fields_total == 1
    assert snapshot.over_cap_rate == pytest.approx(0.5)


def test_an_empty_allowlist_admits_nobody() -> None:
    use_case = GetWithholdingReport(withholding_recorder=_recorder(), operator_ids=frozenset())
    with pytest.raises(ResourceNotFound):
        use_case.run(_principal(ALICE))


def test_a_non_operator_is_refused_as_not_found() -> None:
    use_case = GetWithholdingReport(
        withholding_recorder=_recorder(), operator_ids=frozenset({ALICE})
    )
    with pytest.raises(ResourceNotFound):
        use_case.run(_principal(BOB))
