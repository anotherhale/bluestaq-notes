"""An advisory opinion about a pending approval, and the reason it stays advice.

:class:`~notes_service.application.use_cases.decisioning.EvaluateDecision` had no
production caller. A judgment path exercised only by its own tests is a claim,
not a control: nothing had ever shown that a real envelope built from real
records fits the classification bounds, or that the answer reaches a human
without becoming the decision.

This use case is that first caller. A human who owns an agent can ask the
configured provider for a second opinion about one pending approval request
before deciding it themselves.

Three properties are structural, and each is the reason a particular piece of
code is shaped the way it is:

* **The advisory cannot approve anything.** :class:`ApprovalAdvisory` carries a
  score, a confidence and an abstention reason. It has no field for a decision,
  no route to :meth:`ApprovalRequest.approve`, and the aggregate is never saved
  here. A test asserts the request is still ``PENDING`` after the call, because
  "we did not mean to" is not a control.
* **The subject's content never reaches the provider.** A note title is
  classified ``CONFIDENTIAL``, so it sits above the default ``INTERNAL``
  ceiling: its value is absent from the canonical form and therefore from the
  digest the provider sees. It cannot influence the answer or be confirmed from
  it. Its *name* is recorded in ``excluded_context_fields`` so an audit reader
  can tell "we asked with less than we held" from "there was nothing more".
* **No opinion means no opinion.** With decisioning disabled, or the provider
  erroring, or the confidence below the threshold, the result abstains and the
  human decides alone. That is the fail-closed outcome: the platform does not
  substitute a guess for a decision it is not allowed to make.

The provider is asked a *score* question, never a yes/no. A binary "approve"
verdict would be an affordance a caller could read as consent; a number in
``[0.0, 1.0]`` cannot be mistaken for one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from notes_service.application.errors import ResourceNotFound
from notes_service.application.ports.authorization import AuthorizationProvider
from notes_service.application.ports.clock import Clock
from notes_service.application.use_cases.agents import load_owned_agent
from notes_service.application.use_cases.base import UnitOfWorkFactory, UseCase
from notes_service.domain.agent.context import (
    ContextEnvelope,
    ContextField,
    DataClassification,
)
from notes_service.domain.agent.decisioning import (
    AbstentionReason,
    DecisionKind,
    DecisionRequest,
)
from notes_service.domain.agent.entities import ApprovalRequest
from notes_service.domain.agent.value_objects import ApprovalRequestId
from notes_service.domain.identity.principal import Principal
from notes_service.domain.notes.value_objects import NoteId

if TYPE_CHECKING:
    from notes_service.application.ports.repositories import UnitOfWork
    from notes_service.application.use_cases.decisioning import (
        DecisionEvaluation,
        EvaluateDecision,
    )


@dataclass(frozen=True, slots=True)
class ApprovalAdvisory:
    """A provider's second opinion about one approval request.

    Deliberately not an approval. ``score`` is an advisory number in ``[0.0,
    1.0]`` where higher means "this looks more routine"; ``None`` means the
    provider formed no opinion. Nothing here can decide the request — the human
    still has to, and :class:`ApprovalRequest` remains the only thing that can
    record that.
    """

    approval_id: str
    score: float | None
    confidence: float
    abstention_reason: AbstentionReason | None
    provider_name: str
    context_hash: str
    excluded_context_fields: tuple[str, ...]
    latency_ms: float

    @property
    def is_advisory_available(self) -> bool:
        """Whether a usable number was produced at all."""
        return self.score is not None


class RecommendApprovalDecision(UseCase):
    """Ask the configured provider for a second opinion on a pending approval.

    Ownership is checked exactly as on every other approval path: a non-owner
    gets 404, so an approval id cannot probe for another user's agent.
    """

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        authorization: AuthorizationProvider,
        clock: Clock,
        evaluate_decision: EvaluateDecision,
    ) -> None:
        super().__init__(uow_factory=uow_factory, authorization=authorization, clock=clock)
        self._evaluate_decision = evaluate_decision

    def run(self, principal: Principal, *, approval_id: str) -> ApprovalAdvisory:
        with self._uow_factory() as uow:
            request = uow.approvals.get(ApprovalRequestId.parse(approval_id))
            if request is None:
                raise ResourceNotFound("approval request not found")
            # Same 404-not-403 rule as Get/Decide/Revoke: the agent's existence
            # is private, so an approval id must not reveal another user's agent.
            load_owned_agent(uow, principal=principal, agent_id=str(request.agent_id))

            envelope = self._build_envelope(uow, principal=principal, request=request)
            # Nothing is written: reading an advisory must not change the record
            # it is about, so the request is never saved and never mutated.
            uow.commit()

        decision_request = DecisionRequest.from_context(
            DecisionKind.APPROVAL_RECOMMENDATION,
            envelope,
            correlation_id=str(request.id),
        )
        evaluation = self._evaluate_decision.run(decision_request)
        return _advisory_from(evaluation, approval_id=str(request.id))

    def _build_envelope(
        self, uow: UnitOfWork, *, principal: Principal, request: ApprovalRequest
    ) -> ContextEnvelope:
        """Classify the facts a reviewer would use, before any of them travels.

        Only references and derived facts enter by default. The one piece of
        genuine subject content available here — a note's title — is labelled
        ``CONFIDENTIAL`` and so falls above the default ``INTERNAL`` ceiling: it
        is present in the envelope, withheld from the digest, and named in
        ``excluded_context_fields``.
        """
        now = self._clock.now()
        age_seconds = max(0, int((now - request.requested_at).total_seconds()))

        fields = [
            # PUBLIC: the capability is a fixed vocabulary item, not user data.
            ContextField(
                name="capability",
                classification=DataClassification.PUBLIC,
                value=request.capability.value,
            ),
            ContextField(
                name="resource_kind",
                classification=DataClassification.PUBLIC,
                value=request.resource_ref.split(":", 1)[0],
            ),
            # INTERNAL: how long a human has had to consider it.
            ContextField(
                name="request_age_seconds",
                classification=DataClassification.INTERNAL,
                value=str(age_seconds),
            ),
        ]

        title = self._note_title(uow, principal=principal, request=request)
        if title is not None:
            fields.append(
                ContextField(
                    name="note_title",
                    classification=DataClassification.CONFIDENTIAL,
                    value=title,
                )
            )

        return ContextEnvelope.from_fields(fields)

    def _note_title(
        self, uow: UnitOfWork, *, principal: Principal, request: ApprovalRequest
    ) -> str | None:
        """The target note's title, if this request names a note the caller can read.

        Returns ``None`` rather than raising when the note is missing or
        unreadable: an advisory about an approval is not the place to decide
        whether the underlying action is legal. The tool invocation itself will
        refuse an unreachable note when it runs.
        """
        if not request.resource_ref.startswith("note:"):
            return None
        raw_id = request.resource_ref.split(":", 1)[1]
        try:
            note_id = NoteId.parse(raw_id)
        except Exception:
            return None
        note = uow.notes.get(note_id)
        if note is None:
            return None
        if note.owner_id != principal.user_id:
            # Not an oracle: a collaborator's title is the subject's content and
            # the caller is not its owner, so it is simply not offered.
            return None
        return str(note.title)


def _advisory_from(evaluation: DecisionEvaluation, *, approval_id: str) -> ApprovalAdvisory:
    """Flatten an evaluation into the advisory a reviewer sees."""
    return ApprovalAdvisory(
        approval_id=approval_id,
        score=evaluation.score,
        confidence=evaluation.decision.confidence,
        abstention_reason=evaluation.abstention_reason,
        provider_name=evaluation.provider_name,
        context_hash=evaluation.context_hash,
        excluded_context_fields=evaluation.excluded_context_fields,
        latency_ms=evaluation.latency_ms,
    )


__all__ = ["ApprovalAdvisory", "RecommendApprovalDecision"]
