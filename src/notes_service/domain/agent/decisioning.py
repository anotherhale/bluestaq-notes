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
default, so there is nothing to retain, leak, or inject into. *What* is hashed is
decided by :mod:`notes_service.domain.agent.context`, because a digest of
sensitive content is still a fingerprint of it — hashing is not declassification.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from notes_service.domain.agent.context import ContextEnvelope, ContextHash
from notes_service.domain.errors import ValidationError

# Bounds. A judgment service is a third party; every shape it can influence is
# capped so a misbehaving provider cannot grow a payload the caller must hold.
MAX_CANDIDATES = 50
MAX_CANDIDATE_LENGTH = 120
MAX_RATIONALE_LENGTH = 400
MAX_CORRELATION_LENGTH = 120
MAX_PROVIDER_NAME_LENGTH = 80

#: A provider names itself through the port's ``name`` property, so the value is
#: third-party input: it must be an identifier, not free text. Telemetry keys and
#: log fields are built from it, so a newline or a control character is a
#: log-injection vector rather than a cosmetic issue.
_PROVIDER_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def validate_provider_name(value: object) -> str:
    """Check a provider-supplied name is a bounded telemetry identifier.

    A provider name travels into audit records, structured log fields, and API
    responses. Accepting an arbitrary string there means accepting an unbounded
    field, a log-injection vector, and a value that will not aggregate in
    telemetry. The bound is checked, not the shape alone, so a provider cannot
    smuggle a paragraph in under a name that happens to start with a letter.

    Raises :class:`ValidationError` rather than coercing: a caller that silently
    truncated or sanitised the value would record a *different* provider than the
    one that answered, which is worse than recording none.
    """
    if not isinstance(value, str):
        raise ValidationError("provider name must be a string")
    if not value:
        raise ValidationError("provider name must not be empty")
    if len(value) > MAX_PROVIDER_NAME_LENGTH:
        raise ValidationError(f"provider name may be at most {MAX_PROVIDER_NAME_LENGTH} characters")
    if not _PROVIDER_NAME_PATTERN.fullmatch(value):
        raise ValidationError(
            "provider name must be a lowercase identifier of [a-z0-9._-], "
            "starting with a letter or digit"
        )
    return value


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


def _validate_context_hash(value: ContextHash) -> ContextHash:
    """Refuse anything an envelope did not mint.

    The type is the check. A ``str`` that merely looks like a digest is exactly
    the hand-assembled hash ADR-012 warned about, so it is rejected here rather
    than trusted for its shape.
    """
    if not isinstance(value, ContextHash):
        raise ValidationError(
            "context_hash must be a ContextHash minted by a ContextEnvelope, "
            "not a caller-assembled string"
        )
    return value


@dataclass(frozen=True, slots=True)
class DecisionRequest:
    """A bounded question for a decision provider.

    ``candidates`` is required for, and only meaningful to, a choice kind; a
    score kind must not supply any, so the two shapes cannot be confused.
    """

    kind: DecisionKind
    context_hash: ContextHash
    candidates: tuple[str, ...] = ()
    correlation_id: str | None = None
    #: Names (never values) of context fields withheld from ``context_hash``
    #: because they exceeded the ceiling. Recorded so an audit trail can say the
    #: provider was asked about less than the caller held — a silent drop would
    #: make "we discarded the relevant input" indistinguishable from "there was
    #: none".
    excluded_context_fields: tuple[str, ...] = ()
    #: Names withheld because the value exceeded the field cap — the subset of
    #: ``excluded_context_fields`` that signals a thinner-than-expected advisory
    #: rather than a deliberate classification exclusion. Carried separately so
    #: an operator can watch this number without parsing per-field reasons.
    over_cap_context_fields: tuple[str, ...] = ()

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

        # The over-cap names are a *subset* of the excluded names. Two records
        # that disagree about which inputs were held back are worse than one, so
        # the relation is enforced rather than assumed.
        stray = set(self.over_cap_context_fields) - set(self.excluded_context_fields)
        if stray:
            raise ValidationError(
                f"over-cap context fields must also be excluded context fields: {sorted(stray)!r}"
            )

    @classmethod
    def from_context(
        cls,
        kind: DecisionKind,
        envelope: ContextEnvelope,
        *,
        candidates: tuple[str, ...] = (),
        correlation_id: str | None = None,
    ) -> DecisionRequest:
        """Build a request from a classified envelope.

        This is the only sanctioned way to turn context into a question, because
        it is what guarantees the hash the provider sees was computed over
        content a :class:`ContextEnvelope` approved. ``context_hash`` is typed
        :class:`ContextHash`, so a caller cannot substitute a string it
        assembled itself: doing so would silently take on the declassification
        decision the envelope exists to make.
        """
        if not isinstance(envelope, ContextEnvelope):
            raise ValidationError("a decision request requires a ContextEnvelope")
        return cls(
            kind=kind,
            context_hash=envelope.context_hash,
            candidates=candidates,
            correlation_id=correlation_id,
            excluded_context_fields=envelope.excluded_field_names,
            over_cap_context_fields=envelope.over_cap_field_names,
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
    "validate_provider_name",
]
