"""EvaluateDecision: the one place a judgment becomes usable.

Every decision the platform acts on passes through here, so the same four rules
are applied to every provider:

1. **Disabled means abstain.** A feature flag that is off never reaches the
   provider. Turning judgment off must not change what the platform is *allowed*
   to do, only whether it has an opinion.
2. **A provider failure is an abstention, not an exception.** A raised error, a
   timeout, or a payload that is not a :class:`Decision` becomes
   ``PROVIDER_ERROR`` / ``MALFORMED_OUTPUT``. A third-party client can always
   fail in a way its adapter did not anticipate, and no caller should have to
   wrap this to stay up.
3. **A malformed answer is discarded.** :func:`validate_against` checks the
   answer against the question that produced it — same kind, and a choice drawn
   from the candidates actually offered. An answer that fails is downgraded to
   an abstention rather than propagated.
4. **Confidence below the caller's threshold is an abstention.** This is where
   "unsure" becomes "no opinion" so deterministic behaviour resumes.

The result carries telemetry: provider name, outcome, latency, confidence, and
the reason for any abstention. It carries the request's context hash rather than
the context itself, so a decision can be traced to *which* inputs produced it
without retaining the sensitive content.

What this use case cannot do is the point of the whole module: :class:`Decision`
has no field naming a capability, a principal, or a scope, and nothing here
imports the authorization provider. An evaluation is advice. Enforcement stays
with the deterministic authorization and approval paths.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from notes_service.application.ports.decisioning import DecisionProvider
from notes_service.domain.agent.decisioning import (
    AbstentionReason,
    Decision,
    DecisionKind,
    DecisionOutcome,
    DecisionRequest,
    validate_against,
)
from notes_service.domain.errors import ValidationError

#: Any caller whose threshold is above the stub's 0.1 confidence abstains, which
#: is the safe default for a provider that is not actually judging anything.
DEFAULT_MIN_CONFIDENCE = 0.5


@dataclass(frozen=True, slots=True)
class DecisionEvaluation:
    """A decision plus the evidence needed to explain and audit it."""

    request_kind: DecisionKind
    context_hash: str
    correlation_id: str | None
    provider_name: str
    decision: Decision
    latency_ms: float
    threshold: float
    #: Context field names the caller withheld before asking, carried through so
    #: an audit reader can tell "we asked with less than we had" from "there was
    #: nothing else". Names only; no value reaches this record.
    excluded_context_fields: tuple[str, ...] = ()
    #: The subset of ``excluded_context_fields`` withheld because the value did
    #: not fit the field cap. This is the one worth watching: an above-ceiling
    #: exclusion is policy working, an over-cap exclusion means the advisory was
    #: computed on less than the caller held.
    over_cap_context_fields: tuple[str, ...] = ()

    @property
    def is_decided(self) -> bool:
        """Whether the caller may act on an opinion at all."""
        return self.decision.outcome is DecisionOutcome.DECIDED

    @property
    def choice(self) -> str | None:
        """The selected candidate, or ``None`` when no opinion was formed."""
        return self.decision.choice if self.is_decided else None

    @property
    def score(self) -> float | None:
        """The bounded score, or ``None`` when no opinion was formed."""
        return self.decision.score if self.is_decided else None

    @property
    def abstention_reason(self) -> AbstentionReason | None:
        return None if self.is_decided else self.decision.reason


class EvaluateDecision:
    """Ask the configured provider one bounded question and police the answer."""

    def __init__(
        self,
        *,
        provider: DecisionProvider,
        enabled: bool = True,
        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
        timer: Callable[[], float] = time.perf_counter,
    ) -> None:
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0.0 and 1.0")
        self._provider = provider
        self._enabled = enabled
        self._min_confidence = min_confidence
        self._timer = timer

    @property
    def provider_name(self) -> str:
        return self._provider.name

    @property
    def enabled(self) -> bool:
        return self._enabled

    def run(self, request: DecisionRequest) -> DecisionEvaluation:
        if not self._enabled:
            return self._evaluation(
                request,
                Decision.abstain(request.kind, AbstentionReason.DISABLED),
                latency_ms=0.0,
            )

        started = self._timer()
        try:
            # ``cast`` to ``object`` on purpose: the port promises a Decision,
            # but an adapter is third-party code and the guard below is the only
            # thing standing between a bad payload and a caller that trusts it.
            raw = cast(object, self._provider.decide(request))
        except Exception:
            # Broad by necessity: the port contract says "do not raise for a
            # business condition", but a vendor client can still fail on a
            # socket, a decode, or a bug. None of those may take down a caller
            # that has a deterministic fallback.
            return self._evaluation(
                request,
                Decision.abstain(request.kind, AbstentionReason.PROVIDER_ERROR),
                latency_ms=self._elapsed_ms(started),
            )
        latency_ms = self._elapsed_ms(started)

        if not isinstance(raw, Decision):
            return self._evaluation(
                request,
                Decision.abstain(request.kind, AbstentionReason.MALFORMED_OUTPUT),
                latency_ms=latency_ms,
            )
        decision = raw

        try:
            validate_against(request, decision)
        except ValidationError:
            return self._evaluation(
                request,
                Decision.abstain(request.kind, AbstentionReason.MALFORMED_OUTPUT),
                latency_ms=latency_ms,
            )

        if (
            decision.outcome is DecisionOutcome.DECIDED
            and decision.confidence < self._min_confidence
        ):
            return self._evaluation(
                request,
                Decision.abstain(
                    request.kind,
                    AbstentionReason.LOW_CONFIDENCE,
                    confidence=decision.confidence,
                ),
                latency_ms=latency_ms,
            )

        return self._evaluation(request, decision, latency_ms=latency_ms)

    def _evaluation(
        self, request: DecisionRequest, decision: Decision, *, latency_ms: float
    ) -> DecisionEvaluation:
        return DecisionEvaluation(
            request_kind=request.kind,
            context_hash=request.context_hash,
            correlation_id=request.correlation_id,
            provider_name=self._provider.name,
            decision=decision,
            latency_ms=latency_ms,
            threshold=self._min_confidence,
            excluded_context_fields=request.excluded_context_fields,
            over_cap_context_fields=request.over_cap_context_fields,
        )

    def _elapsed_ms(self, started: float) -> float:
        return round((self._timer() - started) * 1000, 2)
