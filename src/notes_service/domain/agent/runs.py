"""Durable agent runs: a plan of governed tool steps that survives a crash.

The tool gateway already refuses to re-execute an invocation it left ``started``,
because on its own it cannot tell whether the side effect landed. That refusal is
correct but it is not *recovery*: the work is stuck. This module supplies the
missing half — a durable run that records, step by step, how far a multi-step
agent task got, so an interrupted run can be picked up again.

Three rules are load-bearing and are enforced here rather than in a use case:

* **A completed step is never re-executed.** :meth:`AgentRun.begin_step` refuses
  a step that already reached a terminal state, and :meth:`AgentRun.next_step`
  only ever returns a ``pending`` step. Resume therefore cannot double-apply a
  side effect that already happened.
* **Steps run in order.** A step may not begin while an earlier step is
  unfinished. Without this, "resume" could reorder work and produce a state that
  never existed in a single uninterrupted run.
* **An ambiguous step is escalated, not guessed.** A step left ``started`` by a
  crash becomes ``interrupted`` on recovery and blocks the run until an operator
  says whether its effect landed. Silently retrying would risk a double effect;
  silently skipping would risk losing one. Neither guess belongs in code.

Each step carries a deterministic idempotency key derived from the run and the
step's position, so a resumed run presents the *same* key the tool gateway
already recorded. Run-level resume and gateway-level idempotency therefore agree
about what "already done" means instead of maintaining two opinions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from notes_service.domain.agent.tools import resolve_tool
from notes_service.domain.agent.value_objects import Capability
from notes_service.domain.errors import InvariantViolation, ValidationError

_MAX_OUTCOME = 500
_MAX_RESOURCE_REF = 512


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AgentRunId(uuid.UUID):
    """Identifier for one durable agent run."""

    __slots__ = ()

    @classmethod
    def new(cls) -> AgentRunId:
        return cls(str(uuid.uuid4()))

    @classmethod
    def parse(cls, value: str | uuid.UUID) -> AgentRunId:
        if isinstance(value, AgentRunId):
            return value
        try:
            return cls(str(uuid.UUID(str(value))))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError(f"invalid agent run id: {value!r}") from exc


class RunStepId(uuid.UUID):
    """Identifier for one step of a durable run."""

    __slots__ = ()

    @classmethod
    def new(cls) -> RunStepId:
        return cls(str(uuid.uuid4()))

    @classmethod
    def parse(cls, value: str | uuid.UUID) -> RunStepId:
        if isinstance(value, RunStepId):
            return value
        try:
            return cls(str(uuid.UUID(str(value))))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError(f"invalid run step id: {value!r}") from exc


class RunStatus(StrEnum):
    """Lifecycle of a whole run.

    ``INTERRUPTED`` is distinct from ``FAILED`` on purpose: a failure is a
    decision the run made, whereas an interruption is the absence of one. Only an
    interrupted run can be resumed, and only after its ambiguous step is
    resolved.
    """

    PENDING = "pending"
    RUNNING = "running"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    ABORTED = "aborted"

    @property
    def is_terminal(self) -> bool:
        return self in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.ABORTED}


class StepStatus(StrEnum):
    """Lifecycle of one step.

    ``STARTED`` means the handler was entered and its outcome is unknown. It is
    the only status that a crash can leave behind, and it is never treated as
    success.
    """

    PENDING = "pending"
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    INTERRUPTED = "interrupted"

    @property
    def is_terminal(self) -> bool:
        return self in {StepStatus.SUCCEEDED, StepStatus.FAILED}

    @property
    def is_settled(self) -> bool:
        """Whether the step's outcome is known well enough to move past it."""
        return self in {StepStatus.SUCCEEDED, StepStatus.FAILED}


def step_idempotency_key(run_id: AgentRunId, sequence: int) -> str:
    """The key a step presents to the tool gateway, derived from its position.

    Deterministic on purpose: a resumed run must present the key the gateway
    already recorded for this step, or the gateway would treat the retry as a new
    operation and could execute it twice.
    """
    if sequence < 1:
        raise ValidationError("step sequence starts at 1")
    return f"run:{run_id}:step:{sequence}"


@dataclass(frozen=True, slots=True)
class PlannedStep:
    """A step to be scheduled when a run is created. Immutable input.

    ``capability`` is derived from the tool catalog, not supplied by the caller. A
    plan that could declare its own capability could record a weaker authority
    than the step it schedules — and the run row is exactly what an operator reads
    when deciding whether an interrupted step's effect landed.
    """

    tool_name: str
    arguments: dict[str, object]
    capability: Capability = field(init=False)
    resource_ref: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.tool_name, str) or not self.tool_name.strip():
            raise ValidationError("a planned step must name a tool")
        if len(self.resource_ref) > _MAX_RESOURCE_REF:
            raise ValidationError(f"resource ref must be at most {_MAX_RESOURCE_REF} characters")
        tool = resolve_tool(self.tool_name)
        object.__setattr__(self, "capability", tool.capability)


@dataclass(slots=True)
class RunStep:
    """One durable step: its plan, its inputs, and how far it got.

    ``arguments`` is persisted, unlike the gateway's audit row which stores only
    a digest. Durability requires the inputs — a resumed run cannot re-derive
    what a step was asked to do. The run row is owner-scoped working state, not a
    broadly readable audit trail, which is why the two store different things.
    """

    id: RunStepId
    sequence: int
    tool_name: str
    capability: Capability
    idempotency_key: str
    arguments_digest: str
    arguments: dict[str, object]
    resource_ref: str = ""
    status: StepStatus = StepStatus.PENDING
    outcome: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    def _require(self, *allowed: StepStatus, action: str) -> None:
        if self.status not in allowed:
            raise InvariantViolation(
                f"cannot {action} step {self.sequence}: it is {self.status.value}"
            )

    def _start(self, now: datetime | None) -> None:
        self._require(StepStatus.PENDING, action="start")
        self.status = StepStatus.STARTED
        self.started_at = now or _utcnow()

    def _settle(self, status: StepStatus, *, outcome: str | None, now: datetime | None) -> None:
        if status not in {StepStatus.SUCCEEDED, StepStatus.FAILED}:
            raise ValidationError("a step can only settle as succeeded or failed")
        self.status = status
        self.completed_at = now or _utcnow()
        if outcome is not None:
            self.outcome = outcome[:_MAX_OUTCOME]

    def succeed(self, *, outcome: str | None = None, now: datetime | None = None) -> None:
        self._require(StepStatus.STARTED, action="complete")
        self._settle(StepStatus.SUCCEEDED, outcome=outcome, now=now)

    def fail(self, *, outcome: str | None = None, now: datetime | None = None) -> None:
        self._require(StepStatus.STARTED, action="fail")
        self._settle(StepStatus.FAILED, outcome=outcome, now=now)

    def interrupt(self, *, now: datetime | None = None) -> None:
        """Record that a crash left this step's outcome unknown.

        Idempotent: recovering a run twice must not fail the second time.
        """
        if self.status is StepStatus.INTERRUPTED:
            return
        self._require(StepStatus.STARTED, action="interrupt")
        self.status = StepStatus.INTERRUPTED
        self.completed_at = None
        self.outcome = None
        if now is not None:
            self.started_at = self.started_at or now

    def defer(self) -> None:
        """Return a step that never ran to ``pending``.

        Only reachable when the gateway refused *before* entering a handler — a
        pending human approval is the case this exists for. ``started`` means "the
        handler was entered", so a step that was merely deferred must not be left
        there: it would masquerade as an interrupted step and require an owner to
        rule on an effect that never happened.
        """
        self._require(StepStatus.STARTED, action="defer")
        self.status = StepStatus.PENDING
        self.started_at = None

    def abandon(self, *, outcome: str | None = None, now: datetime | None = None) -> None:
        """Settle a step left mid-flight because its run was abandoned.

        ``STARTED`` and ``INTERRUPTED`` are the two states in which a step's
        effect is unknown. Aborting the run does not make it known, so the step is
        recorded as not having produced a confirmed effect — the outcome text says
        exactly that — rather than being left mid-flight beside a terminal run. A
        step that already settled is untouched.
        """
        if self.status is StepStatus.STARTED:
            self._settle(StepStatus.FAILED, outcome=outcome, now=now)
        elif self.status is StepStatus.INTERRUPTED:
            self.resolve_interrupted(effect_landed=False, outcome=outcome, now=now)

    def resolve_interrupted(
        self,
        *,
        effect_landed: bool,
        outcome: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Settle an interrupted step from an explicit operator decision.

        This is the only way out of ``interrupted``. The caller states whether the
        side effect landed; the domain does not infer it, because guessing is
        exactly what produces a duplicate or a lost effect.
        """
        self._require(StepStatus.INTERRUPTED, action="resolve")
        self._settle(
            StepStatus.SUCCEEDED if effect_landed else StepStatus.FAILED,
            outcome=outcome or ("effect confirmed after interruption" if effect_landed else None),
            now=now,
        )


@dataclass(slots=True)
class AgentRun:
    """A durable, ordered plan of governed tool steps for one agent.

    The aggregate is the unit of recovery: loading it from persistence gives you
    the exact point at which work stopped, and the methods below are the only way
    to move it forward.
    """

    id: AgentRunId
    agent_id: uuid.UUID
    delegation_id: uuid.UUID | None
    steps: list[RunStep]
    status: RunStatus = RunStatus.PENDING
    version: int = 1
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    last_error: str | None = None
    # The version this aggregate was last read from or written to storage at.
    # ``version`` alone cannot serve that role: several mutations can happen
    # between two saves, so ``version - 1`` is not the stored version. Persistence
    # needs the exact stored value to express a compare-and-set.
    persisted_version: int = field(default=1, init=False, repr=False)

    def __post_init__(self) -> None:
        self.persisted_version = self.version

    def mark_persisted(self) -> None:
        """Record that ``version`` is now what storage holds.

        Called by the repository after a successful write, so the next write's
        compare-and-set targets the row it actually left behind.
        """
        self.persisted_version = self.version

    @classmethod
    def plan(
        cls,
        *,
        agent_id: uuid.UUID,
        planned: list[PlannedStep],
        delegation_id: uuid.UUID | None = None,
        run_id: AgentRunId | None = None,
    ) -> AgentRun:
        if not planned:
            raise ValidationError("a run must have at least one step")
        resolved = run_id or AgentRunId.new()
        steps = [
            RunStep(
                id=RunStepId.new(),
                sequence=index,
                tool_name=step.tool_name.strip(),
                capability=step.capability,
                idempotency_key=step_idempotency_key(resolved, index),
                arguments_digest=_digest(step),
                arguments=dict(step.arguments),
                resource_ref=step.resource_ref,
            )
            for index, step in enumerate(planned, start=1)
        ]
        return cls(id=resolved, agent_id=agent_id, delegation_id=delegation_id, steps=steps)

    def _bump(self, now: datetime | None) -> None:
        self.version += 1
        self.updated_at = now or _utcnow()

    def _step(self, step_id: RunStepId) -> RunStep:
        for step in self.steps:
            if step.id == step_id:
                return step
        raise ValidationError(f"step {step_id} does not belong to run {self.id}")

    @property
    def ordered_steps(self) -> list[RunStep]:
        return sorted(self.steps, key=lambda step: step.sequence)

    @property
    def completed_steps(self) -> list[RunStep]:
        return [s for s in self.ordered_steps if s.status is StepStatus.SUCCEEDED]

    @property
    def interrupted_step(self) -> RunStep | None:
        for step in self.ordered_steps:
            if step.status is StepStatus.INTERRUPTED:
                return step
        return None

    @property
    def next_step(self) -> RunStep | None:
        """The step that may run now, or ``None`` if the run cannot advance.

        Returns ``None`` when an earlier step is unfinished, so a caller can never
        skip past an interrupted or failed step by accident.
        """
        for step in self.ordered_steps:
            if step.status is StepStatus.SUCCEEDED:
                continue
            return step if step.status is StepStatus.PENDING else None
        return None

    @property
    def is_complete(self) -> bool:
        return all(step.status is StepStatus.SUCCEEDED for step in self.steps)

    def resume(self, *, now: datetime | None = None) -> None:
        """Reopen a pending or interrupted run for further execution.

        Refuses a completed, failed, or aborted run: reopening a finished run
        would let a resume re-execute steps whose effects are already recorded as
        final.
        """
        if self.status.is_terminal:
            raise InvariantViolation(f"run {self.id} is {self.status.value} and cannot resume")
        if self.status is RunStatus.RUNNING:
            return
        self.status = RunStatus.RUNNING
        self.started_at = self.started_at or (now or _utcnow())
        self._bump(now)

    def begin_step(self, step_id: RunStepId, *, now: datetime | None = None) -> RunStep:
        """Move one step to ``started``, enforcing order and non-repetition."""
        if self.status is not RunStatus.RUNNING:
            raise InvariantViolation(
                f"run {self.id} is {self.status.value}; call resume() before starting a step"
            )
        step = self._step(step_id)
        for earlier in self.ordered_steps:
            if earlier.sequence >= step.sequence:
                break
            if earlier.status is not StepStatus.SUCCEEDED:
                raise InvariantViolation(
                    f"step {step.sequence} cannot start before step {earlier.sequence} settles"
                )
        step._start(now)
        self._bump(now)
        return step

    def complete_step(
        self, step_id: RunStepId, *, outcome: str | None = None, now: datetime | None = None
    ) -> None:
        self._step(step_id).succeed(outcome=outcome, now=now)
        self._after_settle(now)

    def fail_step(
        self, step_id: RunStepId, *, outcome: str | None = None, now: datetime | None = None
    ) -> None:
        self._step(step_id).fail(outcome=outcome, now=now)
        self._after_settle(now)

    def defer_step(self, step_id: RunStepId, *, now: datetime | None = None) -> None:
        """Park a step that is waiting on a human, leaving the run unfinished.

        A failed step is a decision, and this is the absence of one: the step never
        ran, so nothing has been decided and nothing can have landed. Marking it
        failed would make the run terminal and strand the approval a human may yet
        grant, so the run stays open with the step back at ``pending``.
        """
        if self.status.is_terminal:
            raise InvariantViolation(
                f"run {self.id} is {self.status.value} and cannot defer a step"
            )
        self._step(step_id).defer()
        # Nothing is in flight and nothing failed, so the run is not "running".
        # PENDING is the honest available state: work remains and a resume can
        # pick it up. The reason travels to the caller as the advance's halt text
        # and to the approver as the open approval request.
        self.status = RunStatus.PENDING
        self._bump(now)

    def _after_settle(self, now: datetime | None) -> None:
        if self.is_complete:
            self.status = RunStatus.COMPLETED
            self.finished_at = now or _utcnow()
        elif any(step.status is StepStatus.FAILED for step in self.steps):
            self.status = RunStatus.FAILED
            self.finished_at = now or _utcnow()
            self.last_error = next(
                (
                    s.outcome
                    for s in self.ordered_steps
                    if s.status is StepStatus.FAILED and s.outcome
                ),
                self.last_error,
            )
        self._bump(now)

    def mark_interrupted(self, *, now: datetime | None = None) -> bool:
        """Recover after a crash: any in-flight step becomes ``interrupted``.

        Returns whether anything changed, so a caller can log a genuine recovery
        rather than a no-op. Idempotent, because recovery may itself be retried.
        """
        changed = False
        for step in self.ordered_steps:
            if step.status is StepStatus.STARTED:
                step.interrupt(now=now)
                changed = True
        if changed:
            self.status = RunStatus.INTERRUPTED
            self._bump(now)
        return changed

    def resolve_interrupted(
        self,
        *,
        effect_landed: bool,
        outcome: str | None = None,
        now: datetime | None = None,
    ) -> None:
        step = self.interrupted_step
        if step is None:
            raise InvariantViolation(f"run {self.id} has no interrupted step to resolve")
        step.resolve_interrupted(effect_landed=effect_landed, outcome=outcome, now=now)
        self._after_settle(now)

    def abort(self, *, reason: str | None = None, now: datetime | None = None) -> None:
        if self.status.is_terminal:
            raise InvariantViolation(f"run {self.id} is already {self.status.value}")
        # Aborting is a decision, so no step may be left mid-flight beside a
        # terminal run: a step still ``started``/``interrupted`` would keep
        # claiming its effect is unknown forever, and ``started`` is exactly what
        # a live call looks like. Each such step is settled as unconfirmed.
        for step in self.ordered_steps:
            if not step.is_terminal and step.status is not StepStatus.PENDING:
                step.abandon(outcome="run aborted before this step's effect was confirmed", now=now)
        self.status = RunStatus.ABORTED
        self.finished_at = now or _utcnow()
        if reason:
            self.last_error = reason[:_MAX_OUTCOME]
        self._bump(now)


def _digest(step: PlannedStep) -> str:
    from notes_service.domain.agent.tools import arguments_digest, resolve_tool

    return arguments_digest(resolve_tool(step.tool_name), step.arguments)


__all__ = [
    "AgentRun",
    "AgentRunId",
    "PlannedStep",
    "RunStatus",
    "RunStep",
    "RunStepId",
    "StepStatus",
    "step_idempotency_key",
]
