"""Policy obligations enforced at the tool gateway (NEX-053).

The security architecture lists obligations next to allow and deny (section 5:
an obligation "must be satisfied before the operation proceeds"; section 8:
"obligations applied to allowed decisions"). NEX-051 enforced deny and ignored
everything else, so an allow rule could only ever *say* "requires approval" — the
call went through as if the obligation did not exist.

These tests pin the three ways obligations can be got wrong:

* **An unsatisfied obligation must not be an allow.** An ``approval`` obligation
  has to route the call through the real human-approval flow, so the handler does
  not run until a person has approved it.
* **An obligation with no enforcement adapter must refuse, not be dropped.** The
  gateway can satisfy ``approval``; it has no adapter for ``step-up``,
  ``redaction``, ``second-approver``, ``rate-limit`` or ``enhanced-audit``.
  Proceeding anyway would make the policy read as enforced when it is not, so the
  call is refused and audited. This is the fail-closed half of the control.
* **Deny still wins.** Obligations on an allow rule that also matched a deny rule
  are irrelevant: the action is refused, and no obligation is reported as a way
  to proceed.

Run against the real gateway, unit of work and note use cases; only the policy
store is in-memory, because the subject is the enforcement path.
"""

from __future__ import annotations

import pytest

from notes_service.application.errors import AccessDenied
from notes_service.domain.agent.tools import InvocationStatus
from notes_service.domain.agent.value_objects import Capability
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

pytestmark = pytest.mark.usefixtures("seeded")


def _allow_tool(
    tool_name: str, *obligations: PolicyObligation, rule_id: str = "allow-tool"
) -> PolicyRule:
    return PolicyRule(
        rule_id=rule_id,
        effect=PolicyEffect.ALLOW,
        when=Comparison(
            attribute=AttributeRef(name="tool", type=AttributeType.STRING),
            operator="==",
            value=tool_name,
        ),
        obligations=obligations,
    )


class TestApprovalObligationIsEnforced:
    def test_an_approval_obligation_defers_a_sensitive_tool(self, h: Harness) -> None:
        # ``delete_note`` is human-gated anyway, so the interesting assertion is
        # that the obligation is *carried through* to the approval rather than
        # short-circuited: no deletion, and a request waiting for a person.
        _publish(h, (_allow_tool("delete_note", PolicyObligation.APPROVAL),))
        agent, secret = h.secret_for(Capability.DELETE_NOTE)
        note = h.create_note.run(h.principal, title="T", content="C")

        with pytest.raises(AccessDenied, match="requires human approval"):
            h.gateway.run(
                secret=secret,
                tool_name="delete_note",
                arguments={"note_id": str(note.id)},
                idempotency_key="k-approval",
            )

        with h.uow() as uow:
            pending = uow.approvals.list_pending_for_agent(agent.id)
            assert len(pending) == 1
        recorded = h.invocations(agent)
        assert [r.status for r in recorded] == [InvocationStatus.DENIED]
        assert recorded[0].approval_request_id is not None
        assert h.list_notes.run(h.principal).total == 1

    def test_an_approval_obligation_cannot_gate_a_non_sensitive_tool(self, h: Harness) -> None:
        # The domain refuses to open an approval request for a capability that is
        # not sensitive, so a policy cannot *invent* a human gate for a tool the
        # platform does not consider human-gated. The call must refuse cleanly
        # rather than raise an invariant error out of the handler, and the refusal
        # must be audited.
        _publish(h, (_allow_tool("create_note", PolicyObligation.APPROVAL),))
        agent, secret = h.secret_for(Capability.CREATE_NOTE)

        with pytest.raises(AccessDenied, match="cannot satisfy"):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": "T", "content": "C"},
                idempotency_key="k-approval-nonsensitive",
            )

        assert h.list_notes.run(h.principal).total == 0
        with h.uow() as uow:
            assert uow.approvals.list_pending_for_agent(agent.id) == []
        recorded = h.invocations(agent)
        assert len(recorded) == 1
        assert recorded[0].status is InvocationStatus.DENIED
        assert "approval" in (recorded[0].outcome or "")


class TestUnsatisfiableObligationsFailClosed:
    @pytest.mark.parametrize(
        "obligation",
        [
            PolicyObligation.STEP_UP,
            PolicyObligation.SECOND_APPROVER,
            PolicyObligation.REDACTION,
            PolicyObligation.RATE_LIMIT,
            PolicyObligation.ENHANCED_AUDIT,
        ],
    )
    def test_an_obligation_with_no_adapter_refuses_the_call(
        self, h: Harness, obligation: PolicyObligation
    ) -> None:
        # The dangerous outcome is not a refusal: it is running the tool while
        # quietly ignoring the obligation. The refusal names the obligation so an
        # operator can see why, and it is audited as DENIED.
        _publish(h, (_allow_tool("create_note", obligation),))
        agent, secret = h.secret_for(Capability.CREATE_NOTE)

        with pytest.raises(AccessDenied, match=obligation.value):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": "T", "content": "C"},
                idempotency_key=f"k-{obligation.value}",
            )

        assert h.list_notes.run(h.principal).total == 0
        recorded = h.invocations(agent)
        assert len(recorded) == 1
        assert recorded[0].status is InvocationStatus.DENIED
        assert obligation.value in (recorded[0].outcome or "")

    def test_one_unsatisfiable_obligation_taints_the_whole_allow(self, h: Harness) -> None:
        # ``approval`` is satisfiable for a sensitive tool on its own, but the call
        # as a whole is not: every obligation must be met, so the set is refused
        # rather than partially satisfied. Otherwise an author could smuggle an
        # unenforceable requirement alongside a real one and have it silently
        # skipped. The satisfiable obligation must not leave a stray approval open.
        _publish(
            h,
            (
                _allow_tool(
                    "delete_note",
                    PolicyObligation.APPROVAL,
                    PolicyObligation.REDACTION,
                ),
            ),
        )
        agent, secret = h.secret_for(Capability.DELETE_NOTE)
        note = h.create_note.run(h.principal, title="T", content="C")

        with pytest.raises(AccessDenied, match="redaction"):
            h.gateway.run(
                secret=secret,
                tool_name="delete_note",
                arguments={"note_id": str(note.id)},
                idempotency_key="k-mixed",
            )

        with h.uow() as uow:
            assert uow.approvals.list_pending_for_agent(agent.id) == []
        assert h.list_notes.run(h.principal).total == 1


class TestDenyStillWins:
    def test_obligations_on_a_denied_call_are_not_a_way_through(self, h: Harness) -> None:
        # An allow-with-obligation and a deny both match. The deny wins and no
        # obligation is reported, because there is nothing to satisfy: reporting
        # one would suggest that satisfying it would let the call proceed.
        _publish(
            h,
            (
                _allow_tool("create_note", PolicyObligation.APPROVAL),
                PolicyRule(
                    rule_id="deny-tool",
                    effect=PolicyEffect.DENY,
                    when=Comparison(
                        attribute=AttributeRef(name="tool", type=AttributeType.STRING),
                        operator="==",
                        value="create_note",
                    ),
                ),
            ),
        )
        agent, secret = h.secret_for(Capability.CREATE_NOTE)

        with pytest.raises(AccessDenied, match="refused by published policy"):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": "T", "content": "C"},
                idempotency_key="k-deny-wins",
            )

        assert h.list_notes.run(h.principal).total == 0
        assert [r.status for r in h.invocations(agent)] == [InvocationStatus.DENIED]


class TestObligationRoundTrip:
    def test_obligations_survive_the_store(self, h: Harness) -> None:
        # The obligation has to survive encode/decode or the gateway would enforce
        # a policy that reads differently from the one that was published.
        rule = _allow_tool("create_note", PolicyObligation.APPROVAL)
        _publish(h, (rule,))

        stored = h.policies.all_published()
        assert len(stored) == 1
        assert stored[0].rules[0].obligations == (PolicyObligation.APPROVAL,)


class TestObligationDomainRules:
    def test_a_deny_rule_cannot_carry_obligations(self) -> None:
        # An obligation on a deny can never be satisfied and would be dropped, so
        # the author must be told at construction time instead.
        from notes_service.domain.errors import ValidationError

        with pytest.raises(ValidationError, match="cannot carry obligations"):
            PolicyRule(
                rule_id="deny-with-obligation",
                effect=PolicyEffect.DENY,
                obligations=(PolicyObligation.APPROVAL,),
            )

    def test_an_unknown_obligation_is_refused(self) -> None:
        from notes_service.domain.errors import ValidationError

        with pytest.raises(ValidationError, match="closed vocabulary"):
            PolicyRule(
                rule_id="bad",
                effect=PolicyEffect.ALLOW,
                obligations=("delete-everything",),  # type: ignore[arg-type]
            )

    def test_a_duplicate_obligation_is_refused(self) -> None:
        from notes_service.domain.errors import ValidationError

        with pytest.raises(ValidationError, match="duplicate obligation"):
            PolicyRule(
                rule_id="dup",
                effect=PolicyEffect.ALLOW,
                obligations=(PolicyObligation.APPROVAL, PolicyObligation.APPROVAL),
            )


class TestDecisionIsSatisfied:
    def test_allowed_with_obligations_is_not_satisfied(self) -> None:
        # The property exists so a caller cannot read ``allowed`` and stop. If this
        # ever returned True for an obligated allow, the gateway's obligation branch
        # would be dead code that still looked correct.
        from notes_service.domain.governance.policy import PolicyDecision

        obligated = PolicyDecision(
            effect=PolicyEffect.ALLOW, obligations=(PolicyObligation.APPROVAL,)
        )
        plain = PolicyDecision(effect=PolicyEffect.ALLOW)

        assert obligated.allowed is True
        assert obligated.is_satisfied is False
        assert plain.is_satisfied is True
