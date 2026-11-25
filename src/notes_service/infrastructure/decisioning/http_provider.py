"""The first real decision provider: a bounded HTTP judgment service.

:class:`~notes_service.infrastructure.decisioning.stub.StubDecisionProvider`
exists so the platform runs with no vendor. This adapter is what a vendor
integration looks like, and it is deliberately the narrowest shape that can work.

Four decisions carry the security argument.

**The vendor cannot abstain; it can only be unsure.** The wire contract has no
``abstained`` outcome. A provider answers with a confidence, and the *caller's*
threshold in :class:`~notes_service.application.use_cases.decisioning.EvaluateDecision`
turns a low one into an abstention. This keeps abstention policy in the
application where it is auditable, and it removes a class of confusion: a vendor
must not be able to claim ``DISABLED`` or ``PROVIDER_ERROR``, because those are
*our* operational states describing *our* relationship to the vendor, not the
vendor's opinion. A vendor that has no opinion says so with confidence ``0.0``.

**The request carries no context, only a question.** The wire body is ``kind``
plus the sorted ``candidates``. It does not carry the context hash, the excluded
field names, or the correlation id. The adapter never receives note text in the
first place — that is the port's design — but the field *names* and the internal
correlation id are still platform facts a third party has no need for, and
"names of sensitive categories" is itself a disclosure. Minimisation here is a
choice, not a constraint: a later vendor that needs to correlate its own logs
should be given a freshly minted opaque per-request id rather than our internal
one.

**Sorting the candidates is what makes order-independence structural.** The
conformance suite requires that reordering the same candidate set does not change
the answer. Hoping a vendor is order-insensitive would make that a property of
the vendor. Sorting before the call makes it a property of *this adapter*: the
same set produces a byte-identical request body, so a deterministic vendor must
produce the same answer.

**A bad answer is an abstention, never an exception.** The port says a provider
must not raise for a business condition, and this adapter holds to that for every
failure it can name: transport error, non-200, oversized or non-JSON body, a
choice the caller never offered, a confidence that is not a finite number in
``[0.0, 1.0]``. The use case still guards the call, because an adapter is
third-party code and can fail in a way it did not anticipate.

Two honest limits, recorded rather than papered over:

* The size bound is checked *after* httpx has read the body, so it bounds what is
  parsed, not what is held in memory. A streaming read is later hardening. The
  threat model here is a buggy or compromised *configured* vendor, not an
  attacker who chooses the URL, which is why this is acceptable for now.
* The rationale is truncated rather than fatal. A verbose explanation is not a
  wrong answer, and discarding a legal answer because its explanation ran long
  would let vendor verbosity deny a decision. Truncation is marked so a reader of
  the audit trail can see the text was cut.
"""

from __future__ import annotations

import math
import re
from typing import Any

import httpx

from notes_service.domain.agent.decisioning import (
    MAX_RATIONALE_LENGTH,
    AbstentionReason,
    Decision,
    DecisionOutcome,
    DecisionRequest,
    validate_provider_name,
)
from notes_service.domain.errors import ValidationError

_DEFAULT_TIMEOUT_SECONDS = 5.0

#: Refuse to parse a body larger than this. The vendor's answer is four short
#: fields; anything near this bound is a broken or hostile peer, not a verbose
#: one. See the module docstring for why this does not bound memory.
_MAX_RESPONSE_BYTES = 64 * 1024

_TRUNCATION_MARKER = "...[truncated]"

#: Control characters become spaces. A rationale is displayed and logged, so a
#: newline in it is a log-injection vector rather than a formatting nicety - the
#: same argument that makes the provider *name* an identifier.
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def _bounded_confidence(payload: dict[str, Any]) -> float | None:
    """Read ``confidence`` as a finite number in ``[0.0, 1.0]``, or ``None``.

    ``bool`` is rejected explicitly and before the numeric check: ``True`` is an
    ``int`` in Python, so a vendor sending ``"confidence": true`` would otherwise
    be read as ``1.0`` - a maximum-confidence answer assembled from a flag.
    """
    value = payload.get("confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    confidence = float(value)
    if not math.isfinite(confidence) or not 0.0 <= confidence <= 1.0:
        return None
    return confidence


def _bounded_score(payload: dict[str, Any]) -> float | None:
    """Read ``score`` under the same rules as :func:`_bounded_confidence`."""
    value = payload.get("score")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    if not math.isfinite(score) or not 0.0 <= score <= 1.0:
        return None
    return score


def _bounded_rationale(payload: dict[str, Any]) -> str:
    """Read ``rationale`` as single-line text no longer than the contract cap.

    A missing or non-string rationale becomes the empty string: the rationale is
    explanatory, and its absence must not turn a legal answer into an abstention.
    """
    value = payload.get("rationale")
    if not isinstance(value, str):
        return ""
    text = _CONTROL_CHARS.sub(" ", value).strip()
    if len(text) <= MAX_RATIONALE_LENGTH:
        return text
    keep = MAX_RATIONALE_LENGTH - len(_TRUNCATION_MARKER)
    return text[:keep].rstrip() + _TRUNCATION_MARKER


def _request_body(request: DecisionRequest) -> dict[str, Any]:
    """The minimal question. Nothing about the platform's context leaves here."""
    body: dict[str, Any] = {"kind": request.kind.value}
    if request.kind.is_choice:
        # Sorted, not merely copied: see the module docstring.
        body["candidates"] = sorted(request.candidates)
    return body


def _decision_from_payload(request: DecisionRequest, payload: dict[str, Any]) -> Decision:
    """Turn a vendor payload into a contract-legal :class:`Decision`.

    Unknown extra keys are ignored rather than rejected. That is safe because
    every field this function reads is named explicitly, so an extra key has no
    path into the result; being lenient about extras also lets a vendor add a
    field without breaking the integration.
    """
    confidence = _bounded_confidence(payload)
    if confidence is None:
        return Decision.abstain(request.kind, AbstentionReason.MALFORMED_OUTPUT)

    rationale = _bounded_rationale(payload)

    if request.kind.is_choice:
        choice = payload.get("choice")
        if not isinstance(choice, str) or choice not in request.candidates:
            return Decision.abstain(request.kind, AbstentionReason.MALFORMED_OUTPUT)
        return Decision(
            kind=request.kind,
            outcome=DecisionOutcome.DECIDED,
            confidence=confidence,
            choice=choice,
            rationale=rationale,
        )

    score = _bounded_score(payload)
    if score is None:
        return Decision.abstain(request.kind, AbstentionReason.MALFORMED_OUTPUT)
    return Decision(
        kind=request.kind,
        outcome=DecisionOutcome.DECIDED,
        confidence=confidence,
        score=score,
        rationale=rationale,
    )


class HttpDecisionProvider:
    """A :class:`~notes_service.application.ports.decisioning.DecisionProvider` over HTTPS."""

    def __init__(
        self,
        *,
        name: str,
        endpoint: str,
        api_key: str | None = None,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        http_client: httpx.Client | None = None,
    ) -> None:
        # Validated at construction, not at request time. The use case degrades an
        # unusable name to ``unidentified``, which is the right *runtime* answer but
        # would hide a configuration typo behind a healthy-looking process. A name
        # that cannot be recorded is a startup defect.
        try:
            self._name = validate_provider_name(name)
        except ValidationError as exc:
            raise ValueError(f"invalid decision provider name: {exc}") from exc

        # Plaintext judgment over the internet is not a defensible default, and a
        # scheme check at startup is the only place it can be caught cheaply.
        if not endpoint.lower().startswith("https://"):
            raise ValueError("decision provider endpoint must use https")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("decision provider timeout_seconds must be a positive number")

        self._endpoint = endpoint
        self._api_key = api_key
        self._http = http_client or httpx.Client(timeout=timeout_seconds)

    @property
    def name(self) -> str:
        return self._name

    def decide(self, request: DecisionRequest) -> Decision:
        try:
            response = self._http.post(
                self._endpoint, json=_request_body(request), headers=self._headers()
            )
        except httpx.HTTPError:
            # Unreachable, timed out, or the connection failed mid-flight. Broad by
            # necessity: httpx raises a family of errors and none of them may escape.
            return Decision.abstain(request.kind, AbstentionReason.PROVIDER_ERROR)

        if response.status_code != 200:
            # The vendor's error body is deliberately discarded. It can echo the
            # request, an internal id, or (in a badly written vendor) the credential,
            # and none of that may reach our audit trail through a rationale.
            return Decision.abstain(request.kind, AbstentionReason.PROVIDER_ERROR)

        if len(response.content) > _MAX_RESPONSE_BYTES:
            return Decision.abstain(request.kind, AbstentionReason.MALFORMED_OUTPUT)

        try:
            payload = response.json()
        except ValueError:
            return Decision.abstain(request.kind, AbstentionReason.MALFORMED_OUTPUT)
        if not isinstance(payload, dict):
            return Decision.abstain(request.kind, AbstentionReason.MALFORMED_OUTPUT)

        try:
            return _decision_from_payload(request, payload)
        except ValidationError:
            # Belt and braces: the helpers above already bound every field, but a
            # ``Decision`` refuses to exist out of contract, and that refusal must
            # not escape into a caller that has a deterministic fallback.
            return Decision.abstain(request.kind, AbstentionReason.MALFORMED_OUTPUT)

    def _headers(self) -> dict[str, str]:
        """Request headers. Never logged, never included in an error path."""
        headers = {"content-type": "application/json", "accept": "application/json"}
        if self._api_key is not None:
            headers["authorization"] = f"Bearer {self._api_key}"
        return headers


__all__ = ["HttpDecisionProvider"]
