"""The actor label recorded on a governance object (NEX-050).

Two identities meet here and only this module knows both. The authoritative
identity is a :class:`~notes_service.domain.identity.principal.Principal`'s
``user_id``, a UUID. The governance domain stores actor labels as lowercase
identifiers, because those labels land in audit records and log fields and the
domain refuses a raw UUID (it starts with a digit, and an unbounded string in an
audit record is a log-injection vector — the same rule NEX-035 applies to a
provider name).

So the mapping lives in exactly one function, in the application layer, because
both the HTTP boundary and every other caller of the policy use cases need it and
a second copy is the one that gets forgotten.

Why the label is derived from ``user_id`` and never from ``provider``/``subject``:
those record *how* the caller authenticated. Two providers can present the same
``subject`` for different people, and a subject is a claim, not an identity
source. ``user_id`` is the only field authorization trusts, so it is the only
field an attribution may use.
"""

from __future__ import annotations

from notes_service.domain.identity.principal import Principal


def actor_label(principal: Principal) -> str:
    """The stable, domain-legal label for ``principal``.

    ``user-<uuid>`` is a legal identifier (lowercase, starts with a letter) and
    keeps the value recognisable as a user rather than a policy id, so an audit
    line naming it can be read without a lookup.
    """
    return f"user-{principal.user_id}"


__all__ = ["actor_label"]
