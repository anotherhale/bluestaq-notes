"""How much damage one tool call can do, and what the platform owes it (NEX-063).

The security architecture asks for two things the platform did not yet have:

* an authorization decision that carries **risk** (section on
  ``AuthorizationDecision`` fields), so a reader of the record can tell a routine
  read from an authority-widening action without re-deriving it; and
* **risk-based approval** — "broad-impact or lockout-prone changes require
  risk-based approval" — so the strength of the human gate follows the blast
  radius of the action, not only what a policy author happened to write.

Both need one thing first: a *deterministic* answer to "how risky is this?".
That answer lives here, and the shape of it is the control.

Why this is a pure function over the capability
-----------------------------------------------

The temptation is to let a model score risk, because "risk" sounds like
judgement. Section 10 of the operating directive forbids exactly that: a model
may reason and recommend, but it may never widen or narrow its own authority.
A risk score that feeds an approval threshold *is* authority, so it cannot be
model-produced. It is derived from facts the enforcement path can attest to —
the capability's declared blast radius — and nothing else.

The derivation is therefore total, deterministic and I/O-free: the same
capability always yields the same tier. A reviewer can reproduce any tier by
reading this file, which is what makes the recorded risk explainable rather
than merely plausible.

Risk may only ever *raise* the gate
-----------------------------------

:func:`required_approvals_for` takes the threshold a published policy demanded
and the tier this module derived, and returns the stricter of the two. It can
never return fewer signatures than either input asks for. A policy that is
silent therefore does not *weaken* a high-risk action, and a high-risk action
cannot be argued down by a policy version — the floor is code, not policy. This
is the same rule ``step-up`` follows for its assurance floor: the control that
requests a protection must not be able to define it away.

Which capabilities are high risk, and why not all of them
--------------------------------------------------------

``SENSITIVE_CAPABILITIES`` already marks the four capabilities that need *a*
human. If risk were just "sensitive or not", it would be a rename of that set
and would tell a reviewer nothing new. The tier instead separates the sensitive
capabilities by **what the action can destroy or expose**:

* ``HIGH`` — the action reaches beyond the acting owner's own boundary:
  organisation-wide search (``ORG_SEARCH``). This is the only capability whose
  blast radius is other tenants' data at once.
* ``ELEVATED`` — the action is sensitive but bounded to the owner's own data
  (``DELETE_NOTE``, ``SHARE_NOTE``, ``REVOKE_SHARE``).
* ``LOW`` — reading, creating, appending and listing.

The tier is a *closed, ordered* vocabulary for the same reason assurance is:
"at least elevated" is a question a caller will want to ask, and an ordered
enum answers it without a second lookup table that could disagree.

Not yet reachable, and said so plainly
--------------------------------------

The two-approver floor applies to ``HIGH``. Today ``ORG_SEARCH`` is the only
``HIGH`` capability and it is also non-delegable, so no agent token can reach it
and the floor has no production caller yet. It is kept because it is the correct
answer for the day a future ADR permits delegated org-wide search: the control
is in place before the capability that needs it, rather than bolted on after.
Every other tier's behaviour is exercised today, and the pure function that
decides the threshold is tested directly rather than through a path that cannot
exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from notes_service.domain.agent.value_objects import (
    SENSITIVE_CAPABILITIES,
    Capability,
)


class RiskTier(StrEnum):
    """An ordered blast-radius class. Higher tiers are strictly more dangerous."""

    LOW = "low"
    ELEVATED = "elevated"
    HIGH = "high"

    @property
    def rank(self) -> int:
        return _TIER_RANK[self]

    def at_least(self, other: RiskTier) -> bool:
        """Whether this tier is ``other`` or more dangerous.

        Exists so a policy or a test can ask "elevated or above" without
        comparing string values, which would silently break if the vocabulary
        were ever reordered for readability.
        """
        return self.rank >= other.rank


_TIER_RANK: dict[RiskTier, int] = {
    RiskTier.LOW: 0,
    RiskTier.ELEVATED: 1,
    RiskTier.HIGH: 2,
}

#: Capabilities whose blast radius crosses the acting owner's boundary. A
#: superset relationship with ``SENSITIVE_CAPABILITIES`` is asserted in the
#: tests: a high-risk capability that needs no human would be a contradiction.
_HIGH_RISK: frozenset[Capability] = frozenset({Capability.ORG_SEARCH})


@dataclass(frozen=True, slots=True)
class ActionRisk:
    """The risk of one capability's use, with the reasons that produced it.

    ``reasons`` are short machine-readable tokens rather than prose, so an audit
    reader or a test can assert *why* a tier was assigned. A tier with no visible
    reason is an assertion; a tier with reasons is a derivation.
    """

    tier: RiskTier
    reasons: tuple[str, ...] = ()

    @property
    def requires_second_approver(self) -> bool:
        """Whether this tier alone demands two distinct humans, policy aside."""
        return self.tier is RiskTier.HIGH

    @property
    def is_sensitive(self) -> bool:
        return self.tier.at_least(RiskTier.ELEVATED)


def assess_action_risk(capability: Capability) -> ActionRisk:
    """Classify one capability's blast radius. Pure, total, deterministic.

    Raises ``TypeError`` for a non-``Capability`` rather than coercing: a string
    that happens to spell a capability name is not a capability, and accepting it
    would let a caller reach an untiered value through the back door.
    """
    if not isinstance(capability, Capability):
        raise TypeError("risk is assessed over a Capability, not " + type(capability).__name__)

    if capability in _HIGH_RISK:
        return ActionRisk(
            tier=RiskTier.HIGH,
            reasons=("cross-owner-blast-radius", "org-wide-reach"),
        )

    if capability in SENSITIVE_CAPABILITIES:
        # Everything else in the sensitive set is bounded to data the acting
        # owner already controls: destructive or visibility-changing, but not a
        # reach into anyone else's boundary.
        return ActionRisk(
            tier=RiskTier.ELEVATED,
            reasons=("human-gated-capability", "owner-scoped-blast-radius"),
        )

    return ActionRisk(tier=RiskTier.LOW, reasons=("read-or-create-scope",))


def required_approvals_for(*, policy_threshold: int, risk: ActionRisk) -> int:
    """The number of distinct humans this action needs, never below either input.

    ``policy_threshold`` is what a published policy demanded (``1`` when the
    policy is silent, ``2`` for ``second-approver``). The risk floor is ``2`` for
    a high-risk action and ``1`` otherwise. The result is the maximum, so:

    * a silent policy does not reduce a high-risk action to one signature, and
    * a policy may still raise a low-risk action to two.

    The aggregate only models 1 or 2 (see ``ApprovalRequest``), so the result is
    clamped into that closed set rather than passed through. Returning ``3``
    would describe a requirement no code path can satisfy.
    """
    floor = 2 if risk.requires_second_approver else 1
    return min(2, max(1, policy_threshold, floor))
