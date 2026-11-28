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
* **An obligation with no adapter for this call must refuse, not be dropped.** The
  gateway can satisfy ``approval`` (for a human-gated capability), ``second-approver``
  (the same flow with a two-distinct-human threshold, NEX-059), ``redaction`` (a real
  output filter, NEX-054), ``rate-limit`` (NEX-056) and ``step-up`` (against the
  assurance recorded on the delegation, NEX-058). What remains unenforceable is a
  *mismatch*: a human gate named for a capability the domain does not treat as
  sensitive, or ``rate-limit`` with no limiter wired. Proceeding anyway would make
  the policy read as enforced when it is not, so the call is refused and audited.
  This is the fail-closed half of the control.
* **Deny still wins.** Obligations on an allow rule that also matched a deny rule
  are irrelevant: the action is refused, and no obligation is reported as a way
  to proceed.

Run against the real gateway, unit of work and note use cases; only the policy
store is in-memory, because the subject is the enforcement path.
"""

from __future__ import annotations

import dataclasses

import pytest

from notes_service.application.errors import AccessDenied, RateLimited
from notes_service.application.use_cases.tools import InvokeTool
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
from notes_service.domain.identity.assurance import AuthenticationAssurance
from notes_service.domain.redaction import REDACTED
from notes_service.infrastructure.security.clock import SystemClock
from tests.agent.test_tool_gateway_policy import (
    RATE_LIMIT_BOUND,
    TOOL_GATEWAY,
    Harness,
    _publish,
)
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


class TestRateLimitObligationIsEnforced:
    """NEX-056: ``rate-limit`` has a real adapter, not just a refusal path."""

    def test_calls_within_the_bound_are_allowed(self, h: Harness) -> None:
        # The bound is 3 per window in the harness. Two calls must pass: an adapter
        # that refused the *first* call would satisfy the fail-closed tests while
        # making the obligation unusable, so the permitted path is asserted
        # explicitly rather than inferred from a refusal elsewhere.
        _publish(h, (_allow_tool("create_note", PolicyObligation.RATE_LIMIT),))
        _agent, secret = h.secret_for(Capability.CREATE_NOTE)

        for index in range(2):
            result = h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": f"T{index}", "content": "C"},
                idempotency_key=f"k-ok-{index}",
            )
            assert result.invocation.status is InvocationStatus.SUCCEEDED

        assert h.list_notes.run(h.principal).total == 2

    def test_exceeding_the_bound_refuses_the_call(self, h: Harness) -> None:
        # The refusal must be a ``RateLimited`` carrying the retry window, so the
        # boundary can render a 429 with ``Retry-After`` rather than a generic
        # denial the caller cannot act on. The note is not created.
        _publish(h, (_allow_tool("create_note", PolicyObligation.RATE_LIMIT),))
        _agent, secret = h.secret_for(Capability.CREATE_NOTE)

        for index in range(RATE_LIMIT_BOUND):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": f"T{index}", "content": "C"},
                idempotency_key=f"k-fill-{index}",
            )

        with pytest.raises(RateLimited) as excinfo:
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": "over", "content": "C"},
                idempotency_key="k-over",
            )

        assert excinfo.value.retry_after >= 1
        assert "rate-limit" in str(excinfo.value)
        assert h.list_notes.run(h.principal).total == RATE_LIMIT_BOUND

    def test_a_throttled_call_is_audited_as_denied(self, h: Harness) -> None:
        # A throttled agent must be visible in the trail, not only in the 429 the
        # caller saw. The row names the obligation so an auditor can tell a policy
        # bound apart from a capability denial.
        _publish(h, (_allow_tool("create_note", PolicyObligation.RATE_LIMIT),))
        agent, secret = h.secret_for(Capability.CREATE_NOTE)

        for index in range(RATE_LIMIT_BOUND):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": f"T{index}", "content": "C"},
                idempotency_key=f"k-fill-{index}",
            )
        with pytest.raises(RateLimited):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": "over", "content": "C"},
                idempotency_key="k-over",
            )

        denied = [r for r in h.invocations(agent) if r.status is InvocationStatus.DENIED]
        assert len(denied) == 1
        assert "rate-limit" in (denied[0].outcome or "")

    def test_the_bound_is_per_tool_not_per_agent(self, h: Harness) -> None:
        # Two capabilities on one agent must not share a counter. If they did, a
        # hot tool would exhaust the budget of every other tool the same agent may
        # call, which is a different (and much broader) rule than the policy states.
        _publish(
            h,
            (
                _allow_tool("create_note", PolicyObligation.RATE_LIMIT, rule_id="rl-create"),
                _allow_tool("list_shared_notes", PolicyObligation.RATE_LIMIT, rule_id="rl-list"),
            ),
        )
        _agent, secret = h.secret_for(Capability.CREATE_NOTE, Capability.LIST_SHARED_NOTES)

        for index in range(RATE_LIMIT_BOUND):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": f"T{index}", "content": "C"},
                idempotency_key=f"k-create-{index}",
            )

        # ``create_note`` is exhausted; ``list_shared_notes`` has its own budget.
        with pytest.raises(RateLimited):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": "over", "content": "C"},
                idempotency_key="k-create-over",
            )

        result = h.gateway.run(
            secret=secret,
            tool_name="list_shared_notes",
            arguments={},
            idempotency_key="k-list-0",
        )
        assert result.invocation.status is InvocationStatus.SUCCEEDED

    def test_an_idempotent_retry_does_not_consume_the_bound(self, h: Harness) -> None:
        # A retry under a recorded key replays a decision already made. Charging it
        # again would make a network retry indistinguishable from abuse, and a
        # caller that retried its last permitted call would be throttled for it.
        _publish(h, (_allow_tool("create_note", PolicyObligation.RATE_LIMIT),))
        _agent, secret = h.secret_for(Capability.CREATE_NOTE)
        arguments = {"title": "T", "content": "C"}

        for index in range(RATE_LIMIT_BOUND - 1):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": f"T{index}", "content": "C"},
                idempotency_key=f"k-fill-{index}",
            )

        first = h.gateway.run(
            secret=secret,
            tool_name="create_note",
            arguments=arguments,
            idempotency_key="k-last",
        )
        replay = h.gateway.run(
            secret=secret,
            tool_name="create_note",
            arguments=arguments,
            idempotency_key="k-last",
        )

        assert first.invocation.status is InvocationStatus.SUCCEEDED
        assert replay.replayed is True
        assert h.list_notes.run(h.principal).total == RATE_LIMIT_BOUND


class TestStepUpObligationIsEnforced:
    """NEX-058: ``step-up`` reads the *recorded* assurance, not a live claim.

    A refusal-only test would be satisfied by an adapter that always refused, so
    the permitted path is pinned too: a delegation granted by a human who proved
    multi-factor must pass. Assurance is fixed on the delegation at consent time
    (ADR-015), which is why the elevated principal is used when granting.
    """

    def test_a_multi_factor_delegation_satisfies_step_up(self, h: Harness) -> None:
        _publish(h, (_allow_tool("create_note", PolicyObligation.STEP_UP),))
        elevated = dataclasses.replace(h.principal, assurance=AuthenticationAssurance.MULTI_FACTOR)
        agent = h.register.run(
            elevated,
            display_name="Summarizer",
            runtime_identity="svc-summarizer",
            capabilities=[Capability.CREATE_NOTE.value],
        )
        delegation = h.delegate.run(
            elevated,
            agent_id=str(agent.id),
            capabilities=[Capability.CREATE_NOTE.value],
        )
        secret = h.issue.run(
            elevated,
            delegation_id=str(delegation.id),
            capabilities=[Capability.CREATE_NOTE.value],
            audience=TOOL_GATEWAY,
            ttl_seconds=300,
        ).secret

        result = h.gateway.run(
            secret=secret,
            tool_name="create_note",
            arguments={"title": "T", "content": "C"},
            idempotency_key="k-step-up-ok",
        )

        assert result.invocation.status is InvocationStatus.SUCCEEDED
        assert h.list_notes.run(h.principal).total == 1

    def test_a_single_factor_delegation_is_refused(self, h: Harness) -> None:
        # The default principal is single-factor, so the same tool that passes
        # above must be refused here: the difference is only the recorded
        # assurance, which is exactly the signal the control is meant to use.
        _publish(h, (_allow_tool("create_note", PolicyObligation.STEP_UP),))
        agent, secret = h.secret_for(Capability.CREATE_NOTE)

        with pytest.raises(AccessDenied, match="step-up"):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": "T", "content": "C"},
                idempotency_key="k-step-up-no",
            )

        assert h.list_notes.run(h.principal).total == 0
        recorded = h.invocations(agent)
        assert [r.status for r in recorded] == [InvocationStatus.DENIED]
        assert "single-factor" in (recorded[0].outcome or "")


class TestUnsatisfiableObligationsFailClosed:
    @pytest.mark.parametrize(
        "obligation",
        [
            PolicyObligation.APPROVAL,
            PolicyObligation.SECOND_APPROVER,
        ],
    )
    def test_a_human_gate_on_a_non_sensitive_tool_refuses_the_call(
        self, h: Harness, obligation: PolicyObligation
    ) -> None:
        # Both gate obligations are satisfiable, but only for a capability the
        # domain already treats as human-gated. ``create_note`` is not one, so a
        # policy naming either is unenforceable *for this tool*: whether a
        # capability needs a human is a static decision in code, and a policy
        # version may tighten an existing gate to two people but may never invent
        # a human gate for a tool the platform considers ungated. The refusal is
        # still the point: the dangerous outcome is not a refusal, it is running
        # the tool while quietly ignoring the obligation.
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

    def test_rate_limit_is_unsatisfiable_without_a_limiter(self, h: Harness) -> None:
        # ``rate-limit`` has an adapter (NEX-056), but only when one is wired. A
        # gateway built without a limiter must still refuse the call rather than
        # treat the obligation as met — otherwise removing a constructor argument
        # would silently disable a published policy's bound. This is the fail-closed
        # half of the wiring, and it is why the obligation's satisfiability is
        # conditioned on ``rate_limiter is not None`` rather than on the kind alone.
        _publish(h, (_allow_tool("create_note", PolicyObligation.RATE_LIMIT),))
        agent, secret = h.secret_for(Capability.CREATE_NOTE)

        from notes_service.application.use_cases.agents import ResolveAgentToken
        from notes_service.application.use_cases.authorization_policy import (
            DefaultAuthorizationProvider,
        )
        from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork

        auth = DefaultAuthorizationProvider()
        clock = SystemClock()

        def uow_factory() -> SqlAlchemyUnitOfWork:
            return SqlAlchemyUnitOfWork(h.session_factory)

        bare_gateway = InvokeTool(
            uow_factory=uow_factory,
            authorization=auth,
            clock=clock,
            resolve_token=ResolveAgentToken(
                uow_factory=uow_factory, authorization=auth, clock=clock
            ),
            policy_versions=h.policies,
        )

        with pytest.raises(AccessDenied, match="rate-limit"):
            bare_gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": "T", "content": "C"},
                idempotency_key="k-rate-limit-unwired",
            )

        assert h.list_notes.run(h.principal).total == 0
        recorded = h.invocations(agent)
        assert [r.status for r in recorded] == [InvocationStatus.DENIED]
        assert "rate-limit" in (recorded[0].outcome or "")

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
                    PolicyObligation.STEP_UP,
                ),
            ),
        )
        agent, secret = h.secret_for(Capability.DELETE_NOTE)
        note = h.create_note.run(h.principal, title="T", content="C")

        with pytest.raises(AccessDenied, match="step-up"):
            h.gateway.run(
                secret=secret,
                tool_name="delete_note",
                arguments={"note_id": str(note.id)},
                idempotency_key="k-mixed",
            )

        with h.uow() as uow:
            assert uow.approvals.list_pending_for_agent(agent.id) == []
        assert h.list_notes.run(h.principal).total == 1


class TestRedactionObligationIsEnforced:
    """NEX-054: the ``redaction`` obligation has a real adapter, not carry-through."""

    def test_a_redaction_obligation_filters_the_returned_note(self, h: Harness) -> None:
        # The subject is the output, not the permission: the call is *allowed* and
        # the handler runs, but the note body must not come back to the caller.
        # Before NEX-054 this obligation was refused as unsatisfiable, which was
        # honest but left the platform unable to express "allow, but withhold the
        # body" — the common case for an agent that may summarise but not exfiltrate.
        _publish(h, (_allow_tool("read_note", PolicyObligation.REDACTION),))
        _agent, secret = h.secret_for(Capability.READ_NOTE)
        note = h.create_note.run(h.principal, title="T", content="api_key=abc123")

        result = h.gateway.run(
            secret=secret,
            tool_name="read_note",
            arguments={"note_id": str(note.id)},
            idempotency_key="k-redaction",
        )

        returned = result.output["note"]
        assert isinstance(returned, dict)
        assert returned["content"] == REDACTED
        # The metadata an agent needs to work with the note is still there; only
        # the subject material is withheld.
        assert returned["id"] == str(note.id)
        assert returned["title"] == "T"

        # Redaction is a view for this caller, not a mutation of the record.
        with h.uow() as uow:
            stored = uow.notes.get(note.id)
        assert stored is not None
        assert str(stored.content) == "api_key=abc123"

    def test_the_audit_row_records_that_redaction_fired(self, h: Harness) -> None:
        # ``ok`` would be indistinguishable from an unredacted success, so an
        # operator reading the trail could not tell a policy had filtered the
        # response. The row must say so without recording the withheld field name.
        _publish(h, (_allow_tool("read_note", PolicyObligation.REDACTION),))
        agent, secret = h.secret_for(Capability.READ_NOTE)
        note = h.create_note.run(h.principal, title="T", content="C")

        h.gateway.run(
            secret=secret,
            tool_name="read_note",
            arguments={"note_id": str(note.id)},
            idempotency_key="k-redaction-audit",
        )

        recorded = h.invocations(agent)
        assert [r.status for r in recorded] == [InvocationStatus.SUCCEEDED]
        outcome = recorded[0].outcome or ""
        assert "redaction applied" in outcome
        assert "1 field(s) withheld" in outcome
        assert "content" not in outcome

    def test_without_the_obligation_the_body_is_returned(self, h: Harness) -> None:
        # The control: the same call, same caller, no obligation. If this also
        # redacted, the previous tests would pass for the wrong reason.
        _publish(h, (_allow_tool("read_note"),))
        agent, secret = h.secret_for(Capability.READ_NOTE)
        note = h.create_note.run(h.principal, title="T", content="C")

        result = h.gateway.run(
            secret=secret,
            tool_name="read_note",
            arguments={"note_id": str(note.id)},
            idempotency_key="k-no-redaction",
        )

        assert result.output["note"]["content"] == "C"  # type: ignore[index]
        assert (h.invocations(agent)[0].outcome or "") == "ok"

    def test_redaction_composes_with_approval(self, h: Harness) -> None:
        # Both obligations are satisfiable, so the call is not refused as tainted.
        # It must still go through the human gate: satisfying ``redaction`` must
        # not become a way to skip ``approval``.
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

        with pytest.raises(AccessDenied, match="requires human approval"):
            h.gateway.run(
                secret=secret,
                tool_name="delete_note",
                arguments={"note_id": str(note.id)},
                idempotency_key="k-redaction-approval",
            )

        with h.uow() as uow:
            assert len(uow.approvals.list_pending_for_agent(agent.id)) == 1
        assert h.list_notes.run(h.principal).total == 1


class TestEnhancedAuditObligationIsEnforced:
    """NEX-055: ``enhanced-audit`` is met by recording evidence, not by refusing.

    The obligation's mechanism *is* the record, so there is nothing to defer and
    nothing to filter. What can go wrong is subtler: the gateway could accept the
    obligation and record exactly the row it always records, which would make the
    policy read as enforced while the "more evidence" it demanded was never
    written.
    """

    def test_an_enhanced_audit_obligation_allows_the_call_and_records_evidence(
        self, h: Harness
    ) -> None:
        _publish(h, (_allow_tool("create_note", PolicyObligation.ENHANCED_AUDIT),))
        agent, secret = h.secret_for(Capability.CREATE_NOTE)

        result = h.gateway.run(
            secret=secret,
            tool_name="create_note",
            arguments={"title": "T", "content": "C"},
            idempotency_key="k-enhanced-audit",
        )

        # The call proceeds: the obligation is satisfiable, so it must not be
        # refused as an unmet requirement.
        assert result.output["note"]["title"] == "T"  # type: ignore[index]

        recorded = h.invocations(agent)
        assert [r.status for r in recorded] == [InvocationStatus.SUCCEEDED]
        evidence = recorded[0].decision_evidence
        assert evidence is not None, "the obligation was accepted but no evidence was written"

        # Each of the three fields answers a question the default trail cannot.
        # Rule ids are namespaced by the store (``gateway@<rule_id>``).
        assert evidence.matched_rules == ("gateway@allow-tool",)
        assert evidence.obligations == ("enhanced-audit",)
        attributes = dict(evidence.attributes)
        assert attributes["tool"] == "create_note"
        assert attributes["capability"] == Capability.CREATE_NOTE.value

    def test_without_the_obligation_no_evidence_is_recorded(self, h: Harness) -> None:
        # The control. If a plain allow also carried evidence, the previous test
        # would pass without the obligation having done anything.
        _publish(h, (_allow_tool("create_note"),))
        agent, secret = h.secret_for(Capability.CREATE_NOTE)

        h.gateway.run(
            secret=secret,
            tool_name="create_note",
            arguments={"title": "T", "content": "C"},
            idempotency_key="k-no-enhanced-audit",
        )

        recorded = h.invocations(agent)
        assert [r.status for r in recorded] == [InvocationStatus.SUCCEEDED]
        assert recorded[0].decision_evidence is None

    def test_the_evidence_is_sealed_into_the_hash_chain(self, h: Harness) -> None:
        # Evidence a reader cannot verify is a claim, not evidence. The chain must
        # still verify with the evidence present, and tampering with it must be
        # reported as a break rather than silently accepted.
        from notes_service.domain.agent.audit_chain import verify_chain
        from notes_service.domain.agent.value_objects import AgentId

        _publish(h, (_allow_tool("create_note", PolicyObligation.ENHANCED_AUDIT),))
        agent, secret = h.secret_for(Capability.CREATE_NOTE)

        h.gateway.run(
            secret=secret,
            tool_name="create_note",
            arguments={"title": "T", "content": "C"},
            idempotency_key="k-enhanced-audit-chain",
        )

        with h.uow() as uow:
            chain = uow.tool_invocations.list_chain(AgentId.parse(agent.id))
        verdict = verify_chain(chain)
        assert verdict.verified, verdict.reason

        # Strip the evidence from the stored row: the intent hash no longer
        # recomputes, so the alteration is detected at that entry.
        assert chain[0].decision_evidence is not None
        forged = dataclasses.replace(chain[0], decision_evidence=None)
        tampered = verify_chain([forged])
        assert tampered.verified is False
        assert tampered.first_break is not None

    def test_enhanced_audit_composes_with_redaction(self, h: Harness) -> None:
        # Both obligations are met by different mechanisms (a record and a filter).
        # Satisfying one must not skip the other.
        _publish(
            h,
            (
                _allow_tool(
                    "read_note",
                    PolicyObligation.ENHANCED_AUDIT,
                    PolicyObligation.REDACTION,
                ),
            ),
        )
        agent, secret = h.secret_for(Capability.READ_NOTE)
        note = h.create_note.run(h.principal, title="T", content="api_key=abc123")

        result = h.gateway.run(
            secret=secret,
            tool_name="read_note",
            arguments={"note_id": str(note.id)},
            idempotency_key="k-enhanced-redaction",
        )

        assert result.output["note"]["content"] == REDACTED  # type: ignore[index]
        recorded = h.invocations(agent)
        assert [r.status for r in recorded] == [InvocationStatus.SUCCEEDED]
        assert recorded[0].decision_evidence is not None
        assert "redaction applied" in (recorded[0].outcome or "")


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
