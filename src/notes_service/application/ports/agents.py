"""Persistence ports for the agent bounded context.

Agents, their delegations, issued tokens, and approval requests are persisted
through these ports. None of them commits: the Unit of Work owns transaction
boundaries, exactly as for every other repository port.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from notes_service.domain.agent.audit_chain import ChainAnchor
from notes_service.domain.agent.delegation import DelegatedAuthorization
from notes_service.domain.agent.entities import AgentIdentity, ApprovalRequest
from notes_service.domain.agent.runs import AgentRun, AgentRunId
from notes_service.domain.agent.token import AgentToken
from notes_service.domain.agent.tools import ToolInvocation, ToolInvocationId
from notes_service.domain.agent.value_objects import (
    AgentId,
    ApprovalRequestId,
    ApprovalStatus,
    DelegatedAuthorizationId,
)
from notes_service.domain.identity.value_objects import UserId


@runtime_checkable
class AgentRepository(Protocol):
    """Persistence port for agent principals."""

    def add(self, agent: AgentIdentity) -> None: ...

    def get(self, agent_id: AgentId) -> AgentIdentity | None: ...

    def get_by_runtime_identity(self, runtime_identity: str) -> AgentIdentity | None: ...

    def list_for_owner(self, owner_user_id: UserId) -> list[AgentIdentity]:
        """Agents owned by one user.

        There is deliberately no unscoped ``list_all``: a caller must always
        name the owner, so one user cannot enumerate another's agents.
        """
        ...

    def list_all_for_integrity_sweep(self, *, limit: int, offset: int = 0) -> list[AgentIdentity]:
        """A page of the deployment's agents, oldest first, capped at ``limit``.

        The one unscoped enumeration, and it exists for exactly one caller: the
        operator-gated platform-wide audit sweep. ``list_for_owner`` refuses to
        be unscoped for a good reason — a user must not enumerate another user's
        agents — and that reason still holds here, which is why this method
        lives behind an operator allowlist at the use-case layer rather than
        being reachable from any user-facing path.

        Capping is part of the contract, not a caller's courtesy: an unbounded
        scan of a large deployment is a denial-of-service on the database. The
        caller compares the rows returned against :meth:`count_all_for_integrity_sweep`
        to report ``truncated``, so a partial sweep cannot be mistaken for a
        complete one.

        ``offset`` is what lets a capped sweep *finish*. A limit with no way to
        advance past it does not bound one request — it permanently excludes
        every agent after the first page, and because the order is oldest-first
        that is exactly the newest trails, which are the most likely to have
        been tampered with. The order must stay deterministic across pages
        (``created_at``, then ``id``) or paging could skip a row and still look
        complete; see the adapter, where both are in the ``ORDER BY``.
        """
        ...

    def count_all_for_integrity_sweep(self) -> int:
        """How many agents exist in the deployment, for the sweep's coverage claim.

        Exists so the sweep can say "verified 40 of 300" rather than "verified
        40". A count is one indexed aggregate; the alternative — inferring
        truncation from ``len(rows) == limit`` — cannot tell a full page from a
        prefix of more.
        """
        ...

    def save(self, agent: AgentIdentity) -> None: ...


@runtime_checkable
class DelegationRepository(Protocol):
    """Persistence port for delegated authorizations."""

    def add(self, delegation: DelegatedAuthorization) -> None: ...

    def get(self, delegation_id: DelegatedAuthorizationId) -> DelegatedAuthorization | None: ...

    def list_for_agent(self, agent_id: AgentId) -> list[DelegatedAuthorization]: ...

    def list_active_for_delegator(self, user_id: UserId) -> list[DelegatedAuthorization]: ...

    def save(self, delegation: DelegatedAuthorization) -> None: ...

    def revoke_all_for_agent(self, agent_id: AgentId, *, now: datetime) -> int:
        """Revoke every active delegation for an agent; returns the count.

        Used when an agent is revoked so derived tokens lose their authority
        immediately rather than at token expiry.
        """
        ...


@runtime_checkable
class AgentTokenRepository(Protocol):
    """Persistence port for issued agent tokens.

    Lookups are by token *hash* only: the raw secret never reaches the
    repository, so it cannot be logged or persisted by accident.
    """

    def add(self, token: AgentToken) -> None: ...

    def get_by_token_hash(self, token_hash: str) -> AgentToken | None: ...

    def save(self, token: AgentToken) -> None: ...

    def revoke_all_for_delegation(
        self, delegation_id: DelegatedAuthorizationId, *, now: datetime
    ) -> int: ...

    def revoke_all_for_agent(self, agent_id: AgentId, *, now: datetime) -> int: ...


@runtime_checkable
class ApprovalRequestRepository(Protocol):
    """Persistence port for human approvals of sensitive agent actions."""

    def add(self, request: ApprovalRequest) -> None: ...

    def get(self, approval_id: ApprovalRequestId) -> ApprovalRequest | None: ...

    def list_pending_for_agent(self, agent_id: AgentId) -> list[ApprovalRequest]: ...

    def list_for_agent(
        self,
        agent_id: AgentId,
        *,
        status: ApprovalStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ApprovalRequest]:
        """Every approval request for an agent, newest state included.

        The tool gateway needs *approved* requests, not only pending ones, so
        this is broader than :meth:`list_pending_for_agent`.

        Bounded reads and server-side status selection (NEX-089)
        -------------------------------------------------------

        This list grows without bound: an agent's approval history is never
        pruned, and every gateway invocation appends nothing but every *opening*
        of a sensitive action does. The original signature returned the whole
        history, and the reporting use case then filtered it in Python — so one
        ``GET`` loaded every approval an agent had ever opened, in order to
        return the ones with a particular status. That is the same unbounded
        read NEX-086 fixed for the revocation history.

        ``status`` selects in the store, not in the caller. ``ApprovalStatus``
        is a stored column (:class:`ApprovalRequestRow.status`), never derived,
        so filtering on it in SQL cannot disagree with filtering the same value
        in Python — the consistency worry that keeps ``revoked_grants`` in Python
        does not apply here.

        ``limit=None`` keeps the whole-history behaviour that the tool gateway
        depends on: it needs every live approval to decide whether an action is
        authorised, so bounding it would break enforcement, not just reporting.
        A serving path always passes a limit.
        """
        ...

    def count_for_agent(self, agent_id: AgentId, *, status: ApprovalStatus | None = None) -> int:
        """The exact number of an agent's approval requests, without loading them.

        A paged report must state the size of the *set*, not of the page it
        happened to return. Without this, ``meta.total`` came from ``len(items)``,
        which for a bounded page equals the page size — so "this agent has 3
        denied approvals" was indistinguishable from "the first 3 of many", and
        an auditor reading a window could not tell a complete answer from a
        truncated one. That is the reassuring-direction error NEX-086 removed from
        the revocation history.

        ``status`` must match the filter the page used, or the total bounds a
        different set than the page and ``offset + len(page) < total`` decides
        "there is another page" from two inconsistent numbers.
        """
        ...

    def save(self, request: ApprovalRequest) -> None: ...

    def consume(self, approval_id: ApprovalRequestId, *, now: datetime) -> bool:
        """Atomically spend an approval; ``False`` if it was not spendable.

        A read-then-write in the use case would let two concurrent invocations
        both observe an unused approval and both spend it. The check and the
        stamp therefore happen in one conditional UPDATE, and the caller treats
        a ``False`` result as "no usable approval".
        """

    def revoke_granted_for_agent(self, agent_id: AgentId, *, now: datetime, reason: str) -> int:
        """Withdraw every granted-but-unspent approval for an agent.

        Called when the authority that justified those approvals is withdrawn —
        a delegation revoked, or the agent itself. An approval is live authority
        until it is spent, and it records no delegation of its own, so it cannot
        be attributed to a surviving delegation once any of the agent's
        authority is taken away. Fail closed: the human must approve again.

        Only ``approved`` rows that have not been consumed are touched. A spent
        approval already authorised its act and is history, not authority; a
        pending one was never a grant and is denied or left to expire instead.
        """
        ...


@runtime_checkable
class ToolInvocationRepository(Protocol):
    """Persistence port for the tool-invocation audit trail.

    A record is written before the handler runs and finalised afterwards, so
    this repository must support an insert followed by an in-place update within
    one transaction. Lookups are scoped by agent: an invocation is never
    readable across agents.
    """

    def add(self, invocation: ToolInvocation) -> None: ...

    def save(self, invocation: ToolInvocation) -> None: ...

    def get(self, invocation_id: ToolInvocationId) -> ToolInvocation | None: ...

    def find_by_idempotency_key(
        self, agent_id: AgentId, idempotency_key: str
    ) -> ToolInvocation | None:
        """The invocation recorded for this agent under this key, if any.

        Scoped to the agent, not global: one agent reusing a key must not be
        able to observe or block another agent's invocation.
        """
        ...

    def list_for_agent(
        self, agent_id: AgentId, *, limit: int = 50, offset: int = 0
    ) -> list[ToolInvocation]: ...

    def count_for_agent(self, agent_id: AgentId) -> int:
        """How many entries this agent's trail holds, for the page's metadata.

        Exists so a paginated listing can say "showing 50 of 3,412" instead of
        "showing 50". Deriving the total from ``len(rows)`` cannot work: a page
        is capped, so the count would equal the page size on every page and a
        reader could not tell a complete trail from a window over a larger one.
        One indexed aggregate answers the question the page cannot.

        Scoped by agent for the same reason as :meth:`list_for_agent`: a count
        is a fact about another user's trail too, so it is never global.
        """
        ...

    def next_chain_link(self, agent_id: AgentId) -> tuple[int, str]:
        """The position and predecessor hash for this agent's next entry.

        Returns ``(1, GENESIS_HASH)`` for an agent with no entries. Read inside
        the same transaction that inserts the entry, so two concurrent attempts
        cannot both claim one sequence number: the unique constraint on
        ``(agent_id, chain_sequence)`` rejects the loser and the caller retries.

        The link is derived from the agent's *anchor*, not from the greatest
        surviving row. If the newest rows were deleted, a row-derived link would
        quietly re-anchor the chain on the truncated tail and the truncation
        would never be noticed. An anchor-derived link points at a hash whose
        entry no longer exists, so the tampering stays visible.
        """
        ...

    def advance_chain_anchor(self, agent_id: AgentId, invocation: ToolInvocation) -> None:
        """Move the agent's anchor to ``invocation``, in the caller's transaction.

        Called after the entry is inserted, so the anchor and the entry commit
        together: an anchor that moved without its entry would report a
        phantom deletion, and an entry inserted without moving the anchor would
        let the next truncation go unnoticed.
        """
        ...

    def chain_anchor(self, agent_id: AgentId) -> ChainAnchor:
        """The agent's recorded high-water mark, for verification."""
        ...

    def list_chain(self, agent_id: AgentId) -> list[ToolInvocation]:
        """The agent's whole trail in ``chain_sequence`` order, for verification."""
        ...


@runtime_checkable
class AgentRunRepository(Protocol):
    """Persistence port for durable agent runs.

    The aggregate is loaded and saved whole: a run's steps are part of its
    identity, and a partial save could record a step as started while losing an
    earlier step's completion. Lookups are scoped by agent, so one agent cannot
    read another's run.
    """

    def add(self, run: AgentRun) -> None: ...

    def get(self, run_id: AgentRunId) -> AgentRun | None: ...

    def list_for_agent(
        self, agent_id: AgentId, *, limit: int = 50, offset: int = 0
    ) -> list[AgentRun]: ...

    def count_for_agent(self, agent_id: AgentId) -> int:
        """How many runs this agent has, for the page's metadata.

        Exists so a paginated listing can say "showing 50 of 1,208" instead of
        "showing 50". Deriving the total from ``len(rows)`` cannot work: the
        listing is capped, so a page-derived total equals the page size on every
        page and a reader cannot tell a complete history from a window over a
        larger one.

        Scoped by agent for the same reason as :meth:`list_for_agent`: a count is
        a fact about another agent's activity too, so it is never global.
        """
        ...

    def list_resumable_for_agent(self, agent_id: AgentId) -> list[AgentRun]:
        """Runs for this agent that stopped without reaching a terminal state."""
        ...

    def save(self, run: AgentRun) -> None: ...


__all__ = [
    "AgentRepository",
    "AgentRunRepository",
    "AgentTokenRepository",
    "ApprovalRequestRepository",
    "DelegationRepository",
    "ToolInvocationRepository",
]
