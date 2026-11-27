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

Rule 2 covers the provider's *answer*, but the provider also supplies its own
*identity* through the port's ``name`` property, and that is third-party code
too. An adapter that builds its name from a config lookup or a health probe can
raise there, and a name is not an unbounded free-text channel: it is an
identifier recorded in telemetry and returned to API clients. So the name is
read once, defensively, and reduced to a bounded identifier before it reaches any
record. A provider whose name cannot be read or is not identifier-shaped is
recorded under :data:`UNIDENTIFIED_PROVIDER` rather than taking down a caller
that has a deterministic fallback — an unidentifiable provider is itself the
useful signal.

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
    validate_provider_name,
)
from notes_service.domain.errors import ValidationError

#: Any caller whose threshold is above the stub's 0.1 confidence abstains, which
#: is the safe default for a provider that is not actually judging anything.
DEFAULT_MIN_CONFIDENCE = 0.5

#: Recorded when a provider cannot name itself in an identifier-shaped way.
#: Deliberately a valid provider name, so a record never carries an empty or
#: unusable field; an operator alerting on this value is alerting on adapters
#: whose identity is broken, which is a real defect rather than noise.
UNIDENTIFIED_PROVIDER = "unidentified"


def _safe_provider_name(provider: DecisionProvider) -> str:
    """Read a provider's name without letting third-party code break the caller.

    ``name`` is a property on a protocol implemented by an adapter, so it is as
    much third-party code as ``decide`` is. An adapter that derives its name from
    a config lookup or a health probe can raise there, and that must not escape:
    the whole point of this use case is that a caller never has to wrap it to stay
    up. A name that is not an identifier is equally unusable, so both failures
    degrade to :data:`UNIDENTIFIED_PROVIDER`.
    """
    try:
        raw = provider.name
    except Exception:
        # Broad for the same reason as in ``run``: a vendor property can fail on
        # anything from a missing config key to a socket.
        return UNIDENTIFIED_PROVIDER
    try:
        return validate_provider_name(raw)
    except ValidationError:
        return UNIDENTIFIED_PROVIDER


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
        return _safe_provider_name(self._provider)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def run(self, request: DecisionRequest) -> DecisionEvaluation:
        # Read the name once, before anything else can fail, so every record this
        # run produces names the same provider. Reading it lazily inside
        # ``_evaluation`` would read it on the disabled path too, where the
        # provider was never called and its name may not be resolvable.
        provider_name = _safe_provider_name(self._provider)

        if not self._enabled:
            return self._evaluation(
                request,
                Decision.abstain(request.kind, AbstentionReason.DISABLED),
                latency_ms=0.0,
                provider_name=provider_name,
            )

        started = self._timer()
        try:
            # ``cast`` to ``object`` on purpose: the port promises a Decision,
            # but an adapter is third-party code and the guard below is the only
            # thing standing between a bad payload and a caller that trusts it.
            raw = cast(object, self._provider.decide(request))
        except ValidationError:
            # An adapter that constructs its ``Decision`` from a vendor payload
            # lets this module's own bounds reject an out-of-contract answer, and
            # the raise then escapes through ``decide``. That is a *malformed
            # payload*, not an unreachable vendor: the two are operationally
            # different, and reporting a contract violation as PROVIDER_ERROR
            # sends an operator to look at connectivity when the payload is the
            # problem. Caught before the broad clause below, which would
            # otherwise swallow it.
            return self._evaluation(
                request,
                Decision.abstain(request.kind, AbstentionReason.MALFORMED_OUTPUT),
                latency_ms=self._elapsed_ms(started),
                provider_name=provider_name,
            )
        except Exception:
            # Broad by necessity: the port contract says "do not raise for a
            # business condition", but a vendor client can still fail on a
            # socket, a decode, or a bug. None of those may take down a caller
            # that has a deterministic fallback.
            return self._evaluation(
                request,
                Decision.abstain(request.kind, AbstentionReason.PROVIDER_ERROR),
                latency_ms=self._elapsed_ms(started),
                provider_name=provider_name,
            )
        latency_ms = self._elapsed_ms(started)

        if not isinstance(raw, Decision):
            return self._evaluation(
                request,
                Decision.abstain(request.kind, AbstentionReason.MALFORMED_OUTPUT),
                latency_ms=latency_ms,
                provider_name=provider_name,
            )
        decision = raw

        try:
            validate_against(request, decision)
        except ValidationError:
            return self._evaluation(
                request,
                Decision.abstain(request.kind, AbstentionReason.MALFORMED_OUTPUT),
                latency_ms=latency_ms,
                provider_name=provider_name,
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
                provider_name=provider_name,
            )

        return self._evaluation(
            request, decision, latency_ms=latency_ms, provider_name=provider_name
        )

    def _evaluation(
        self,
        request: DecisionRequest,
        decision: Decision,
        *,
        latency_ms: float,
        provider_name: str,
    ) -> DecisionEvaluation:
        return DecisionEvaluation(
            request_kind=request.kind,
            context_hash=request.context_hash,
            correlation_id=request.correlation_id,
            provider_name=provider_name,
            decision=decision,
            latency_ms=latency_ms,
            threshold=self._min_confidence,
            excluded_context_fields=request.excluded_context_fields,
            over_cap_context_fields=request.over_cap_context_fields,
        )

    def _elapsed_ms(self, started: float) -> float:
        return round((self._timer() - started) * 1000, 2)
