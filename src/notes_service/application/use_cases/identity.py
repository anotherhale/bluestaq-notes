"""Identity use cases: resolve the current user and list shareable users."""

from __future__ import annotations

from notes_service.application.errors import ResourceNotFound
from notes_service.application.ports.identity import DEMO_PROVIDER_NAME
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


class ListDemoIdentities(UseCase):
    """List the seeded development demo identities.

    This exists so a client can *discover* which demo users it may act as before
    it has authenticated (the demo selector needs to populate itself). It is a
    development-only affordance: the presentation layer only exposes it when
    demo authentication is enabled, and never in production. It returns only
    active users linked to the demo provider, so it can never enumerate real or
    suspended accounts.
    """

    def run(self, *, limit: int, offset: int) -> list[User]:
        with self._uow_factory() as uow:
            identities = uow.external_identities.list_by_provider(DEMO_PROVIDER_NAME)
            users: list[User] = []
            for identity in identities:
                user = uow.users.get(identity.user_id)
                if user is not None and user.is_active:
                    users.append(user)
            users.sort(key=lambda user: (user.display_name, str(user.id)))
            return users[offset : offset + limit]

    def count(self) -> int:
        with self._uow_factory() as uow:
            identities = uow.external_identities.list_by_provider(DEMO_PROVIDER_NAME)
            total = 0
            for identity in identities:
                user = uow.users.get(identity.user_id)
                if user is not None and user.is_active:
                    total += 1
            return total
