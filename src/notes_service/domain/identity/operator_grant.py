"""A stored operator grant: who may read which platform-wide view, until when.

Until this slice the operator allowlist was a static environment variable
(``OPS_OPERATOR_USER_IDS``). Scoping (NEX-044) narrowed *what* each grant covers,
but two things were still impossible and both were structural, not cosmetic:

* **a grant could not expire.** The only way to revoke one was to edit
  configuration and redeploy. A grant handed to someone for an incident outlives
  the incident, and the deployment has no record that it should not.
* **nobody could see who holds what.** The allowlist answered "is this caller
  allowed" and nothing answered "who is currently an operator, and since when".
  An audit of a privileged surface that cannot enumerate its own holders is not
  an audit.

Both need a *record* — a value with a lifetime and a provenance — rather than a
narrower string. This is that record.

Why the domain and not the config
---------------------------------

Expiry is a business rule ("a grant past its ``expires_at`` confers nothing"),
not a parsing detail, so it lives here where it is unit-testable without a
settings object and cannot drift between the gate and any future reader. The
environment variable remains a *seed path*: it still produces grants at startup,
but the gate consults a store of these objects rather than the string.

Fail-closed decisions, stated
-----------------------------

* **A grant with no scope confers nothing.** Same rule as NEX-044's parser, at the
  type level: an empty scope set is not "all", it is "none".
* **A grant whose ``expires_at`` has passed confers nothing.** The comparison is
  ``now >= expires_at``, so a grant is dead at the instant it expires rather than
  one tick later.
* **A grant whose ``not_before`` is in the future confers nothing yet.** A grant
  that is not yet valid is not an early grant.
* **The clock is a parameter, never read here.** R29 already cost this programme a
  defect where one clock wrote a bound and another read it. ``is_active`` takes
  the caller's ``now`` so the gate's clock and the bound's clock are provably the
  same one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from notes_service.domain.errors import ValidationError
from notes_service.domain.identity.value_objects import UserId

MAX_SCOPE_LENGTH = 64
"""Upper bound on a scope token.

Scope names are fixed identifiers each use case chooses (``withholding-report``),
never caller-supplied text, so this bound only catches a typo or a pasted blob.
It is deliberately generous relative to the longest real name.
"""


@dataclass(frozen=True, slots=True)
class OperatorGrant:
    """One operator's permission to read one platform-wide operation.

    A grant is immutable and compared by value. Several grants for one user are
    normal — that is how one operator holds two scopes — so nothing here assumes
    a user has exactly one.

    ``granted_by`` is the actor who issued the grant, or ``None`` for a grant
    that came from deployment configuration (the seed path). Recording it means
    a stored grant can answer "who gave this", which a config string never can.
    ``None`` is an honest "no human issued this", not a missing field.
    """

    user_id: UserId
    scope: str
    granted_at: datetime
    granted_by: UserId | None = None
    not_before: datetime | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, UserId):
            raise ValidationError("operator grant user_id must be a UserId")
        if self.granted_by is not None and not isinstance(self.granted_by, UserId):
            raise ValidationError("operator grant granted_by must be a UserId or None")
        scope = self.scope.strip() if isinstance(self.scope, str) else ""
        if not scope:
            raise ValidationError("operator grant scope must not be empty")
        if len(scope) > MAX_SCOPE_LENGTH:
            raise ValidationError(
                f"operator grant scope must be at most {MAX_SCOPE_LENGTH} characters"
            )
        if scope != self.scope:
            raise ValidationError("operator grant scope must not have surrounding whitespace")
        if self.expires_at is not None and self.expires_at <= self.granted_at:
            # A grant that expires at or before it was issued is a mistake that
            # would otherwise look like a working grant until the first check.
            raise ValidationError("operator grant expires_at must be after granted_at")
        if (
            self.not_before is not None
            and self.expires_at is not None
            and self.not_before >= self.expires_at
        ):
            raise ValidationError("operator grant not_before must be before expires_at")

    def is_active(self, *, now: datetime) -> bool:
        """True when this grant confers its scope at ``now``.

        ``now`` is supplied by the caller's clock (R29): the same clock that
        stamped the request decides whether the grant covers it. A grant is
        inactive before ``not_before``, at or after ``expires_at``, and — by
        definition — a bare grant with no bounds is active once granted.
        """
        if now < self.granted_at:
            # A grant cannot be used before it was issued. This is not pedantry:
            # a clock skew that put granted_at in the future would otherwise
            # authorise a grant that has not happened yet.
            return False
        if self.not_before is not None and now < self.not_before:
            return False
        return self.expires_at is None or now < self.expires_at

    @property
    def is_expiring(self) -> bool:
        """True when this grant has a deadline at all."""
        return self.expires_at is not None


__all__ = ["MAX_SCOPE_LENGTH", "OperatorGrant"]
