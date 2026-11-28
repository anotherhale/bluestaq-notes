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
* **Every timestamp must be timezone-aware.** A naive datetime does not name an
  instant, it names a wall-clock reading in an unstated zone, so two deployments
  reading the same grant would disagree about when it expires. The old code
  accepted one and then compared it to an aware clock, which raised ``TypeError`` —
  a 500 on the grant-issue path, and a bound whose meaning depended on which side
  happened to be naive. Refusing it at construction makes the requirement
  checkable once, for every writer, instead of at each comparison.
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

MAX_RATIONALE_LENGTH = 500
"""Upper bound on the free-text reason an operator records for a grant.

A rationale is human-written prose explaining *why* authority was widened. The
bound is generous enough for a sentence or two of context and small enough that
the column is a note, not a document store. It is enforced here rather than only
in the request schema so every path that constructs a grant — HTTP, a future CLI,
a test — obeys the same limit.
"""


@dataclass(frozen=True, slots=True)
class OperatorGrantRationale:
    """Why an operator widened someone else's authority, as a validated value.

    A grant record answers *who* may do *what* until *when*. It cannot answer
    *why*, and "why" is the question an auditor actually asks about a privileged
    hand-out. Before this value existed the rationale had no column and was
    therefore discarded at the boundary; recording intent on a widening is the
    difference between an audit that can reconstruct a decision and one that can
    only list its effects.

    Normalisation and bounds are enforced at construction, so a caller cannot
    store an empty or over-long reason that would later read as a real one:

    * **Not blank.** A rationale of only whitespace is absence pretending to be a
      record; it is rejected rather than silently trimmed to nothing.
    * **Trimmed, not interior-collapsed.** Surrounding whitespace is stripped
      (it is never meaningful in a note); interior spacing and newlines are kept,
      because the operator's own sentence structure is part of the record.
    * **Bounded.** See :data:`MAX_RATIONALE_LENGTH`.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise ValidationError("operator grant rationale must be text")
        trimmed = self.value.strip()
        if not trimmed:
            raise ValidationError("operator grant rationale must not be blank")
        if len(trimmed) > MAX_RATIONALE_LENGTH:
            raise ValidationError(
                f"operator grant rationale must be at most {MAX_RATIONALE_LENGTH} characters"
            )
        # Freeze the normalised form so equality and persistence agree with what
        # the operator actually meant, not with incidental padding.
        object.__setattr__(self, "value", trimmed)

    def __str__(self) -> str:
        return self.value


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

    ``rationale`` is the *why*: the operator's stated reason for widening this
    user's authority. It is required for every human-issued grant (``granted_by``
    set) and must be absent for a configuration grant, because the two are
    different facts — configuration is reviewed with the deployment, whereas a
    hand-out between people needs a recorded justification. Requiring it here,
    at the type level, means a caller cannot create an attributable grant whose
    purpose is unknown.
    """

    user_id: UserId
    scope: str
    granted_at: datetime
    granted_by: UserId | None = None
    not_before: datetime | None = None
    expires_at: datetime | None = None
    rationale: OperatorGrantRationale | None = None

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
        if self.rationale is not None and not isinstance(self.rationale, OperatorGrantRationale):
            raise ValidationError("operator grant rationale must be an OperatorGrantRationale")
        if self.granted_by is not None and self.rationale is None:
            # A grant a person issued must say why. Configuration grants are the
            # one exception: their reason is the deployment itself.
            raise ValidationError("a human-issued operator grant must carry a rationale")
        if self.granted_by is None and self.rationale is not None:
            # The reverse would attribute a reason to a deployment that never gave
            # one, which is a fabricated audit fact rather than a missing one.
            raise ValidationError("a configuration operator grant must not carry a rationale")
        # Awareness is checked before any comparison, because comparing a naive to
        # an aware datetime raises TypeError rather than returning False - an
        # unhandled 500 rather than the fail-closed refusal this type promises.
        # ``utcoffset() is None`` is checked as well as ``tzinfo is None`` because a
        # tzinfo that yields no offset (a misconfigured zone) is naive too, by the
        # same rule the stdlib uses.
        for field_name in ("granted_at", "not_before", "expires_at"):
            value = getattr(self, field_name)
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValidationError(
                    f"operator grant {field_name} must be timezone-aware; "
                    "a naive datetime names a wall-clock reading, not an instant"
                )
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


__all__ = ["MAX_RATIONALE_LENGTH", "MAX_SCOPE_LENGTH", "OperatorGrant", "OperatorGrantRationale"]
