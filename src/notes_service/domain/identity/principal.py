"""The :class:`Principal` produced by authentication.

A principal is the authenticated *application* identity for the duration of a
request. It deliberately carries only the information authorization needs, plus
non-authoritative provenance metadata for logging and audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from notes_service.domain.identity.assurance import AuthenticationAssurance
from notes_service.domain.identity.value_objects import Email, UserId


@dataclass(frozen=True, slots=True)
class Principal:
    """An authenticated caller.

    ``user_id`` is the sole authoritative identity used by authorization. The
    ``provider`` and ``subject`` fields record *how* the caller authenticated
    and must never be used as an ownership source.

    ``assurance`` records how *strongly* the caller authenticated (ADR-015). It
    is derived by the identity adapter from the IdP's ``acr``/``amr`` claims and
    is never accepted from a request. It defaults to ``SINGLE_FACTOR`` so a
    principal built without an explicit assurance can only ever satisfy the
    weakest floor.
    """

    user_id: UserId
    email: Email
    display_name: str
    provider: str
    subject: str
    claims: dict[str, object] = field(default_factory=dict)
    assurance: AuthenticationAssurance = AuthenticationAssurance.SINGLE_FACTOR

    def __post_init__(self) -> None:
        if not self.provider:
            raise ValueError("principal provider must not be empty")
        if not self.subject:
            raise ValueError("principal subject must not be empty")
