"""The ``second-approver`` obligation is enforced, not merely declared (NEX-059).

The security architecture lists ``second-approver`` in the obligation vocabulary,
and until this slice the platform only *parsed* it: ``PolicyObligation`` had the
member, the gateway could name it, and no code path ever demanded a second human.
A policy author who published a two-person control got a control that read as
enforced in the audit trail and behaved as a one-person grant — the worst of both
worlds, because the record says "two people approved" when one did.

Three properties have to hold together for the obligation to be real, and each is
pinned here:

* **One signature is not an approval.** A request needing two approvers stays
  ``PENDING`` after the first signature, and the gateway refuses the call. The
  status is what ``is_usable`` reads, so marking it ``APPROVED`` on one signature
  would be a live grant of the control's authority to a single person.
* **The second signature must be a different human.** Separation of duties is
  checked in the aggregate, where every decision path must pass, not in the use
  case where one path could forget it.
* **The threshold is persisted and enforced at spend.** A stale or tampered row
  cannot spend an approval with fewer signatures than the policy demanded, and a
  half-approved row is not spendable. The guard is in the SQL predicate, so a
  concurrent consumer cannot bypass it by reading a stale domain object.

Run against the real gateway, unit of work and note use cases; only the policy
store is in-memory, because the subject is the enforcement path.
"""

from __future__ import annotations

import pytest

from notes_service.application.errors import AccessDenied
from notes_service.domain.agent.tools import InvocationStatus
from notes_service.domain.agent.value_objects import Capability
from notes_service.domain.errors import InvariantViolation
from notes_service.domain.governance.policy import (
    AttributeRef,
    AttributeType,
    Comparison,
    PolicyEffect,
    PolicyObligation,
    PolicyRule,
)
from tests.agent.test_tool_gateway_policy import Harness, _publish
from tests.agent.test_tool_gateway_policy import h as h
from tests.conftest import BOB, CAROL

pytestmark = pytest.mark.usefixtures("seeded")


def _allow_delete_with_second_approver() -> PolicyRule:
    return PolicyRule(
        rule_id="delete-needs-two",
        effect=PolicyEffect.ALLOW,
        when=Comparison(
            attribute=AttributeRef(name="tool", type=AttributeType.STRING),
            operator="==",
            value="delete_note",
        ),
        obligations=(PolicyObligation.SECOND_APPROVER,),
    )


def _request_delete(h: Harness):  # type: ignore[no-untyped-def]
    """Open a two-person approval by making the gated call once, then return it."""
    agent, secret = h.secret_for(Capability.DELETE_NOTE)
    note = h.create_note.run(h.principal, title="Doomed", content="body")
    with pytest.raises(AccessDenied, match="requires human approval"):
        h.gateway.run(
            secret=secret,
            tool_name="delete_note",
            arguments={"note_id": str(note.id)},
            idempotency_key="k-two-person-open",
        )
    with h.uow() as uow:
        pending = uow.approvals.list_pending_for_agent(agent.id)
        assert len(pending) == 1
        request = pending[0]
    return agent, secret, note, request


class TestSecondApproverIsRecordedOnTheRequest:
    def test_the_policy_threshold_is_stamped_on_the_request(self, h: Harness) -> None:
        # The threshold travels with the request rather than being re-derived at
        # spend time. A policy that is later replaced must not retroactively
        # weaken a request that was opened under the stricter rule.
        _publish(h, (_allow_delete_with_second_approver(),))
        _, _, _, request = _request_delete(h)
        assert request.required_approvals == 2

    def test_a_plain_approval_policy_still_asks_for_one(self, h: Harness) -> None:
        # The default must not drift: an ``approval`` obligation is a one-person
        # gate, and it would be a regression to demand two signatures for it.
        _publish(
            h,
            (
                PolicyRule(
                    rule_id="delete-needs-one",
                    effect=PolicyEffect.ALLOW,
                    when=Comparison(
                        attribute=AttributeRef(name="tool", type=AttributeType.STRING),
                        operator="==",
                        value="delete_note",
                    ),
                    obligations=(PolicyObligation.APPROVAL,),
                ),
            ),
        )
        _, _, _, request = _request_delete(h)
        assert request.required_approvals == 1


class TestOneSignatureIsNotAnApproval:
    def test_the_first_signature_leaves_the_request_pending(self, h: Harness) -> None:
        _publish(h, (_allow_delete_with_second_approver(),))
        agent, secret, note, _ = _request_delete(h)

        with h.uow() as uow:
            stored = uow.approvals.list_pending_for_agent(agent.id)[0]
            stored.approve(approver_user_id=BOB, now=stored.requested_at)
            uow.approvals.save(stored)
            uow.commit()

        with h.uow() as uow:
            reloaded = uow.approvals.list_pending_for_agent(agent.id)[0]
        # Recorded, but not an approval: the status is what the gateway reads.
        assert reloaded.decided_by_user_id is not None
        assert reloaded.status.value == "pending"

        # A half-approved request must not authorise the action.
        with pytest.raises(AccessDenied, match="requires human approval"):
            h.gateway.run(
                secret=secret,
                tool_name="delete_note",
                arguments={"note_id": str(note.id)},
                idempotency_key="k-two-person-half",
            )
        assert h.list_notes.run(h.principal).total == 1

    def test_two_distinct_approvers_authorise_the_action(self, h: Harness) -> None:
        # The positive path: two different humans, and the gated action runs.
        _publish(h, (_allow_delete_with_second_approver(),))
        agent, secret, note, _ = _request_delete(h)

        with h.uow() as uow:
            stored = uow.approvals.list_pending_for_agent(agent.id)[0]
            stored.approve(approver_user_id=BOB, now=stored.requested_at)
            stored.approve(approver_user_id=CAROL, now=stored.requested_at)
            uow.approvals.save(stored)
            uow.commit()

        with h.uow() as uow:
            approved = uow.approvals.list_for_agent(agent.id)[0]
        assert approved.status.value == "approved"
        assert approved.decided_by_user_id is not None
        assert approved.second_approver_user_id is not None
        assert approved.decided_by_user_id != approved.second_approver_user_id

        result = h.gateway.run(
            secret=secret,
            tool_name="delete_note",
            arguments={"note_id": str(note.id)},
            idempotency_key="k-two-person-done",
        )
        assert result is not None
        assert h.list_notes.run(h.principal).total == 0


class TestSeparationOfDuties:
    def test_the_same_human_cannot_sign_twice(self, h: Harness) -> None:
        # A second click by the first approver is not a second approver. If this
        # passed, the whole obligation would be a formality a single person could
        # satisfy alone while the audit trail claimed two.

        _publish(h, (_allow_delete_with_second_approver(),))
        agent, _secret, _note, _ = _request_delete(h)

        with h.uow() as uow:
            stored = uow.approvals.list_pending_for_agent(agent.id)[0]
            stored.approve(approver_user_id=BOB, now=stored.requested_at)
            with pytest.raises(InvariantViolation, match="different human"):
                stored.approve(approver_user_id=BOB, now=stored.requested_at)
        assert h.list_notes.run(h.principal).total == 1


class TestThresholdCannotBeWeakened:
    def test_a_half_approved_request_cannot_be_raised_into_reuse(self, h: Harness) -> None:
        # Raising the threshold is how a tightening policy is honoured on an open
        # request. It must refuse once a signature exists: silently un-approving a
        # human's recorded decision would rewrite history.

        _publish(
            h,
            (
                PolicyRule(
                    rule_id="delete-needs-one",
                    effect=PolicyEffect.ALLOW,
                    when=Comparison(
                        attribute=AttributeRef(name="tool", type=AttributeType.STRING),
                        operator="==",
                        value="delete_note",
                    ),
                    obligations=(PolicyObligation.APPROVAL,),
                ),
            ),
        )
        _, _, _, request = _request_delete(h)
        assert request.required_approvals == 1
        request.approve(approver_user_id=BOB)
        with pytest.raises(InvariantViolation, match="already carries a signature"):
            request.raise_required_approvals(2)

    def test_raising_an_unsigned_request_to_two_is_allowed(self, h: Harness) -> None:
        # A policy that tightens while a request is open escalates it in place,
        # so the human is asked for the number of signatures the current rule
        # demands rather than the number the old rule demanded.
        _publish(h, (_allow_delete_with_second_approver(),))
        agent, _, _, _ = _request_delete(h)
        with h.uow() as uow:
            request = uow.approvals.list_pending_for_agent(agent.id)[0]
            assert request.required_approvals == 2
            # Already at two, so raising again is a no-op rather than an error.
            request.raise_required_approvals(2)
            assert request.required_approvals == 2

    def test_lowering_the_threshold_is_refused(self, h: Harness) -> None:
        # A relaxed policy is a reason to open a new request, not to rewrite a
        # decision that was recorded at the stricter threshold.

        _publish(h, (_allow_delete_with_second_approver(),))
        _, _, _, request = _request_delete(h)
        with pytest.raises(InvariantViolation, match="never lowered"):
            request.raise_required_approvals(1)


class TestSpendGuardFailsClosed:
    def test_a_pending_two_person_row_is_not_spendable(self, h: Harness) -> None:
        # The guard is in the SQL predicate, so a consumer cannot spend a row that
        # still needs a signature even if it read a stale domain object.
        _publish(h, (_allow_delete_with_second_approver(),))
        agent, _, _, _ = _request_delete(h)

        with h.uow() as uow:
            request = uow.approvals.list_pending_for_agent(agent.id)[0]
            assert uow.approvals.consume(request.id, now=request.requested_at) is False

    def test_a_fully_signed_two_person_row_is_spendable_once(self, h: Harness) -> None:
        _publish(h, (_allow_delete_with_second_approver(),))
        agent, _, _, _ = _request_delete(h)

        with h.uow() as uow:
            stored = uow.approvals.list_pending_for_agent(agent.id)[0]
            stored.approve(approver_user_id=BOB, now=stored.requested_at)
            stored.approve(approver_user_id=CAROL, now=stored.requested_at)
            uow.approvals.save(stored)
            uow.commit()

        with h.uow() as uow:
            request = uow.approvals.list_for_agent(agent.id)[0]
            assert uow.approvals.consume(request.id, now=request.requested_at) is True
            # Spent once, and only once.
            assert uow.approvals.consume(request.id, now=request.requested_at) is False


class TestGatewayAuditsTheGate:
    def test_the_deferral_is_audited_against_the_request(self, h: Harness) -> None:
        _publish(h, (_allow_delete_with_second_approver(),))
        agent, _, _, request = _request_delete(h)

        recorded = h.invocations(agent)
        assert len(recorded) == 1
        assert recorded[0].status is InvocationStatus.DENIED
        assert recorded[0].approval_request_id == request.id
