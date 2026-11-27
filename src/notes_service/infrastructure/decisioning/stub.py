"""Offline decision provider: deterministic, dependency-free, honest.

This adapter exists so the platform can run, be demonstrated, and be tested with
no vendor, no network, and no credentials. It is not a mock: it applies a real,
documented rule and abstains whenever that rule cannot produce a defensible
answer.

The rule is deliberately simple and visible:

* a **choice** question picks the lexicographically first candidate, at low
  confidence — an arbitrary but *stable* tie-break, not a claim of merit, so any
  sensible confidence threshold turns it into an abstention;
* a **score** question answers ``0.5``, the midpoint, at the same low
  confidence, because "no information" is exactly what it has.

Every answer therefore carries low confidence. With any caller threshold above
it, the honest result is an abstention and deterministic behaviour resumes. That
is the intended default: a stub should never quietly look like a real judgment.
"""

from __future__ import annotations

from notes_service.domain.agent.decisioning import (
    Decision,
    DecisionOutcome,
    DecisionRequest,
)

# Below any threshold a caller would sensibly set for real use, so the stub's
# arbitrary answer is abstained on rather than acted upon.
_STUB_CONFIDENCE = 0.1


class StubDecisionProvider:
    """Deterministic provider for tests, offline demos, and the disabled path."""

    def __init__(self, *, confidence: float = _STUB_CONFIDENCE) -> None:
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0.0 and 1.0")
        self._confidence = confidence

    @property
    def name(self) -> str:
        return "stub"

    def decide(self, request: DecisionRequest) -> Decision:
        if request.kind.is_choice:
            # ``min`` rather than "first": the answer must not depend on input
            # ordering, so the same set of candidates always yields the same
            # choice.
            return Decision(
                kind=request.kind,
                outcome=DecisionOutcome.DECIDED,
                confidence=self._confidence,
                choice=min(request.candidates),
                rationale="stub provider: stable lexical tie-break",
            )

        # The midpoint carries no lean. This matters most for
        # ``approval_recommendation``: an advisory score must never be readable
        # as consent, so the stub does not lean towards approving.
        return Decision(
            kind=request.kind,
            outcome=DecisionOutcome.DECIDED,
            confidence=self._confidence,
            score=0.5,
            rationale="stub provider: no signal",
        )
