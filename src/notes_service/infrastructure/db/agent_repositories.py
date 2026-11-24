"""SQLAlchemy implementations of the agent persistence ports.

Repositories map between ORM rows and domain aggregates and never commit; the
:class:`~notes_service.infrastructure.db.uow.SqlAlchemyUnitOfWork` owns
transaction boundaries.

Capability sets are persisted as comma-separated strings. The vocabulary is
small and fixed, and the domain validates on the way out, so this keeps one DDL
that works unchanged on SQLite and PostgreSQL without a JSON column.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from notes_service.domain.agent.delegation import DelegatedAuthorization
from notes_service.domain.agent.entities import AgentIdentity, ApprovalRequest
from notes_service.domain.agent.token import AgentToken
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
from notes_service.domain.identity.value_objects import UserId
from notes_service.infrastructure.db.models import (
    AgentRow,
    AgentTokenRow,
    ApprovalRequestRow,
    DelegationRow,
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
        row = self._session.get(AgentRow, _as_uuid(agent.id))
        if row is None:
            self.add(agent)
            return
        row.display_name = agent.display_name
        row.capabilities = _encode_capabilities(agent.capabilities)
        row.status = agent.status.value
        row.max_token_ttl_seconds = agent.max_token_ttl_seconds
        row.version = agent.version
        row.updated_at = agent.updated_at
        row.revoked_at = agent.revoked_at


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
            )
        )

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

    def save(self, request: ApprovalRequest) -> None:
        row = self._session.get(ApprovalRequestRow, _as_uuid(request.id))
        if row is None:
            self.add(request)
            return
        row.status = request.status.value
        row.decided_at = request.decided_at
        row.decided_by_user_id = (
            _as_uuid(request.decided_by_user_id) if request.decided_by_user_id is not None else None
        )
        row.reason = request.reason


__all__ = [
    "SqlAlchemyAgentRepository",
    "SqlAlchemyAgentTokenRepository",
    "SqlAlchemyApprovalRequestRepository",
    "SqlAlchemyDelegationRepository",
]
