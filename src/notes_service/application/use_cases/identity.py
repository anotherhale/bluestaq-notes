"""Identity use cases: resolve the current user and list shareable users."""

from __future__ import annotations

from notes_service.application.errors import ResourceNotFound
from notes_service.application.use_cases.base import UseCase
from notes_service.domain.identity.entities import User
from notes_service.domain.identity.principal import Principal


class GetCurrentUser(UseCase):
    """Resolve the principal to a full user record."""

    def run(self, principal: Principal) -> User:
        with self._uow_factory() as uow:
            user = uow.users.get(principal.user_id)
            if user is None or not user.is_active:
                # A principal for an unknown/inactive user must never be usable.
                raise ResourceNotFound("current user not found")
            return user


class ListUsers(UseCase):
    """List users available for sharing.

    Returns only the fields required for sharing (id, email, display name);
    other attributes stay internal.
    """

    def run(self, principal: Principal, *, limit: int, offset: int) -> list[User]:
        with self._uow_factory() as uow:
            return uow.users.list_all(limit=limit, offset=offset)

    def count(self, principal: Principal) -> int:
        with self._uow_factory() as uow:
            return uow.users.count()
