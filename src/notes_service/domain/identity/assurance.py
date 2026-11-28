"""Authentication assurance: how strongly a human proved who they are.

ADR-015 names the deterministic signal that makes the ``step-up`` obligation
enforceable: the *recorded* authentication assurance of the delegating human. An
agent has no assurance of its own — every tool handler acts as its delegator — so
the honest question the gateway can answer is how strongly that human
authenticated when they granted the authority.

The vocabulary is closed and **ordered**. A floor is a minimum, and a free-text
assurance label in an enforcement path is only a suggestion: two deployments
spelling ``"mfa"``, ``"mfa2"`` and ``"multi-factor"`` would each silently mean
whatever they liked. Widening this enum is a vocabulary change and needs an ADR.

**Absent or unrecognised reads as the lowest rank.** An unknown value must never
become authority, so :meth:`AuthenticationAssurance.parse` fails closed to
``SINGLE_FACTOR`` rather than guessing upward. A nullable column with no writer,
or a default of multi-factor, would turn an unpersisted value into authority —
exactly the failure ADR-015 forbids.
"""

from __future__ import annotations

from enum import StrEnum


class AuthenticationAssurance(StrEnum):
    """How strongly the authenticating human proved their identity.

    ``SINGLE_FACTOR`` is one authentication mechanism (a password, or one
    federated login). ``MULTI_FACTOR`` is two or more independent mechanisms, as
    asserted by the identity provider's ``acr``/``amr`` claims.
    """

    SINGLE_FACTOR = "single-factor"
    MULTI_FACTOR = "multi-factor"

    @property
    def rank(self) -> int:
        """Ordinal strength, for comparing an assurance against a floor."""
        return _RANKS[self]

    def meets(self, floor: AuthenticationAssurance) -> bool:
        """True when this assurance is at least ``floor``.

        Written as ``>=`` on the ordinal rank rather than ``is`` so that adding a
        stronger level later does not silently stop satisfying an older floor.
        """
        return self.rank >= floor.rank

    @classmethod
    def parse(cls, value: object) -> AuthenticationAssurance:
        """Coerce a stored or supplied value, failing closed to single-factor.

        Never raises: a stored value this build does not recognise (an older or
        newer writer) must degrade to the weakest level rather than make an
        enforcement path blow up or, worse, skip its check.
        """
        if isinstance(value, cls):
            return value
        if isinstance(value, str):
            try:
                return cls(value)
            except ValueError:
                return cls.SINGLE_FACTOR
        return cls.SINGLE_FACTOR


# The one place ordinal strength is defined. Kept out of the enum body so a
# member cannot shadow it and so the ordering is visible at a glance.
_RANKS: dict[AuthenticationAssurance, int] = {
    AuthenticationAssurance.SINGLE_FACTOR: 1,
    AuthenticationAssurance.MULTI_FACTOR: 2,
}

# The minimum assurance a ``step-up`` obligation demands. A static application
# constant, never read from a policy version or a request: a policy author must
# not be able to lower the bar for what counts as "stepped up" (ADR-015).
STEP_UP_FLOOR: AuthenticationAssurance = AuthenticationAssurance.MULTI_FACTOR
