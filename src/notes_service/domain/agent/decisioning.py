"""Vendor-neutral decisioning: structured judgment, never authority.

A *decision* is an advisory, bounded, structured opinion about something the
platform is unsure of — which intent a request matches, how risky an operation
looks, which of several known candidates is most relevant. It is the vocabulary
for asking a judgment service a narrow question and getting an answer the
application can reason about.

Three properties are load-bearing, and all three are structural rather than
conventional:

* **A decision grants nothing.** :class:`DecisionRequest` and :class:`Decision`
  have no field that can name a permission, a capability, a principal, or a
  resource scope. There is nothing to *hold* an authorization in, so a decision
  cannot be mistaken for one. Authorization stays with the deterministic
  authorization provider, which this module does not import and cannot reach.
* **Every answer is bounded.** A choice must be one of the candidates the caller
  supplied; a score is a float in ``[0.0, 1.0]``; confidence is in the same
  range; the rationale is a short string. There is no free-form output channel,
  so a provider cannot smuggle an instruction back into the application, and a
  caller cannot be handed an unbounded payload it has to trust.
* **Abstention is a first-class outcome, not an error.** A provider that is
  disabled, unsure, slow, or malformed produces :data:`DecisionOutcome.ABSTAINED`
  with a named :class:`AbstentionReason`. Callers must treat abstention as "no
  opinion", which means falling back to deterministic behaviour. Failing closed
  is expressed by *not deciding*, never by deciding "allow".

Note the input side: a request carries a SHA-256 of the context the caller used,
not the context itself. Raw note text is never handed to a judgment service by
default, so there is nothing to retain, leak, or inject into.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from notes_service.domain.errors import ValidationError

_CONTEXT_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

# Bounds. A judgment service is a third party; every shape it can influence is
# capped so a misbehaving provider cannot grow a payload the caller must hold.
MAX_CANDIDATES = 50
MAX_CANDIDATE_LENGTH = 120
MAX_RATIONALE_LENGTH = 400
MAX_CORRELATION_LENGTH = 120
MAX_PROVIDER_NAME_LENGTH = 80


class DecisionKind(StrEnum):
    """The finite set of questions the platform may ask.

    The set is closed on purpose. A new judgment type is a code change that
    names its output shape, so a provider can never invent a decision kind the
    application has no rule for.
    """

    INTENT_ROUTING = "intent_routing"
    OPERATION_CLASSIFICATION = "operation_classification"
    CANDIDATE_SELECTION = "candidate_selection"
    RISK_SCORE = "risk_score"
    RELEVANCE = "relevance"
    COMPLETENESS = "completeness"
    APPROVAL_RECOMMENDATION = "approval_recommendation"

    @property
    def is_choice(self) -> bool:
        """Whether this kind answers with one of the supplied candidates."""
        return self in _CHOICE_KINDS

    @property
    def is_score(self) -> bool:
        """Whether this kind answers with a bounded numeric score."""
        return self in _SCORE_KINDS


_CHOICE_KINDS: frozenset[DecisionKind] = frozenset(
    {
        DecisionKind.INTENT_ROUTING,
        DecisionKind.OPERATION_CLASSIFICATION,
        DecisionKind.CANDIDATE_SELECTION,
    }
)

_SCORE_KINDS: frozenset[DecisionKind] = frozenset(
    {
        DecisionKind.RISK_SCORE,
        DecisionKind.RELEVANCE,
        DecisionKind.COMPLETENESS,
        # A *score*, not a yes/no, so there is no binary "approve" affordance for
        # a caller to mistake for a grant. The human approval workflow remains
        # the only thing that can approve an action.
        DecisionKind.APPROVAL_RECOMMENDATION,
    }
)


class DecisionOutcome(StrEnum):
    """Whether the provider reached a usable answer."""

    DECIDED = "decided"
    ABSTAINED = "abstained"


class AbstentionReason(StrEnum):
    """Why a decision carries no opinion.

    Each reason names a distinct operational situation, so telemetry can tell a
    disabled feature apart from a broken provider and from a provider that
    answered honestly but without enough confidence.
    """

    #: The feature flag is off; deterministic behaviour applies.
    DISABLED = "disabled"
    #: The provider raised, timed out, or was unreachable.
    PROVIDER_ERROR = "provider_error"
    #: The provider answered with a shape this contract does not allow.
    MALFORMED_OUTPUT = "malformed_output"
    #: The provider answered, but below the caller's confidence threshold.
    LOW_CONFIDENCE = "low_confidence"


def _validate_context_hash(value: str) -> str:
    if not isinstance(value, str) or not _CONTEXT_HASH_RE.match(value):
        raise ValidationError("context_hash must be a lowercase 64-character hex digest")
    return value


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    """A bounded question for a decision provider.

    ``candidates`` is required for, and only meaningful to, a choice kind; a
    score kind must not supply any, so the two shapes cannot be confused.
    """

    kind: DecisionKind
    context_hash: str
    candidates: tuple[str, ...] = ()
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        _validate_context_hash(self.context_hash)

        if self.kind.is_choice:
            if not self.candidates:
                raise ValidationError(f"{self.kind.value} requires at least one candidate")
        elif self.candidates:
            raise ValidationError(f"{self.kind.value} does not take candidates")

        if len(self.candidates) > MAX_CANDIDATES:
            raise ValidationError(f"at most {MAX_CANDIDATES} candidates are allowed")
        seen: set[str] = set()
        for candidate in self.candidates:
            if not isinstance(candidate, str) or not candidate.strip():
                raise ValidationError("candidates must be non-empty strings")
            if len(candidate) > MAX_CANDIDATE_LENGTH:
                raise ValidationError(
                    f"a candidate may be at most {MAX_CANDIDATE_LENGTH} characters"
                )
            if candidate in seen:
                raise ValidationError(f"duplicate candidate: {candidate!r}")
            seen.add(candidate)

        if self.correlation_id is not None and len(self.correlation_id) > MAX_CORRELATION_LENGTH:
            raise ValidationError(
                f"correlation_id may be at most {MAX_CORRELATION_LENGTH} characters"
            )


@dataclass(frozen=True, slots=True)
class Decision:
    """A provider's bounded, advisory answer.

    Constructing one validates only what is checkable in isolation: ranges,
    the choice/score split for the kind, and the rationale bound. Whether a
    choice is one of the *request's* candidates needs the request, so that check
    belongs to :func:`validate_against` and is applied by the use case.
    """

    kind: DecisionKind
    outcome: DecisionOutcome
    confidence: float
    choice: str | None = None
    score: float | None = None
    rationale: str = ""
    reason: AbstentionReason | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, DecisionKind):
            raise ValidationError("decision kind must be a DecisionKind")
        if not isinstance(self.outcome, DecisionOutcome):
            raise ValidationError("decision outcome must be a DecisionOutcome")

        if not 0.0 <= self.confidence <= 1.0:
            raise ValidationError("confidence must be between 0.0 and 1.0")

        if len(self.rationale) > MAX_RATIONALE_LENGTH:
            raise ValidationError(f"rationale may be at most {MAX_RATIONALE_LENGTH} characters")

        if self.outcome is DecisionOutcome.ABSTAINED:
            if self.reason is None:
                raise ValidationError("an abstention must name a reason")
            if self.choice is not None or self.score is not None:
                raise ValidationError("an abstention carries no choice and no score")
            return

        if self.reason is not None:
            raise ValidationError("a decided outcome must not carry an abstention reason")
        if self.kind.is_choice:
            if self.choice is None:
                raise ValidationError(f"{self.kind.value} must supply a choice")
            if self.score is not None:
                raise ValidationError(f"{self.kind.value} must not supply a score")
        else:
            if self.score is None:
                raise ValidationError(f"{self.kind.value} must supply a score")
            if self.choice is not None:
                raise ValidationError(f"{self.kind.value} must not supply a choice")
            if not 0.0 <= self.score <= 1.0:
                raise ValidationError("score must be between 0.0 and 1.0")

    @classmethod
    def abstain(
        cls,
        kind: DecisionKind,
        reason: AbstentionReason,
        *,
        confidence: float = 0.0,
    ) -> Decision:
        """Build an abstention, the fail-closed answer."""
        return cls(
            kind=kind, outcome=DecisionOutcome.ABSTAINED, confidence=confidence, reason=reason
        )


def validate_against(request: DecisionRequest, decision: Decision) -> None:
    """Check a decision against the question that produced it.

    Raises :class:`ValidationError` when the provider answered a different kind
    than it was asked, or named a choice the caller never offered. Both are
    malformed output: the use case downgrades them to an abstention rather than
    propagating an answer nobody can safely act on.
    """
    if decision.kind is not request.kind:
        raise ValidationError(
            f"provider answered {decision.kind.value} for a {request.kind.value} request"
        )
    if (
        decision.outcome is DecisionOutcome.DECIDED
        and request.kind.is_choice
        and decision.choice not in request.candidates
    ):
        raise ValidationError(f"choice {decision.choice!r} is not one of the offered candidates")


__all__ = [
    "MAX_CANDIDATES",
    "MAX_CANDIDATE_LENGTH",
    "MAX_CORRELATION_LENGTH",
    "MAX_PROVIDER_NAME_LENGTH",
    "MAX_RATIONALE_LENGTH",
    "AbstentionReason",
    "Decision",
    "DecisionKind",
    "DecisionOutcome",
    "DecisionRequest",
    "validate_against",
]
