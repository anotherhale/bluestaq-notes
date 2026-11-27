"""Published-policy enforcement at the tool gateway (NEX-051).

The policy lifecycle (NEX-047..NEX-050) could draft, publish and retire versions,
but nothing consulted them: ``evaluate`` had no production caller, so a published
policy was documentation rather than a control. These tests pin the seam that
changed that, and — more importantly — the two ways it could go wrong:

* **An empty policy set must not deny.** ``evaluate(())`` returns ``DENY`` (the
  correct default for a bare evaluator). Wiring that straight into the gateway
  would refuse *every* tool call in a deployment with no published policy. The
  gateway denies only on a *matched* deny rule, and this is the regression test
  for that distinction.
* **A matched deny rule must deny, and be audited.** A policy refusal has to be
  a real gate before any handler runs, and it must leave the same ``denied``
  invocation row a capability refusal does.

These run against the real gateway, real unit of work and real note use cases;
only the policy store is the in-memory adapter, because the point is the
enforcement path, not the SQL adapter (which NEX-048 already covers).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.errors import AccessDenied
from notes_service.application.use_cases.agents import (
    GrantDelegation,
    IssueAgentToken,
    RegisterAgent,
    ResolveAgentToken,
)
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.application.use_cases.notes import CreateNote, ListNotes
from notes_service.application.use_cases.tools import InvokeTool
from notes_service.domain.agent.tools import InvocationStatus, ToolInvocation
from notes_service.domain.agent.value_objects import Capability, TokenAudience
from notes_service.domain.governance.policy import (
    AttributeRef,
    AttributeType,
    Comparison,
    PolicyEffect,
    PolicyRule,
    new_policy_version,
)
from notes_service.domain.identity.value_objects import UserId
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.governance.policy_versions import (
    InMemoryPolicyVersionRepository,
)
from notes_service.infrastructure.security.clock import SystemClock
from tests.agent.test_tool_gateway import principal_for

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)
TOOL_GATEWAY = TokenAudience.TOOL_GATEWAY.value


@dataclass
class Harness:
    session_factory: sessionmaker[Session]
    gateway: InvokeTool
    policies: InMemoryPolicyVersionRepository
    register: RegisterAgent
    delegate: GrantDelegation
    issue: IssueAgentToken
    create_note: CreateNote
    list_notes: ListNotes

    @property
    def principal(self):  # type: ignore[no-untyped-def]
        return principal_for(ALICE)

    def uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.session_factory)

    def secret_for(self, *capabilities: Capability) -> tuple[object, str]:
        """An agent, a delegation, and a gateway token all holding ``capabilities``."""
        caps = capabilities or (Capability.READ_NOTE,)
        agent = self.register.run(
            self.principal,
            display_name="Summarizer",
            runtime_identity="svc-summarizer",
            capabilities=[c.value for c in caps],
        )
        delegation = self.delegate.run(
            self.principal,
            agent_id=str(agent.id),
            capabilities=[c.value for c in caps],
        )
        secret = self.issue.run(
            self.principal,
            delegation_id=str(delegation.id),
            capabilities=[c.value for c in caps],
            audience=TOOL_GATEWAY,
            ttl_seconds=300,
        ).secret
        return agent, secret

    def invocations(self, agent) -> list[ToolInvocation]:  # type: ignore[no-untyped-def]
        with self.uow() as uow:
            return uow.tool_invocations.list_for_agent(agent.id)


@pytest.fixture
def h(session_factory: sessionmaker[Session]) -> Harness:
    auth = DefaultAuthorizationProvider()
    clock = SystemClock()
    policies = InMemoryPolicyVersionRepository()

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    def make(cls, **extra):  # type: ignore[no-untyped-def]
        return cls(uow_factory=uow_factory, authorization=auth, clock=clock, **extra)

    resolve = make(ResolveAgentToken)
    return Harness(
        session_factory=session_factory,
        gateway=make(InvokeTool, resolve_token=resolve, policy_versions=policies),
        policies=policies,
        register=make(RegisterAgent),
        delegate=make(GrantDelegation),
        issue=make(IssueAgentToken),
        create_note=make(CreateNote),
        list_notes=make(ListNotes),
    )


def _publish(h: Harness, rules: tuple[PolicyRule, ...]) -> None:
    """Store a draft and publish it, through the store's own transitions."""
    version = new_policy_version(
        policy_id="gateway",
        version=h.policies.next_version_number("gateway"),
        rules=rules,
        rationale="test policy",
        drafted_by="drafter",
    )
    h.policies.add_draft(version)
    h.policies.publish(version.publish(now=SystemClock().now(), published_by="operator"))


def _deny_tool(tool_name: str) -> PolicyRule:
    return PolicyRule(
        rule_id="deny-tool",
        effect=PolicyEffect.DENY,
        when=Comparison(
            attribute=AttributeRef(name="tool", type=AttributeType.STRING),
            operator="==",
            value=tool_name,
        ),
    )


class TestEmptyPolicySetDoesNotDeny:
    def test_a_tool_call_succeeds_with_no_published_policy(self, h: Harness) -> None:
        # The trap: evaluate(()) is DENY. A deployment with no published policy
        # must still be able to use the gateway, so the empty set cannot be read
        # as a denial. Without the matched-rules guard this test fails.
        agent, secret = h.secret_for(Capability.CREATE_NOTE)

        result = h.gateway.run(
            secret=secret,
            tool_name="create_note",
            arguments={"title": "T", "content": "C"},
            idempotency_key="k-empty",
        )

        assert result.invocation.status is InvocationStatus.SUCCEEDED
        assert [r.status for r in h.invocations(agent)] == [InvocationStatus.SUCCEEDED]


class TestMatchedDenyRuleIsEnforced:
    def test_a_matched_deny_rule_refuses_the_call(self, h: Harness) -> None:
        _publish(h, (_deny_tool("create_note"),))
        agent, secret = h.secret_for(Capability.CREATE_NOTE)

        with pytest.raises(AccessDenied, match="refused by published policy"):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": "T", "content": "C"},
                idempotency_key="k-deny",
            )

        recorded = h.invocations(agent)
        assert len(recorded) == 1
        assert recorded[0].status is InvocationStatus.DENIED
        assert "deny-tool" in (recorded[0].outcome or "")

    def test_a_deny_rule_for_another_tool_does_not_block_this_one(self, h: Harness) -> None:
        # A published deny rule that matches nothing leaves the call alone:
        # policies subtract authority, they are not a blanket refusal.
        _publish(h, (_deny_tool("delete_note"),))
        _agent, secret = h.secret_for(Capability.CREATE_NOTE)

        result = h.gateway.run(
            secret=secret,
            tool_name="create_note",
            arguments={"title": "T", "content": "C"},
            idempotency_key="k-other",
        )

        assert result.invocation.status is InvocationStatus.SUCCEEDED

    def test_a_policy_allow_cannot_grant_a_missing_capability(self, h: Harness) -> None:
        # "Policies only subtract": an ALLOW rule cannot bypass the capability
        # check, which runs first. The refusal must still name the capability.
        _publish(
            h,
            (PolicyRule(rule_id="allow-all", effect=PolicyEffect.ALLOW, when=None),),
        )
        _agent, secret = h.secret_for(Capability.CREATE_NOTE)

        with pytest.raises(AccessDenied, match="lacks capability"):
            h.gateway.run(
                secret=secret,
                tool_name="delete_note",
                arguments={"note_id": "n1"},
                idempotency_key="k-allow",
            )

    def test_a_policy_denial_is_not_replayed_as_an_execution(self, h: Harness) -> None:
        # Retrying a policy-denied call replays the denial: it must not slip
        # through on the second attempt, nor collide with the idempotency key.
        _publish(h, (_deny_tool("create_note"),))
        agent, secret = h.secret_for(Capability.CREATE_NOTE)

        for _ in range(2):
            with pytest.raises(AccessDenied):
                h.gateway.run(
                    secret=secret,
                    tool_name="create_note",
                    arguments={"title": "T", "content": "C"},
                    idempotency_key="k-replay",
                )

        recorded = h.invocations(agent)
        assert len(recorded) == 1
        assert recorded[0].status is InvocationStatus.DENIED
