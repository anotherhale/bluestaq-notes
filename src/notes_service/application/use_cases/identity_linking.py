"""Link and unlink external IdP identities (NEX-091 / M1.6).

Real OIDC login resolves a verified token through
``(provider, subject)`` to an :class:`ExternalIdentity`, and from there to a
local :class:`User` (``CompleteLogin._resolve_user``). Nothing created those
links, so a correctly configured OIDC provider could never log anyone in: every
verified subject was denied as "no local account is linked to this identity".
These two use cases are that missing path.

Why this is administrative, not self-service
--------------------------------------------
A link is *login authority*: whoever controls the IdP subject at that provider
becomes that local user. If a person could link their own IdP account to an
arbitrary local user, they could attach themselves to an owner's account and
inherit everything it can reach. Attribution therefore belongs to an operator
holding ``MANAGE_MEMBERSHIPS`` inside the tenant, and the target must be a
member of *that* tenant — so an admin in tenant A can never mint a login for a
user who only exists in tenant B.

The escalation rules follow from the same reasoning:

* an actor may not link or unlink an identity for an account whose role
  outranks their own, because that is a route to taking over a more privileged
  account;
* unlinking revokes the sessions established through that identity, so a
  revoked link cannot keep working until the cookie expires. Suspension and
  deprovisioning already do this through ``ensure_tenant_access`` at login and
  on every request; unlink closes the remaining case, where the user stays
  valid but this particular route to them must stop.

Audit
-----
Each successful change logs the actor, target, provider and subject. The
subject is an IdP identifier, not a credential, so it is safe to record and is
the fact an investigator needs ("which local account was this login attached
to"). The log is written after the change succeeds, mirroring the operator
access log: a refused attempt leaves no record claiming it happened.
"""

from __future__ import annotations

import logging

from notes_service.application.errors import AccessDenied, InvalidInput, ResourceNotFound
from notes_service.application.ports.repositories import UnitOfWork
from notes_service.application.use_cases.organization import OrganizationUseCase
from notes_service.domain.identity.entities import ExternalIdentity
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import ExternalIdentityId, ProviderSubject, UserId
from notes_service.domain.organization.entities import OrganizationMembership
from notes_service.domain.organization.value_objects import OrganizationId, OrgPermission

_LOGGER = logging.getLogger("notes_service.identity_linking")


class _ExternalIdentityAdminUseCase(OrganizationUseCase):
    """Shared resolution and guard rails for identity-link administration."""

    def _authorize_target(
        self,
        uow: UnitOfWork,
        *,
        principal: Principal,
        organization_id: OrganizationId,
        user_id: UserId,
    ) -> OrganizationMembership:
        """Resolve the target membership, refusing a non-member or a senior account.

        A user who is not a member of this organization is reported as 404, the
        same as an absent organization, so a caller cannot use this endpoint to
        discover which user ids exist elsewhere in the platform.
        """
        organization, actor_membership = self._load_organization(
            uow, organization_id=organization_id, principal=principal
        )
        actor = self._require_permission(
            organization=organization,
            membership=actor_membership,
            permission=OrgPermission.MANAGE_MEMBERSHIPS,
        )
        if uow.users.get(user_id) is None:
            raise ResourceNotFound("user not found")
        target = uow.organization_memberships.get_for_user(organization_id, user_id)
        if target is None:
            raise ResourceNotFound("user is not a member of this organization")
        if target.role.rank > actor.role.rank:
            # Linking a login is a takeover route: an admin must not be able to
            # attach an IdP subject to an account more privileged than their own.
            raise AccessDenied("cannot manage identity links for a more privileged account")
        return target


class LinkExternalIdentity(_ExternalIdentityAdminUseCase):
    """Attach an IdP ``(provider, subject)`` to an existing tenant member."""

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        user_id: UserId,
        provider: str,
        subject: str,
    ) -> ExternalIdentity:
        provider_subject = ProviderSubject(provider=provider, subject=subject)
        with self._uow_factory() as uow:
            self._authorize_target(
                uow,
                principal=principal,
                organization_id=organization_id,
                user_id=user_id,
            )
            existing = uow.external_identities.find_by_provider_subject(provider_subject)
            if existing is not None:
                if str(existing.user_id) == str(user_id):
                    # Idempotent: re-linking the same pair is a no-op, not a
                    # conflict, so a retried request cannot fail spuriously.
                    return existing
                # (provider, subject) is unique system-wide. Silently rebinding it
                # would move another user's login to this account.
                raise InvalidInput("this identity is already linked to another account")
            identity = ExternalIdentity.link(
                user_id=user_id, provider=provider_subject.provider, subject=subject
            )
            uow.external_identities.add(identity)
            uow.commit()
        _LOGGER.info(
            "external identity linked actor=%s target=%s organization=%s provider=%s subject=%s",
            principal.user_id,
            user_id,
            organization_id,
            provider_subject.provider,
            provider_subject.subject,
        )
        return identity


class UnlinkExternalIdentity(_ExternalIdentityAdminUseCase):
    """Detach a link so the IdP subject no longer authenticates as that user.

    Sessions established through the unlinked identity are revoked in the same
    transaction: the point of unlinking is to stop that login working, and a live
    cookie minted from it would keep working until it expired. Only sessions
    carrying this exact ``(provider, subject)`` are revoked, so unrelated
    sessions (a different provider, or a first-factor login) survive.
    """

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        user_id: UserId,
        identity_id: ExternalIdentityId,
    ) -> int:
        revoked = 0
        now = self._clock.now()
        with self._uow_factory() as uow:
            self._authorize_target(
                uow,
                principal=principal,
                organization_id=organization_id,
                user_id=user_id,
            )
            identity = uow.external_identities.get(identity_id)
            if identity is None or str(identity.user_id) != str(user_id):
                # Not linked to *this* member. Reported as 404 rather than
                # deleted, so one tenant's admin cannot remove a link belonging
                # to a user in another tenant.
                raise ResourceNotFound("identity link not found")
            provider_subject = identity.provider_subject
            uow.external_identities.remove(identity.id)
            revoked = uow.sessions.revoke_for_identity(
                user_id=user_id,
                provider=provider_subject.provider,
                subject=provider_subject.subject,
                now=now,
            )
            uow.commit()
        _LOGGER.info(
            "external identity unlinked actor=%s target=%s organization=%s "
            "provider=%s subject=%s sessions_revoked=%s",
            principal.user_id,
            user_id,
            organization_id,
            provider_subject.provider,
            provider_subject.subject,
            revoked,
        )
        return revoked


class ListUserExternalIdentities(OrganizationUseCase):
    """The IdP links attached to one tenant member (never another tenant's)."""

    def run(
        self,
        principal: Principal,
        *,
        organization_id: OrganizationId,
        user_id: UserId,
    ) -> list[ExternalIdentity]:
        with self._uow_factory() as uow:
            organization, actor_membership = self._load_organization(
                uow, organization_id=organization_id, principal=principal
            )
            self._require_permission(
                organization=organization,
                membership=actor_membership,
                permission=OrgPermission.MANAGE_MEMBERSHIPS,
            )
            if uow.organization_memberships.get_for_user(organization_id, user_id) is None:
                raise ResourceNotFound("user is not a member of this organization")
            return uow.external_identities.list_for_user(user_id)


__all__ = [
    "LinkExternalIdentity",
    "ListUserExternalIdentities",
    "UnlinkExternalIdentity",
]
