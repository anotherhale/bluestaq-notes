"""SQLAlchemy Unit of Work.

A single :class:`Session` is shared by every repository for the duration of one
use case, so all writes succeed or fail together. The UoW never commits on
``__exit__``: the use case must call :meth:`commit` explicitly. This makes
transaction boundaries visible in the application layer and guarantees a
rollback if anything raises.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.orm import Session, sessionmaker

from notes_service.application.ports.agents import (
    AgentRepository,
    AgentTokenRepository,
    ApprovalRequestRepository,
    DelegationRepository,
)
from notes_service.application.ports.organization_repositories import (
    OrganizationMembershipRepository,
    OrganizationRepository,
    OrganizationUnitRepository,
    TeamMembershipRepository,
    TeamRepository,
)
from notes_service.application.ports.repositories import (
    ExternalIdentityRepository,
    FolderRepository,
    NotePermissionRepository,
    NoteRepository,
    NoteStarRepository,
    UnitOfWork,
    UserRepository,
)
from notes_service.application.ports.sessions import (
    LoginTransactionRepository,
    UserSessionRepository,
)
from notes_service.infrastructure.db.agent_repositories import (
    SqlAlchemyAgentRepository,
    SqlAlchemyAgentTokenRepository,
    SqlAlchemyApprovalRequestRepository,
    SqlAlchemyDelegationRepository,
)
from notes_service.infrastructure.db.organization_repositories import (
    SqlAlchemyOrganizationMembershipRepository,
    SqlAlchemyOrganizationRepository,
    SqlAlchemyOrganizationUnitRepository,
    SqlAlchemyTeamMembershipRepository,
    SqlAlchemyTeamRepository,
)
from notes_service.infrastructure.db.repositories import (
    SqlAlchemyExternalIdentityRepository,
    SqlAlchemyFolderRepository,
    SqlAlchemyLoginTransactionRepository,
    SqlAlchemyNotePermissionRepository,
    SqlAlchemyNoteRepository,
    SqlAlchemyNoteStarRepository,
    SqlAlchemyUserRepository,
    SqlAlchemyUserSessionRepository,
)


class SqlAlchemyUnitOfWork(UnitOfWork):
    """Owns a session/transaction and exposes the repository adapters.

    Declares conformance to the :class:`UnitOfWork` port so the composition root
    can hand it to use cases without a cast.
    """

    users: UserRepository
    external_identities: ExternalIdentityRepository
    notes: NoteRepository
    permissions: NotePermissionRepository
    folders: FolderRepository
    stars: NoteStarRepository
    organizations: OrganizationRepository
    organization_units: OrganizationUnitRepository
    teams: TeamRepository
    organization_memberships: OrganizationMembershipRepository
    team_memberships: TeamMembershipRepository
    sessions: UserSessionRepository
    login_transactions: LoginTransactionRepository
    agents: AgentRepository
    delegations: DelegationRepository
    agent_tokens: AgentTokenRepository
    approvals: ApprovalRequestRepository

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._session: Session | None = None

    @property
    def session(self) -> Session:
        if self._session is None:
            raise RuntimeError("UnitOfWork used outside of a context manager")
        return self._session

    def __enter__(self) -> Self:
        self._session = self._session_factory()
        self.users = SqlAlchemyUserRepository(self._session)
        self.external_identities = SqlAlchemyExternalIdentityRepository(self._session)
        self.notes = SqlAlchemyNoteRepository(self._session)
        self.permissions = SqlAlchemyNotePermissionRepository(self._session)
        self.folders = SqlAlchemyFolderRepository(self._session)
        self.stars = SqlAlchemyNoteStarRepository(self._session)
        self.organizations = SqlAlchemyOrganizationRepository(self._session)
        self.organization_units = SqlAlchemyOrganizationUnitRepository(self._session)
        self.teams = SqlAlchemyTeamRepository(self._session)
        self.organization_memberships = SqlAlchemyOrganizationMembershipRepository(self._session)
        self.team_memberships = SqlAlchemyTeamMembershipRepository(self._session)
        self.sessions = SqlAlchemyUserSessionRepository(self._session)
        self.login_transactions = SqlAlchemyLoginTransactionRepository(self._session)
        self.agents = SqlAlchemyAgentRepository(self._session)
        self.delegations = SqlAlchemyDelegationRepository(self._session)
        self.agent_tokens = SqlAlchemyAgentTokenRepository(self._session)
        self.approvals = SqlAlchemyApprovalRequestRepository(self._session)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._session is not None
        try:
            if exc_type is not None:
                self._session.rollback()
        finally:
            self._session.close()
            self._session = None

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
