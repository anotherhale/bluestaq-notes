"""Focused tests for the governed tool gateway (``InvokeTool``).

The gateway is the single seam every agent action flows through, so its
guarantees are pinned here directly rather than inferred from the rest of the
suite. Three are load-bearing:

* **Every attempt is audited, including refusals.** A denial writes a ``denied``
  invocation row before raising, so "who tried what and was refused" survives.
* **Idempotency is real, not advisory.** A repeated key with the same arguments
  replays the recorded outcome instead of executing again; a repeated key with
  *different* arguments is refused rather than silently executed; an invocation
  left ``started`` by a crash is refused, because the gateway cannot know
  whether the side effect landed.
* **The audit trail never becomes a second copy of note content.** Only a
  SHA-256 digest of the arguments is stored, and it is stable under dict order.

These run against the real unit of work and the real note use cases, so they
also prove the gateway does not bypass per-resource authorization: a delegated
``read_note`` lets an agent *attempt* a read, not read a note its delegator
cannot see.

Application-level, not HTTP: the gateway has no router yet (tracked as
NEX-005-followup), and these assertions are about the use case, not the edge.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.errors import AccessDenied, Conflict, ResourceNotFound
from notes_service.application.use_cases.agents import (
    DecideApprovalRequest,
    GrantDelegation,
    IssueAgentToken,
    RegisterAgent,
    ResolveAgentToken,
    RevokeDelegation,
)
from notes_service.application.use_cases.authorization_policy import DefaultAuthorizationProvider
from notes_service.application.use_cases.notes import CreateNote, ListNotes
from notes_service.application.use_cases.tools import InvokeTool, ToolResult
from notes_service.domain.agent.tools import (
    InvocationStatus,
    ToolInvocation,
    arguments_digest,
    resolve_tool,
)
from notes_service.domain.agent.value_objects import Capability, TokenAudience
from notes_service.domain.errors import ValidationError
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email, UserId
from notes_service.infrastructure.db.models import ToolInvocationRow
from notes_service.infrastructure.db.seed import DEMO_USERS
from notes_service.infrastructure.db.uow import SqlAlchemyUnitOfWork
from notes_service.infrastructure.security.clock import SystemClock

pytestmark = pytest.mark.usefixtures("seeded")

ALICE = UserId(DEMO_USERS[0].id)
BOB = UserId(DEMO_USERS[1].id)
TOOL_GATEWAY = TokenAudience.TOOL_GATEWAY.value


def principal_for(user_id: UserId) -> Principal:
    return Principal(
        user_id=user_id,
        email=Email(f"{user_id}@example.com"),
        display_name="Caller",
        provider="demo",
        subject=str(user_id),
    )


@dataclass
class Harness:
    """Wires the gateway and the identity use cases it depends on, over one DB."""

    session_factory: sessionmaker[Session]
    principal: Principal
    gateway: InvokeTool
    register: RegisterAgent
    delegate: GrantDelegation
    issue: IssueAgentToken
    revoke_delegation: RevokeDelegation
    decide: DecideApprovalRequest
    create_note: CreateNote
    list_notes: ListNotes

    def uow(self) -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(self.session_factory)

    def agent(self, *capabilities: Capability, runtime_identity: str = "svc-summarizer"):
        return self.register.run(
            self.principal,
            display_name="Summarizer",
            runtime_identity=runtime_identity,
            capabilities=[c.value for c in capabilities],
        )

    def delegation(self, agent, *capabilities: Capability):
        return self.delegate.run(
            self.principal,
            agent_id=str(agent.id),
            capabilities=[c.value for c in capabilities],
        )

    def tool_token(
        self,
        delegation,
        *capabilities: Capability,
        audience: str = TOOL_GATEWAY,
        ttl_seconds: int = 300,
    ) -> str:
        return self.issue.run(
            self.principal,
            delegation_id=str(delegation.id),
            capabilities=[c.value for c in capabilities],
            audience=audience,
            ttl_seconds=ttl_seconds,
        ).secret

    def secret_for(self, *capabilities: Capability) -> tuple[object, str]:
        """An agent, a delegation, and a gateway token all holding ``capabilities``."""
        caps = capabilities or (Capability.READ_NOTE,)
        agent = self.agent(*caps)
        delegation = self.delegation(agent, *caps)
        return agent, self.tool_token(delegation, *caps)

    def invocations(self, agent) -> list[ToolInvocation]:
        with self.uow() as uow:
            return uow.tool_invocations.list_for_agent(agent.id)


@pytest.fixture
def h(session_factory: sessionmaker[Session]) -> Harness:
    auth = DefaultAuthorizationProvider()
    clock = SystemClock()

    def uow_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(session_factory)

    def make(cls, **extra):  # type: ignore[no-untyped-def]
        return cls(uow_factory=uow_factory, authorization=auth, clock=clock, **extra)

    resolve = make(ResolveAgentToken)
    return Harness(
        session_factory=session_factory,
        principal=principal_for(ALICE),
        gateway=make(InvokeTool, resolve_token=resolve),
        register=make(RegisterAgent),
        delegate=make(GrantDelegation),
        issue=make(IssueAgentToken),
        revoke_delegation=make(RevokeDelegation),
        decide=make(DecideApprovalRequest),
        create_note=make(CreateNote),
        list_notes=make(ListNotes),
    )


class TestDenyPathIsAudited:
    def test_missing_capability_is_denied_and_recorded(self, h: Harness) -> None:
        # The agent may read, but asks for a tool needing create_note.
        agent, secret = h.secret_for(Capability.READ_NOTE)

        with pytest.raises(AccessDenied, match="lacks capability"):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": "T", "content": "C"},
                idempotency_key="k-deny",
            )

        recorded = h.invocations(agent)
        assert len(recorded) == 1
        assert recorded[0].status is InvocationStatus.DENIED
        assert recorded[0].capability is Capability.CREATE_NOTE
        assert "lacks capability" in (recorded[0].outcome or "")

    def test_a_denial_never_leaks_note_content_into_the_audit_row(self, h: Harness) -> None:
        secret_marker = "TOPSECRET-CONTENT-8f3a1c"
        # Agent holds create_note but not read_note; read_note is the denied tool,
        # and the content is supplied as an argument that must not be persisted.
        agent, secret = h.secret_for(Capability.CREATE_NOTE)

        with pytest.raises(AccessDenied):
            h.gateway.run(
                secret=secret,
                tool_name="append_to_note",
                arguments={"note_id": "n1", "content": secret_marker},
                idempotency_key="k-leak",
            )

        with h.uow() as uow:
            row = uow.session.query(ToolInvocationRow).filter_by(agent_id=agent.id).one()
        stored_strings = " ".join(
            str(getattr(row, field))
            for field in (
                "tool_name",
                "capability",
                "idempotency_key",
                "arguments_digest",
                "resource_ref",
                "status",
                "outcome",
            )
        )
        assert secret_marker not in stored_strings
        assert len(row.arguments_digest) == 64  # a digest, not the content

    def test_an_unknown_tool_is_refused_before_any_audit_row(self, h: Harness) -> None:
        agent, secret = h.secret_for(Capability.READ_NOTE)

        with pytest.raises(ValidationError, match="unknown tool"):
            h.gateway.run(
                secret=secret,
                tool_name="rm_rf",
                arguments={},
                idempotency_key="k-unknown",
            )

        assert h.invocations(agent) == []

    def test_undeclared_arguments_are_rejected(self, h: Harness) -> None:
        agent, secret = h.secret_for(Capability.READ_NOTE)

        with pytest.raises(ValidationError, match="undeclared arguments"):
            h.gateway.run(
                secret=secret,
                tool_name="read_note",
                arguments={"note_id": "n1", "owner_id": str(BOB)},
                idempotency_key="k-undeclared",
            )

        assert h.invocations(agent) == []


class TestIdempotentReplay:
    def test_same_key_and_arguments_replays_without_re_executing(self, h: Harness) -> None:
        agent, secret = h.secret_for(Capability.CREATE_NOTE)
        args = {"title": "Once", "content": "body"}

        first = h.gateway.run(
            secret=secret, tool_name="create_note", arguments=args, idempotency_key="k1"
        )
        second = h.gateway.run(
            secret=secret, tool_name="create_note", arguments=args, idempotency_key="k1"
        )

        assert first.replayed is False
        assert second.replayed is True
        # Replay returns the recorded invocation, not a second execution.
        assert second.invocation.id == first.invocation.id
        assert second.output == {}
        assert len(h.invocations(agent)) == 1

        page = h.list_notes.run(h.principal)
        assert page.total == 1  # exactly one note was ever created

    def test_same_key_with_different_arguments_is_refused(self, h: Harness) -> None:
        agent, secret = h.secret_for(Capability.CREATE_NOTE)
        h.gateway.run(
            secret=secret,
            tool_name="create_note",
            arguments={"title": "A", "content": "x"},
            idempotency_key="k1",
        )

        with pytest.raises(Conflict, match="different tool invocation"):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": "B", "content": "y"},
                idempotency_key="k1",
            )

        assert len(h.invocations(agent)) == 1

    def test_a_started_invocation_is_not_retried(self, h: Harness) -> None:
        agent, secret = h.secret_for(Capability.CREATE_NOTE)
        tool = resolve_tool("create_note")
        args = {"title": "Crashy", "content": "body"}

        # Simulate a handler that crashed: a STARTED row was committed, then the
        # process died before it could be finalised.
        orphan = ToolInvocation.begin(
            agent_id=agent.id,
            token_id=None,
            tool=tool,
            idempotency_key="k-start",
            arguments=args,
            resource_ref="tool:create_note",
        )
        with h.uow() as uow:
            uow.tool_invocations.add(orphan)
            uow.commit()

        with pytest.raises(Conflict, match="did not finish"):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments=args,
                idempotency_key="k-start",
            )

        assert len(h.invocations(agent)) == 1  # no second row, no re-execution

    def test_a_denied_invocation_is_not_replayed_as_an_execution(self, h: Harness) -> None:
        _agent, secret = h.secret_for(Capability.READ_NOTE)

        with pytest.raises(AccessDenied):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": "T", "content": "C"},
                idempotency_key="k-deny",
            )
        # Retrying the same key must stay denied, not become an execution.
        with pytest.raises(AccessDenied, match="previously ended as denied"):
            h.gateway.run(
                secret=secret,
                tool_name="create_note",
                arguments={"title": "T", "content": "C"},
                idempotency_key="k-deny",
            )


class TestDigestStability:
    def test_digest_is_independent_of_argument_order(self) -> None:
        tool = resolve_tool("create_note")

        a = arguments_digest(tool, {"title": "T", "content": "C"})
        b = arguments_digest(tool, {"content": "C", "title": "T"})

        assert a == b
        assert len(a) == 64

    def test_digest_changes_when_an_argument_changes(self) -> None:
        tool = resolve_tool("create_note")

        assert arguments_digest(tool, {"title": "T", "content": "C"}) != arguments_digest(
            tool, {"title": "T", "content": "D"}
        )

    def test_digest_is_bound_to_the_tool_name(self) -> None:
        create = resolve_tool("create_note")
        append = resolve_tool("append_to_note")
        args = {"title": "T", "content": "C"}

        assert arguments_digest(create, args) != arguments_digest(append, args)


class TestApprovalGating:
    def test_sensitive_tool_opens_an_approval_instead_of_executing(self, h: Harness) -> None:
        agent, secret = h.secret_for(Capability.DELETE_NOTE)
        note = h.create_note.run(h.principal, title="Doomed", content="body")

        with pytest.raises(AccessDenied, match="requires human approval"):
            h.gateway.run(
                secret=secret,
                tool_name="delete_note",
                arguments={"note_id": str(note.id)},
                idempotency_key="k-del-1",
            )

        recorded = h.invocations(agent)
        assert len(recorded) == 1
        assert recorded[0].status is InvocationStatus.DENIED
        assert recorded[0].approval_request_id is not None

        with h.uow() as uow:
            requests = uow.approvals.list_pending_for_agent(agent.id)
        assert len(requests) == 1
        assert requests[0].resource_ref == f"note:{note.id}"

        # The note still exists: nothing executed.
        assert h.list_notes.run(h.principal).total == 1

    def test_an_approved_request_allows_the_action_to_run(self, h: Harness) -> None:
        agent, secret = h.secret_for(Capability.DELETE_NOTE)
        note = h.create_note.run(h.principal, title="Doomed", content="body")

        with pytest.raises(AccessDenied):
            h.gateway.run(
                secret=secret,
                tool_name="delete_note",
                arguments={"note_id": str(note.id)},
                idempotency_key="k-del-1",
            )
        with h.uow() as uow:
            request = uow.approvals.list_pending_for_agent(agent.id)[0]
        h.decide.run(h.principal, approval_id=str(request.id), approve=True)

        result = h.gateway.run(
            secret=secret,
            tool_name="delete_note",
            arguments={"note_id": str(note.id)},
            idempotency_key="k-del-2",
        )

        assert isinstance(result, ToolResult)
        assert result.output == {"deleted": True}
        assert h.list_notes.run(h.principal).total == 0

    def test_an_approval_is_spent_by_the_action_it_authorises(self, h: Harness) -> None:
        """One human decision authorises one action, not a standing permission.

        Before this was enforced, the gateway matched an ``approved`` row and
        never spent it, so a single approval to delete a note silently
        authorised every later delete of that note. The second attempt must be
        refused, and it must not execute.
        """
        agent, secret = h.secret_for(Capability.DELETE_NOTE)
        first = h.create_note.run(h.principal, title="Doomed", content="body")
        h.create_note.run(h.principal, title="Spared", content="body")

        with pytest.raises(AccessDenied):
            h.gateway.run(
                secret=secret,
                tool_name="delete_note",
                arguments={"note_id": str(first.id)},
                idempotency_key="k-del-1",
            )
        with h.uow() as uow:
            request = uow.approvals.list_pending_for_agent(agent.id)[0]
        h.decide.run(h.principal, approval_id=str(request.id), approve=True)

        # The approval covers exactly this resource and is spent by this call.
        h.gateway.run(
            secret=secret,
            tool_name="delete_note",
            arguments={"note_id": str(first.id)},
            idempotency_key="k-del-2",
        )
        assert h.list_notes.run(h.principal).total == 1

        # A second attempt against the same resource finds no usable approval.
        with pytest.raises(AccessDenied, match="requires human approval"):
            h.gateway.run(
                secret=secret,
                tool_name="delete_note",
                arguments={"note_id": str(first.id)},
                idempotency_key="k-del-3",
            )

        with h.uow() as uow:
            spent = uow.approvals.list_for_agent(agent.id)[0]
        assert spent.consumed_at is not None

    def test_an_expired_approval_does_not_authorise_execution(self, h: Harness) -> None:
        """A lapsed approval must fail closed, not execute on a stale decision."""
        agent, secret = h.secret_for(Capability.DELETE_NOTE)
        note = h.create_note.run(h.principal, title="Doomed", content="body")

        with pytest.raises(AccessDenied):
            h.gateway.run(
                secret=secret,
                tool_name="delete_note",
                arguments={"note_id": str(note.id)},
                idempotency_key="k-del-1",
            )
        with h.uow() as uow:
            request = uow.approvals.list_pending_for_agent(agent.id)[0]
            # Approve, then push the validity window into the past as the clock
            # would after 72 hours. This is the state the gateway must refuse.
            request.approve(approver_user_id=BOB, now=request.requested_at)
            request.expires_at = request.decided_at - timedelta(seconds=1)
            uow.approvals.save(request)
            uow.commit()

        with pytest.raises(AccessDenied, match="requires human approval"):
            h.gateway.run(
                secret=secret,
                tool_name="delete_note",
                arguments={"note_id": str(note.id)},
                idempotency_key="k-del-2",
            )

        # Fail closed: the note survives.
        assert h.list_notes.run(h.principal).total == 1


class TestPerResourceAuthorizationIsNotBypassed:
    def test_an_agent_cannot_read_a_note_its_delegator_cannot_see(self, h: Harness) -> None:
        # BOB owns a private note; ALICE's agent holds read_note.
        bob_principal = principal_for(BOB)
        bobs_note = h.create_note.run(bob_principal, title="Bob's", content="private")
        agent, secret = h.secret_for(Capability.READ_NOTE)

        with pytest.raises(ResourceNotFound):
            h.gateway.run(
                secret=secret,
                tool_name="read_note",
                arguments={"note_id": str(bobs_note.id)},
                idempotency_key="k-read",
            )

        # The attempt is recorded as failed, not silently dropped.
        recorded = h.invocations(agent)
        assert len(recorded) == 1
        assert recorded[0].status is InvocationStatus.FAILED

    def test_an_agent_reads_a_note_its_delegator_owns(self, h: Harness) -> None:
        note = h.create_note.run(h.principal, title="Mine", content="hello")
        agent, secret = h.secret_for(Capability.READ_NOTE)

        result = h.gateway.run(
            secret=secret,
            tool_name="read_note",
            arguments={"note_id": str(note.id)},
            idempotency_key="k-read-ok",
        )

        assert result.output["note"]["content"] == "hello"
        assert h.invocations(agent)[0].status is InvocationStatus.SUCCEEDED


class TestTokenIsReResolvedEveryCall:
    def test_a_wrong_audience_token_is_refused(self, h: Harness) -> None:
        agent = h.agent(Capability.READ_NOTE)
        delegation = h.delegation(agent, Capability.READ_NOTE)
        notes_api_secret = h.tool_token(
            delegation, Capability.READ_NOTE, audience=TokenAudience.NOTES_API.value
        )

        with pytest.raises(AccessDenied, match="audience"):
            h.gateway.run(
                secret=notes_api_secret,
                tool_name="list_shared_notes",
                arguments={},
                idempotency_key="k-aud",
            )

    def test_revoking_the_delegation_kills_the_token_immediately(self, h: Harness) -> None:
        agent, secret = h.secret_for(Capability.READ_NOTE)
        with h.uow() as uow:
            delegation = uow.delegations.list_for_agent(agent.id)[0]
        h.revoke_delegation.run(h.principal, delegation_id=str(delegation.id))

        with pytest.raises(AccessDenied):
            h.gateway.run(
                secret=secret,
                tool_name="list_shared_notes",
                arguments={},
                idempotency_key="k-revoked",
            )

        # Resolution failed before any actor was established, so there is no
        # invocation to attribute — an unattributable row would be a lie.
        assert h.invocations(agent) == []


class TestInvocationLifecycleIsTerminal:
    def test_a_finished_invocation_cannot_be_finished_twice(self) -> None:
        tool = resolve_tool("list_shared_notes")
        invocation = ToolInvocation.begin(
            agent_id=__import__("uuid").uuid4(),
            token_id=None,
            tool=tool,
            idempotency_key="k",
            arguments={},
            resource_ref="tool:list_shared_notes",
        )
        invocation.succeed(outcome="ok")

        with pytest.raises(ValidationError, match="already"):
            invocation.fail(outcome="again")
