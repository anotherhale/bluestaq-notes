"""Authorization port for organization administration.

This is deliberately separate from the note authorization port. They answer
different questions:

* the note policy decides whether a person may read or edit a note's *content*;
* this policy decides whether a person may administer an organization's
  *structure and membership*.

Conflating them is the classic enterprise bug where "org admin" quietly becomes
"can read everyone's private documents". Holding an administrative role here
grants no content access whatsoever.

The decision input is the caller's :class:`OrganizationMembership` (or ``None``)
plus the organization, so an implementation never needs to reach for global
state, and a future policy engine (ABAC, external PDP) can replace this one
behind the same port.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from notes_service.application.ports.authorization import Decision
from notes_service.domain.organization.entities import Organization, OrganizationMembership
from notes_service.domain.organization.value_objects import OrgPermission


@runtime_checkable
class OrganizationAuthorizationProvider(Protocol):
    """Decides whether a member may exercise an administrative capability.

    Implementations must default to denial: an absent, inactive, or
    out-of-scope membership never yields an allow.
    """

    @property
    def name(self) -> str: ...

    def decide(
        self,
        *,
        organization: Organization,
        membership: OrganizationMembership | None,
        permission: OrgPermission,
    ) -> Decision: ...


__all__ = ["OrganizationAuthorizationProvider"]
