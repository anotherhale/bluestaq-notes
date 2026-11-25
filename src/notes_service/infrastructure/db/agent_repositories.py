"""SQLAlchemy implementations of the agent persistence ports.

Repositories map between ORM rows and domain aggregates and never commit; the
:class:`~notes_service.infrastructure.db.uow.SqlAlchemyUnitOfWork` owns
transaction boundaries.

Capability sets are persisted as comma-separated strings. The vocabulary is
small and fixed, and the domain validates on the way out, so this keeps one DDL
that works unchanged on SQLite and PostgreSQL without a JSON column.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from notes_service.domain.agent.audit_chain import GENESIS_HASH
from notes_service.domain.agent.delegation import DelegatedAuthorization
from notes_service.domain.agent.entities import AgentIdentity, ApprovalRequest
from notes_service.domain.agent.runs import (
    AgentRun,
    AgentRunId,
    RunStatus,
    RunStep,
    RunStepId,
    StepStatus,
)
from notes_service.domain.agent.token import AgentToken
from notes_service.domain.agent.tools import InvocationStatus, ToolInvocation, ToolInvocationId
from notes_service.domain.agent.value_objects import (
    AgentId,
    AgentStatus,
    AgentTokenId,
    ApprovalRequestId,
    ApprovalStatus,
    Capability,
    DelegatedAuthorizationId,
    DelegationStatus,
    RuntimeIdentity,
    TokenAudience,
)
from notes_service.domain.errors import VersionConflict
from notes_service.domain.identity.value_objects import UserId
from notes_service.infrastructure.db.models import (
    AgentRow,
    AgentRunRow,
    AgentRunStepRow,
    AgentTokenRow,
    ApprovalRequestRow,
    DelegationRow,
    ToolInvocationRow,
)


def _as_uuid(value: object) -> uuid.UUID:
    return uuid.UUID(str(value))


def _encode_capabilities(capabilities: frozenset[Capability]) -> str:
    return ",".join(sorted(c.value for c in capabilities))


def _decode_capabilities(raw: str) -> frozenset[Capability]:
    return frozenset(Capability(part) for part in raw.split(",") if part)


def _agent_to_domain(row: AgentRow) -> AgentIdentity:
    return AgentIdentity(
        id=AgentId.parse(row.id),
        display_name=row.display_name,
        owner_user_id=UserId.parse(row.owner_user_id),
        runtime_identity=RuntimeIdentity(row.runtime_identity),
        capabilities=_decode_capabilities(row.capabilities),
        status=AgentStatus(row.status),
        max_token_ttl_seconds=row.max_token_ttl_seconds,
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        revoked_at=row.revoked_at,
    )


def _delegation_to_domain(row: DelegationRow) -> DelegatedAuthorization:
    return DelegatedAuthorization(
        id=DelegatedAuthorizationId.parse(row.id),
        agent_id=AgentId.parse(row.agent_id),
        delegator_user_id=UserId.parse(row.delegator_user_id),
        capabilities=_decode_capabilities(row.capabilities),
        status=DelegationStatus(row.status),
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


def _token_to_domain(row: AgentTokenRow) -> AgentToken:
    return AgentToken(
        id=AgentTokenId.parse(row.id),
        agent_id=AgentId.parse(row.agent_id),
        delegation_id=DelegatedAuthorizationId.parse(row.delegation_id),
        audience=TokenAudience(row.audience),
        capabilities=_decode_capabilities(row.capabilities),
        token_hash=row.token_hash,
        issued_at=row.issued_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


def _approval_to_domain(row: ApprovalRequestRow) -> ApprovalRequest:
    return ApprovalRequest(
        id=ApprovalRequestId.parse(row.id),
        agent_id=AgentId.parse(row.agent_id),
        capability=Capability(row.capability),
        resource_ref=row.resource_ref,
        status=ApprovalStatus(row.status),
        requested_at=row.requested_at,
        decided_at=row.decided_at,
        decided_by_user_id=UserId.parse(row.decided_by_user_id) if row.decided_by_user_id else None,
        reason=row.reason,
        expires_at=row.expires_at,
        consumed_at=row.consumed_at,
        revoked_at=row.revoked_at,
        revoked_by_user_id=(
            UserId.parse(row.revoked_by_user_id) if row.revoked_by_user_id else None
        ),
        revoke_reason=row.revoke_reason,
        version=row.version,
    )


def _invocation_to_domain(row: ToolInvocationRow) -> ToolInvocation:
    return ToolInvocation(
        id=ToolInvocationId.parse(row.id),
        agent_id=AgentId.parse(row.agent_id),
        token_id=AgentTokenId.parse(row.token_id) if row.token_id else None,
        tool_name=row.tool_name,
        capability=Capability(row.capability),
        idempotency_key=row.idempotency_key,
        arguments_digest=row.arguments_digest,
        resource_ref=row.resource_ref,
        status=InvocationStatus(row.status),
        outcome=row.outcome,
        approval_request_id=(
            ApprovalRequestId.parse(row.approval_request_id) if row.approval_request_id else None
        ),
        invoked_at=row.invoked_at,
        completed_at=row.completed_at,
        chain_sequence=row.chain_sequence,
        previous_hash=row.previous_hash,
        intent_hash=row.intent_hash,
        outcome_hash=row.outcome_hash,
    )


class SqlAlchemyAgentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, agent: AgentIdentity) -> None:
        self._session.add(
            AgentRow(
                id=_as_uuid(agent.id),
                display_name=agent.display_name,
                owner_user_id=_as_uuid(agent.owner_user_id),
                runtime_identity=str(agent.runtime_identity),
                capabilities=_encode_capabilities(agent.capabilities),
                status=agent.status.value,
                max_token_ttl_seconds=agent.max_token_ttl_seconds,
                version=agent.version,
                created_at=agent.created_at,
                updated_at=agent.updated_at,
                revoked_at=agent.revoked_at,
            )
        )
        agent.mark_persisted()

    def get(self, agent_id: AgentId) -> AgentIdentity | None:
        row = self._session.get(AgentRow, _as_uuid(agent_id))
        return _agent_to_domain(row) if row else None

    def get_by_runtime_identity(self, runtime_identity: str) -> AgentIdentity | None:
        row = self._session.execute(
            select(AgentRow).where(AgentRow.runtime_identity == runtime_identity)
        ).scalar_one_or_none()
        return _agent_to_domain(row) if row else None

    def list_for_owner(self, owner_user_id: UserId) -> list[AgentIdentity]:
        rows = (
            self._session.execute(
                select(AgentRow)
                .where(AgentRow.owner_user_id == _as_uuid(owner_user_id))
                .order_by(AgentRow.created_at)
            )
            .scalars()
            .all()
        )
        return [_agent_to_domain(row) for row in rows]

    def save(self, agent: AgentIdentity) -> None:
        """Persist the aggregate, refusing to overwrite a newer stored version.

        The compare-and-set is one ``UPDATE ... WHERE version = :expected``, as in
        :meth:`SqlAlchemyAgentRunRepository.save`. Without it two writers that
        loaded the same agent both succeed and the loser's commit wins silently:
        a copy taken before a revoke writes ``status=active`` and
        ``revoked_at=NULL`` back over the revoked row, restoring a principal a
        human deliberately stopped, and a copy taken before a narrowing restores
        the capabilities it removed. Both are silent re-grants of withdrawn
        authority, so the loser must reload rather than overwrite.
        """
        row = self._session.get(AgentRow, _as_uuid(agent.id))
        if row is None:
            self.add(agent)
            return
        expected_version = agent.persisted_version
        result = self._session.execute(
            update(AgentRow)
            .where(
                AgentRow.id == _as_uuid(agent.id),
                AgentRow.version == expected_version,
            )
            .values(
                display_name=agent.display_name,
                capabilities=_encode_capabilities(agent.capabilities),
                status=agent.status.value,
                max_token_ttl_seconds=agent.max_token_ttl_seconds,
                version=agent.version,
                updated_at=agent.updated_at,
                revoked_at=agent.revoked_at,
            )
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            current = self._session.execute(
                select(AgentRow.version).where(AgentRow.id == _as_uuid(agent.id))
            ).scalar_one_or_none()
            raise VersionConflict(
                expected=int(expected_version),
                actual=int(current) if current is not None else int(expected_version),
            )
        agent.mark_persisted()


class SqlAlchemyDelegationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, delegation: DelegatedAuthorization) -> None:
        self._session.add(
            DelegationRow(
                id=_as_uuid(delegation.id),
                agent_id=_as_uuid(delegation.agent_id),
                delegator_user_id=_as_uuid(delegation.delegator_user_id),
                capabilities=_encode_capabilities(delegation.capabilities),
                status=delegation.status.value,
                created_at=delegation.created_at,
                revoked_at=delegation.revoked_at,
            )
        )

    def get(self, delegation_id: DelegatedAuthorizationId) -> DelegatedAuthorization | None:
        row = self._session.get(DelegationRow, _as_uuid(delegation_id))
        return _delegation_to_domain(row) if row else None

    def list_for_agent(self, agent_id: AgentId) -> list[DelegatedAuthorization]:
        rows = (
            self._session.execute(
                select(DelegationRow)
                .where(DelegationRow.agent_id == _as_uuid(agent_id))
                .order_by(DelegationRow.created_at)
            )
            .scalars()
            .all()
        )
        return [_delegation_to_domain(row) for row in rows]

    def list_active_for_delegator(self, user_id: UserId) -> list[DelegatedAuthorization]:
        rows = (
            self._session.execute(
                select(DelegationRow)
                .where(
                    DelegationRow.delegator_user_id == _as_uuid(user_id),
                    DelegationRow.status == DelegationStatus.ACTIVE.value,
                )
                .order_by(DelegationRow.created_at)
            )
            .scalars()
            .all()
        )
        return [_delegation_to_domain(row) for row in rows]

    def save(self, delegation: DelegatedAuthorization) -> None:
        row = self._session.get(DelegationRow, _as_uuid(delegation.id))
        if row is None:
            self.add(delegation)
            return
        row.capabilities = _encode_capabilities(delegation.capabilities)
        row.status = delegation.status.value
        row.revoked_at = delegation.revoked_at

    def revoke_all_for_agent(self, agent_id: AgentId, *, now: datetime) -> int:
        result = self._session.execute(
            update(DelegationRow)
            .where(
                DelegationRow.agent_id == _as_uuid(agent_id),
                DelegationRow.status == DelegationStatus.ACTIVE.value,
            )
            .values(status=DelegationStatus.REVOKED.value, revoked_at=now)
        )
        return cast(CursorResult[Any], result).rowcount


class SqlAlchemyAgentTokenRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, token: AgentToken) -> None:
        self._session.add(
            AgentTokenRow(
                id=_as_uuid(token.id),
                agent_id=_as_uuid(token.agent_id),
                delegation_id=_as_uuid(token.delegation_id),
                audience=token.audience.value,
                capabilities=_encode_capabilities(token.capabilities),
                token_hash=token.token_hash,
                issued_at=token.issued_at,
                expires_at=token.expires_at,
                revoked_at=token.revoked_at,
            )
        )

    def get_by_token_hash(self, token_hash: str) -> AgentToken | None:
        row = self._session.execute(
            select(AgentTokenRow).where(AgentTokenRow.token_hash == token_hash)
        ).scalar_one_or_none()
        return _token_to_domain(row) if row else None

    def save(self, token: AgentToken) -> None:
        row = self._session.get(AgentTokenRow, _as_uuid(token.id))
        if row is None:
            self.add(token)
            return
        row.revoked_at = token.revoked_at

    def revoke_all_for_delegation(
        self, delegation_id: DelegatedAuthorizationId, *, now: datetime
    ) -> int:
        result = self._session.execute(
            update(AgentTokenRow)
            .where(
                AgentTokenRow.delegation_id == _as_uuid(delegation_id),
                AgentTokenRow.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return cast(CursorResult[Any], result).rowcount

    def revoke_all_for_agent(self, agent_id: AgentId, *, now: datetime) -> int:
        result = self._session.execute(
            update(AgentTokenRow)
            .where(
                AgentTokenRow.agent_id == _as_uuid(agent_id),
                AgentTokenRow.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
        return cast(CursorResult[Any], result).rowcount


class SqlAlchemyApprovalRequestRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, request: ApprovalRequest) -> None:
        self._session.add(
            ApprovalRequestRow(
                id=_as_uuid(request.id),
                agent_id=_as_uuid(request.agent_id),
                capability=request.capability.value,
                resource_ref=request.resource_ref,
                status=request.status.value,
                requested_at=request.requested_at,
                decided_at=request.decided_at,
                decided_by_user_id=(
                    _as_uuid(request.decided_by_user_id)
                    if request.decided_by_user_id is not None
                    else None
                ),
                reason=request.reason,
                # The validity window and single-use stamp belong to the row
                # from the moment it is created. Omitting them here wrote NULL,
                # which ``is_usable`` reads as "never expires" — silently
                # undoing the bound the aggregate stamped in ``open``.
                expires_at=request.expires_at,
                consumed_at=request.consumed_at,
                revoked_at=request.revoked_at,
                revoked_by_user_id=(
                    _as_uuid(request.revoked_by_user_id)
                    if request.revoked_by_user_id is not None
                    else None
                ),
                revoke_reason=request.revoke_reason,
                version=request.version,
            )
        )
        request.mark_persisted()

    def get(self, approval_id: ApprovalRequestId) -> ApprovalRequest | None:
        row = self._session.get(ApprovalRequestRow, _as_uuid(approval_id))
        return _approval_to_domain(row) if row else None

    def list_pending_for_agent(self, agent_id: AgentId) -> list[ApprovalRequest]:
        rows = (
            self._session.execute(
                select(ApprovalRequestRow)
                .where(
                    ApprovalRequestRow.agent_id == _as_uuid(agent_id),
                    ApprovalRequestRow.status == ApprovalStatus.PENDING.value,
                )
                .order_by(ApprovalRequestRow.requested_at)
            )
            .scalars()
            .all()
        )
        return [_approval_to_domain(row) for row in rows]

    def list_for_agent(self, agent_id: AgentId) -> list[ApprovalRequest]:
        rows = (
            self._session.execute(
                select(ApprovalRequestRow)
                .where(ApprovalRequestRow.agent_id == _as_uuid(agent_id))
                .order_by(ApprovalRequestRow.requested_at)
            )
            .scalars()
            .all()
        )
        return [_approval_to_domain(row) for row in rows]

    def save(self, request: ApprovalRequest) -> None:
        """Persist the decision, refusing to overwrite a newer stored version.

        The compare-and-set mirrors :meth:`SqlAlchemyAgentRunRepository.save`.
        Without it a stale writer that loaded the request before a decision
        rewrites ``status``, ``consumed_at`` and ``revoked_at`` wholesale: an
        ``approved`` copy saved after a revoke silently restores withdrawn
        authority, and a pre-consumption copy clears ``consumed_at`` so a spent
        approval can be spent twice. Both are silent re-grants, so the loser of
        the race must reload rather than overwrite.
        """
        row = self._session.get(ApprovalRequestRow, _as_uuid(request.id))
        if row is None:
            self.add(request)
            return
        expected_version = request.persisted_version
        result = self._session.execute(
            update(ApprovalRequestRow)
            .where(
                ApprovalRequestRow.id == _as_uuid(request.id),
                ApprovalRequestRow.version == expected_version,
            )
            .values(
                status=request.status.value,
                decided_at=request.decided_at,
                decided_by_user_id=(
                    _as_uuid(request.decided_by_user_id)
                    if request.decided_by_user_id is not None
                    else None
                ),
                reason=request.reason,
                expires_at=request.expires_at,
                consumed_at=request.consumed_at,
                revoked_at=request.revoked_at,
                revoked_by_user_id=(
                    _as_uuid(request.revoked_by_user_id)
                    if request.revoked_by_user_id is not None
                    else None
                ),
                revoke_reason=request.revoke_reason,
                version=request.version,
            )
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            current = self._session.execute(
                select(ApprovalRequestRow.version).where(
                    ApprovalRequestRow.id == _as_uuid(request.id)
                )
            ).scalar_one_or_none()
            raise VersionConflict(
                expected=int(expected_version),
                actual=int(current) if current is not None else int(expected_version),
            )
        request.mark_persisted()

    def consume(self, approval_id: ApprovalRequestId, *, now: datetime) -> bool:
        result = self._session.execute(
            update(ApprovalRequestRow)
            .where(
                ApprovalRequestRow.id == _as_uuid(approval_id),
                ApprovalRequestRow.status == ApprovalStatus.APPROVED.value,
                ApprovalRequestRow.consumed_at.is_(None),
                # Fail closed: a row with no recorded bound is not spendable.
                # Permitting NULL here would make an unpersisted column a
                # permanent grant, which is the bug this predicate guards.
                ApprovalRequestRow.expires_at.is_not(None),
                ApprovalRequestRow.expires_at > now,
                # A revoked grant is not spendable even if a concurrent writer
                # has not yet flipped the status column.
                ApprovalRequestRow.revoked_at.is_(None),
            )
            .values(consumed_at=now, version=ApprovalRequestRow.version + 1)
        )
        return cast(CursorResult[Any], result).rowcount == 1

    def revoke_granted_for_agent(self, agent_id: AgentId, *, now: datetime, reason: str) -> int:
        result = self._session.execute(
            update(ApprovalRequestRow)
            .where(
                ApprovalRequestRow.agent_id == _as_uuid(agent_id),
                ApprovalRequestRow.status == ApprovalStatus.APPROVED.value,
                # A spent approval is history: the act it authorised already
                # happened, and marking it revoked would claim an authorisation
                # was withdrawn before it was used.
                ApprovalRequestRow.consumed_at.is_(None),
                # Do not overwrite an earlier revocation's stamp or reason; the
                # first withdrawal is the one an auditor needs.
                ApprovalRequestRow.revoked_at.is_(None),
            )
            .values(
                status=ApprovalStatus.REVOKED.value,
                revoked_at=now,
                # No human performed this withdrawal, so ``revoked_by_user_id``
                # stays NULL — that is the honest record. The reason carries the
                # cause instead of inventing an approver.
                revoke_reason=reason,
                version=ApprovalRequestRow.version + 1,
            )
        )
        return cast(CursorResult[Any], result).rowcount


class SqlAlchemyToolInvocationRepository:
    """SQLAlchemy adapter for the tool-invocation audit trail."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, invocation: ToolInvocation) -> None:
        self._session.add(
            ToolInvocationRow(
                id=_as_uuid(invocation.id),
                agent_id=_as_uuid(invocation.agent_id),
                token_id=(
                    _as_uuid(invocation.token_id) if invocation.token_id is not None else None
                ),
                tool_name=invocation.tool_name,
                capability=invocation.capability.value,
                idempotency_key=invocation.idempotency_key,
                arguments_digest=invocation.arguments_digest,
                resource_ref=invocation.resource_ref,
                status=invocation.status.value,
                outcome=invocation.outcome,
                approval_request_id=(
                    _as_uuid(invocation.approval_request_id)
                    if invocation.approval_request_id is not None
                    else None
                ),
                invoked_at=invocation.invoked_at,
                completed_at=invocation.completed_at,
                chain_sequence=invocation.chain_sequence,
                previous_hash=invocation.previous_hash,
                intent_hash=invocation.intent_hash,
                outcome_hash=invocation.outcome_hash,
            )
        )

    def save(self, invocation: ToolInvocation) -> None:
        row = self._session.get(ToolInvocationRow, _as_uuid(invocation.id))
        if row is None:
            self.add(invocation)
            return
        row.status = invocation.status.value
        row.outcome = invocation.outcome
        row.completed_at = invocation.completed_at
        # The outcome seal is written with the verdict it covers, in the same
        # UPDATE: a crash between the two would leave a completed row whose
        # outcome_hash is missing, which the verifier reports as tampering.
        row.outcome_hash = invocation.outcome_hash

    def get(self, invocation_id: ToolInvocationId) -> ToolInvocation | None:
        row = self._session.get(ToolInvocationRow, _as_uuid(invocation_id))
        return _invocation_to_domain(row) if row else None

    def find_by_idempotency_key(
        self, agent_id: AgentId, idempotency_key: str
    ) -> ToolInvocation | None:
        row = (
            self._session.execute(
                select(ToolInvocationRow).where(
                    ToolInvocationRow.agent_id == _as_uuid(agent_id),
                    ToolInvocationRow.idempotency_key == idempotency_key,
                )
            )
            .scalars()
            .first()
        )
        return _invocation_to_domain(row) if row else None

    def list_for_agent(
        self, agent_id: AgentId, *, limit: int = 50, offset: int = 0
    ) -> list[ToolInvocation]:
        rows = (
            self._session.execute(
                select(ToolInvocationRow)
                .where(ToolInvocationRow.agent_id == _as_uuid(agent_id))
                .order_by(ToolInvocationRow.invoked_at.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        return [_invocation_to_domain(row) for row in rows]

    def next_chain_link(self, agent_id: AgentId) -> tuple[int, str]:
        row = self._session.execute(
            select(ToolInvocationRow.chain_sequence, ToolInvocationRow.intent_hash)
            .where(ToolInvocationRow.agent_id == _as_uuid(agent_id))
            .order_by(ToolInvocationRow.chain_sequence.desc())
            .limit(1)
        ).first()
        if row is None:
            return 1, GENESIS_HASH
        return row[0] + 1, row[1]

    def list_chain(self, agent_id: AgentId) -> list[ToolInvocation]:
        """The agent's entire trail in chain order, for verification.

        Unpaginated on purpose: a partial trail cannot be verified, because the
        first entry of a window does not carry the hash of the entry before it.
        Callers are the owner-only verification use case, so this is not a
        general-purpose listing.
        """
        rows = (
            self._session.execute(
                select(ToolInvocationRow)
                .where(ToolInvocationRow.agent_id == _as_uuid(agent_id))
                .order_by(ToolInvocationRow.chain_sequence.asc())
            )
            .scalars()
            .all()
        )
        return [_invocation_to_domain(row) for row in rows]


class SqlAlchemyAgentRunRepository:
    """SQLAlchemy adapter for durable agent runs.

    A run and its steps are written as one aggregate. ``save`` updates the step
    rows in place and inserts any that are new, and it refuses a write whose
    expected version no longer matches the row, so the in-memory aggregate is the
    truth only for the version it was loaded at.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, run: AgentRun) -> None:
        self._session.add(_run_row(run))
        for step in run.ordered_steps:
            self._session.add(_step_row(run.id, step))
        run.mark_persisted()

    def save(self, run: AgentRun) -> None:
        """Persist the aggregate, refusing to overwrite a newer stored version.

        The compare-and-set is one ``UPDATE ... WHERE version = :expected``, as in
        :class:`~notes_service.infrastructure.db.repositories.SqlAlchemyNoteRepository`.
        Without it two writers that loaded the same run both succeed, and the
        loser's commit erases the winner's step progress — which for a run can
        turn a settled step back into ``pending`` and make a later resume
        re-execute an already-applied effect. The loser must instead reload.
        """
        row = self._session.get(AgentRunRow, _as_uuid(run.id))
        if row is None:
            self.add(run)
            run.mark_persisted()
            return
        expected_version = run.persisted_version
        result = self._session.execute(
            update(AgentRunRow)
            .where(
                AgentRunRow.id == _as_uuid(run.id),
                AgentRunRow.version == expected_version,
            )
            .values(
                status=run.status.value,
                version=run.version,
                last_error=run.last_error,
                updated_at=run.updated_at,
                started_at=run.started_at,
                finished_at=run.finished_at,
            )
        )
        if cast(CursorResult[Any], result).rowcount != 1:
            current = self._session.execute(
                select(AgentRunRow.version).where(AgentRunRow.id == _as_uuid(run.id))
            ).scalar_one_or_none()
            raise VersionConflict(
                expected=int(expected_version),
                actual=int(current) if current is not None else int(expected_version),
            )
        existing = {
            step_row.id: step_row
            for step_row in self._session.execute(
                select(AgentRunStepRow).where(AgentRunStepRow.run_id == _as_uuid(run.id))
            )
            .scalars()
            .all()
        }
        for step in run.ordered_steps:
            step_row = existing.pop(_as_uuid(step.id), None)
            if step_row is None:
                self._session.add(_step_row(run.id, step))
                continue
            step_row.status = step.status.value
            step_row.outcome = step.outcome
            step_row.started_at = step.started_at
            step_row.completed_at = step.completed_at
        run.mark_persisted()

    def get(self, run_id: AgentRunId) -> AgentRun | None:
        row = self._session.get(AgentRunRow, _as_uuid(run_id))
        if row is None:
            return None
        return self._hydrate(row)

    def list_for_agent(
        self, agent_id: AgentId, *, limit: int = 50, offset: int = 0
    ) -> list[AgentRun]:
        rows = (
            self._session.execute(
                select(AgentRunRow)
                .where(AgentRunRow.agent_id == _as_uuid(agent_id))
                .order_by(AgentRunRow.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )
        return [self._hydrate(row) for row in rows]

    def list_resumable_for_agent(self, agent_id: AgentId) -> list[AgentRun]:
        resumable = (RunStatus.PENDING.value, RunStatus.RUNNING.value, RunStatus.INTERRUPTED.value)
        rows = (
            self._session.execute(
                select(AgentRunRow)
                .where(
                    AgentRunRow.agent_id == _as_uuid(agent_id),
                    AgentRunRow.status.in_(resumable),
                )
                .order_by(AgentRunRow.created_at.asc())
            )
            .scalars()
            .all()
        )
        return [self._hydrate(row) for row in rows]

    def _hydrate(self, row: AgentRunRow) -> AgentRun:
        step_rows = (
            self._session.execute(
                select(AgentRunStepRow)
                .where(AgentRunStepRow.run_id == row.id)
                .order_by(AgentRunStepRow.sequence.asc())
            )
            .scalars()
            .all()
        )
        return _run_to_domain(row, list(step_rows))


def _run_row(run: AgentRun) -> AgentRunRow:
    return AgentRunRow(
        id=_as_uuid(run.id),
        agent_id=_as_uuid(run.agent_id),
        delegation_id=(_as_uuid(run.delegation_id) if run.delegation_id is not None else None),
        status=run.status.value,
        version=run.version,
        last_error=run.last_error,
        created_at=run.created_at,
        updated_at=run.updated_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


def _step_row(run_id: AgentRunId, step: RunStep) -> AgentRunStepRow:
    return AgentRunStepRow(
        id=_as_uuid(step.id),
        run_id=_as_uuid(run_id),
        sequence=step.sequence,
        tool_name=step.tool_name,
        capability=step.capability.value,
        idempotency_key=step.idempotency_key,
        arguments_digest=step.arguments_digest,
        arguments=json.dumps(step.arguments, sort_keys=True, ensure_ascii=True),
        resource_ref=step.resource_ref,
        status=step.status.value,
        outcome=step.outcome,
        started_at=step.started_at,
        completed_at=step.completed_at,
    )


def _run_to_domain(row: AgentRunRow, step_rows: list[AgentRunStepRow]) -> AgentRun:
    return AgentRun(
        id=AgentRunId.parse(row.id),
        agent_id=_as_uuid(row.agent_id),
        delegation_id=_as_uuid(row.delegation_id) if row.delegation_id else None,
        steps=[_step_to_domain(step_row) for step_row in step_rows],
        status=RunStatus(row.status),
        version=row.version,
        created_at=row.created_at,
        updated_at=row.updated_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        last_error=row.last_error,
    )


def _step_to_domain(row: AgentRunStepRow) -> RunStep:
    return RunStep(
        id=RunStepId.parse(row.id),
        sequence=row.sequence,
        tool_name=row.tool_name,
        capability=Capability(row.capability),
        idempotency_key=row.idempotency_key,
        arguments_digest=row.arguments_digest,
        arguments=json.loads(row.arguments),
        resource_ref=row.resource_ref,
        status=StepStatus(row.status),
        outcome=row.outcome,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


__all__ = [
    "SqlAlchemyAgentRepository",
    "SqlAlchemyAgentRunRepository",
    "SqlAlchemyAgentTokenRepository",
    "SqlAlchemyApprovalRequestRepository",
    "SqlAlchemyDelegationRepository",
    "SqlAlchemyToolInvocationRepository",
]
