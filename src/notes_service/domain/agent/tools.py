"""The governed tool surface: catalog, argument schemas, and invocation audit.

An agent does not call an arbitrary function. It asks the tool gateway to invoke
one *declared* tool with *validated* arguments, and the gateway decides. This
module holds the two things that decision depends on, kept pure so they can be
tested without a database or a web framework:

* :data:`TOOL_CATALOG` — the complete, finite set of tools, each mapped to the
  single :class:`~notes_service.domain.agent.value_objects.Capability` it
  requires and to an explicit argument schema. A name absent from this catalog
  is denied by construction; there is no dynamic registration and no
  string-to-callable lookup.
* :func:`validate_arguments` — a small, hand-written validator. It is
  deliberately *not* a general JSON Schema engine: a permissive validator is an
  authorization bypass wearing a validation hat, because the handler assumes
  its inputs are exactly what the schema promised. Only the shapes the catalog
  declares are accepted, and everything else raises.

:class:`ToolInvocation` is the audit record. Every attempt produces one —
including denied and failed attempts — so the trail answers "who tried what,
under which capability, and what happened" rather than only recording successes.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from notes_service.domain.agent.audit_chain import (
    GENESIS_HASH,
    DecisionEvidence,
    intent_hash,
    outcome_hash,
)
from notes_service.domain.agent.value_objects import SENSITIVE_CAPABILITIES, Capability
from notes_service.domain.errors import ValidationError


def _utcnow() -> datetime:
    return datetime.now(UTC)


class ToolInvocationId(uuid.UUID):
    """Identifier for one audited tool invocation."""

    __slots__ = ()

    @classmethod
    def new(cls) -> ToolInvocationId:
        return cls(str(uuid.uuid4()))

    @classmethod
    def parse(cls, value: str | uuid.UUID) -> ToolInvocationId:
        if isinstance(value, ToolInvocationId):
            return value
        try:
            return cls(str(uuid.UUID(str(value))))
        except (ValueError, AttributeError, TypeError) as exc:
            raise ValidationError(f"invalid tool invocation id: {value!r}") from exc


class ParameterType(StrEnum):
    """The only argument types a tool may declare."""

    STRING = "string"
    INTEGER = "integer"
    BOOLEAN = "boolean"


class InvocationStatus(StrEnum):
    """Terminal-or-pending outcome of one invocation.

    ``STARTED`` is written *before* the handler runs so that a crash mid-handler
    is visible as an unfinished invocation rather than vanishing. It is not a
    success: a retry that finds a ``STARTED`` row refuses to re-execute, because
    the gateway cannot know whether the side effect landed. NEX-006 (durable
    runtime) is what will make that case resumable; until then, refusing is the
    only honest answer.
    """

    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DENIED = "denied"

    @property
    def is_terminal(self) -> bool:
        return self is not InvocationStatus.STARTED


_MAX_DESCRIPTION = 400


@dataclass(frozen=True, slots=True)
class Parameter:
    """One declared argument of a tool."""

    name: str
    type: ParameterType
    required: bool = True
    max_length: int | None = None
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """A declared tool: its capability requirement and its argument schema."""

    name: str
    capability: Capability
    description: str
    parameters: tuple[Parameter, ...] = ()

    @property
    def requires_approval(self) -> bool:
        """Whether the capability behind this tool is human-gated."""
        return self.capability in SENSITIVE_CAPABILITIES

    def parameter(self, name: str) -> Parameter | None:
        for parameter in self.parameters:
            if parameter.name == name:
                return parameter
        return None


def _p(
    name: str,
    type_: ParameterType,
    *,
    required: bool = True,
    max_length: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> Parameter:
    return Parameter(
        name=name,
        type=type_,
        required=required,
        max_length=max_length,
        minimum=minimum,
        maximum=maximum,
    )


_NOTE_ID = _p("note_id", ParameterType.STRING, max_length=64)

#: The finite tool surface. Adding a tool is a deliberate act: it needs a
#: capability, an argument schema, and a handler in the application layer.
TOOL_CATALOG: dict[str, ToolDefinition] = {
    tool.name: tool
    for tool in (
        ToolDefinition(
            name="search_notes",
            capability=Capability.SEARCH_NOTES,
            description="Search notes the delegator can read.",
            parameters=(
                _p("query", ParameterType.STRING, required=False, max_length=200),
                _p("limit", ParameterType.INTEGER, required=False, minimum=1, maximum=100),
            ),
        ),
        ToolDefinition(
            name="read_note",
            capability=Capability.READ_NOTE,
            description="Read one note the delegator can read.",
            parameters=(_NOTE_ID,),
        ),
        ToolDefinition(
            name="create_note",
            capability=Capability.CREATE_NOTE,
            description="Create a note owned by the delegator.",
            parameters=(
                _p("title", ParameterType.STRING, max_length=200),
                _p("content", ParameterType.STRING, max_length=65536),
            ),
        ),
        ToolDefinition(
            name="append_to_note",
            capability=Capability.APPEND_NOTE,
            description="Append content to a note the delegator can write.",
            parameters=(_NOTE_ID, _p("content", ParameterType.STRING, max_length=65536)),
        ),
        ToolDefinition(
            name="list_shared_notes",
            capability=Capability.LIST_SHARED_NOTES,
            description="List notes shared with the delegator.",
        ),
        ToolDefinition(
            name="delete_note",
            capability=Capability.DELETE_NOTE,
            description="Delete a note the delegator owns. Requires human approval.",
            parameters=(_NOTE_ID,),
        ),
    )
}


def resolve_tool(name: str) -> ToolDefinition:
    """Look up a tool, or raise. An unknown name is never resolved to a callable."""
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("tool name must be a non-empty string")
    definition = TOOL_CATALOG.get(name.strip())
    if definition is None:
        raise ValidationError(f"unknown tool: {name!r}")
    return definition


def validate_arguments(tool: ToolDefinition, raw: dict[str, object] | None) -> dict[str, object]:
    """Validate ``raw`` against ``tool``'s declared schema, returning a clean copy.

    Rejects unknown keys rather than ignoring them: an argument the schema does
    not declare is either a caller bug or an attempt to smuggle input to a
    handler, and both should surface.
    """
    supplied = dict(raw or {})

    declared = {parameter.name for parameter in tool.parameters}
    unknown = set(supplied) - declared
    if unknown:
        raise ValidationError(
            f"tool {tool.name} received undeclared arguments: " + ", ".join(sorted(unknown))
        )

    cleaned: dict[str, object] = {}
    for parameter in tool.parameters:
        if parameter.name not in supplied:
            if parameter.required:
                raise ValidationError(f"tool {tool.name} requires argument {parameter.name!r}")
            continue
        cleaned[parameter.name] = _validate_value(tool.name, parameter, supplied[parameter.name])
    return cleaned


def _validate_value(tool_name: str, parameter: Parameter, value: object) -> object:
    if parameter.type is ParameterType.STRING:
        if not isinstance(value, str):
            raise ValidationError(f"argument {parameter.name!r} of {tool_name} must be a string")
        if parameter.max_length is not None and len(value) > parameter.max_length:
            raise ValidationError(
                f"argument {parameter.name!r} of {tool_name} must be at most "
                f"{parameter.max_length} characters"
            )
        return value

    if parameter.type is ParameterType.INTEGER:
        # ``bool`` is an ``int`` subclass in Python; accepting it would let
        # ``true`` mean ``1``. Reject it explicitly.
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(f"argument {parameter.name!r} of {tool_name} must be an integer")
        if parameter.minimum is not None and value < parameter.minimum:
            raise ValidationError(
                f"argument {parameter.name!r} of {tool_name} must be at least {parameter.minimum}"
            )
        if parameter.maximum is not None and value > parameter.maximum:
            raise ValidationError(
                f"argument {parameter.name!r} of {tool_name} must be at most {parameter.maximum}"
            )
        return value

    if not isinstance(value, bool):
        raise ValidationError(f"argument {parameter.name!r} of {tool_name} must be a boolean")
    return value


def arguments_digest(tool: ToolDefinition, arguments: dict[str, object]) -> str:
    """A stable digest of (tool, arguments) used to detect idempotency-key reuse.

    Two requests under one idempotency key are only the *same* request if this
    digest matches; otherwise the second is a different operation reusing a key,
    which is refused rather than silently executed.
    """
    canonical = json.dumps(
        {"tool": tool.name, "arguments": arguments},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_MAX_IDEMPOTENCY_KEY = 200
_MAX_SUMMARY = 500


@dataclass(slots=True)
class ToolInvocation:
    """One audited attempt to invoke a tool, successful or not.

    The record is written *before* the handler runs (``STARTED``) and finalised
    after. ``arguments_digest`` rather than the raw arguments is stored so the
    audit trail never becomes a second copy of note content.

    Each record is a link in a per-agent hash chain (see
    :mod:`notes_service.domain.agent.audit_chain`). ``chain_sequence`` is the
    record's 1-based position and ``previous_hash`` is the preceding record's
    ``intent_hash``, so deleting, reordering or editing an attempt is detectable
    after the fact. The chain is sealed at construction and at finalisation and
    is never recomputed from mutable state.

    ``policy_versions`` names the policy versions that were in force when the
    gateway decided this call (``policy@v3``): the security architecture's
    "matched policy versions" field. It is sealed into the intent hash, so the
    decision stays explainable after the policies move on and the cited versions
    cannot be quietly erased. Empty when no policy was consulted.

    ``decision_evidence`` is the extra proof a policy's ``enhanced-audit``
    obligation requires (NEX-055): which rules matched, which obligations were
    imposed, and the attribute snapshot the decision was made against. It is
    sealed into the intent hash exactly as ``policy_versions`` is, so the extra
    evidence cannot be added to, trimmed from, or rewritten after the fact
    without breaking the chain. ``None`` for an ordinary attempt.
    """

    id: ToolInvocationId
    agent_id: uuid.UUID
    token_id: uuid.UUID | None
    tool_name: str
    capability: Capability
    idempotency_key: str
    arguments_digest: str
    resource_ref: str
    policy_versions: tuple[str, ...] = ()
    decision_evidence: DecisionEvidence | None = None
    status: InvocationStatus = InvocationStatus.STARTED
    outcome: str | None = None
    approval_request_id: uuid.UUID | None = None
    invoked_at: datetime = field(default_factory=_utcnow)
    completed_at: datetime | None = None
    chain_sequence: int = 0
    previous_hash: str = GENESIS_HASH
    intent_hash: str = ""
    outcome_hash: str | None = None

    @classmethod
    def begin(
        cls,
        *,
        agent_id: uuid.UUID,
        token_id: uuid.UUID | None,
        tool: ToolDefinition,
        idempotency_key: str,
        arguments: dict[str, object],
        resource_ref: str,
        approval_request_id: uuid.UUID | None = None,
        invocation_id: ToolInvocationId | None = None,
        previous_hash: str = GENESIS_HASH,
        chain_sequence: int = 1,
        policy_versions: tuple[str, ...] = (),
        decision_evidence: DecisionEvidence | None = None,
    ) -> ToolInvocation:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ValidationError("an idempotency key is required")
        key = idempotency_key.strip()
        if len(key) > _MAX_IDEMPOTENCY_KEY:
            raise ValidationError(
                f"idempotency key must be at most {_MAX_IDEMPOTENCY_KEY} characters"
            )
        if chain_sequence < 1:
            raise ValidationError("chain sequence must be at least 1")
        resolved_id = invocation_id or ToolInvocationId.new()
        digest = arguments_digest(tool, arguments)
        now = _utcnow()
        consulted = tuple(policy_versions)
        return cls(
            id=resolved_id,
            agent_id=agent_id,
            token_id=token_id,
            tool_name=tool.name,
            capability=tool.capability,
            idempotency_key=key,
            arguments_digest=digest,
            resource_ref=resource_ref,
            policy_versions=consulted,
            decision_evidence=decision_evidence,
            approval_request_id=approval_request_id,
            invoked_at=now,
            chain_sequence=chain_sequence,
            previous_hash=previous_hash,
            intent_hash=intent_hash(
                invocation_id=resolved_id,
                agent_id=agent_id,
                token_id=token_id,
                tool_name=tool.name,
                capability=tool.capability.value,
                idempotency_key=key,
                arguments_digest=digest,
                resource_ref=resource_ref,
                approval_request_id=approval_request_id,
                invoked_at=now,
                previous_hash=previous_hash,
                policy_versions=consulted,
                decision_evidence=decision_evidence,
            ),
        )

    @property
    def is_terminal(self) -> bool:
        return self.status.is_terminal

    def succeed(self, *, outcome: str | None = None, now: datetime | None = None) -> None:
        self._finish(InvocationStatus.SUCCEEDED, outcome=outcome, now=now)

    def fail(self, *, outcome: str | None = None, now: datetime | None = None) -> None:
        self._finish(InvocationStatus.FAILED, outcome=outcome, now=now)

    def deny(self, *, outcome: str | None = None, now: datetime | None = None) -> None:
        self._finish(InvocationStatus.DENIED, outcome=outcome, now=now)

    def _finish(
        self, status: InvocationStatus, *, outcome: str | None, now: datetime | None
    ) -> None:
        if self.is_terminal:
            raise ValidationError(f"invocation {self.id} is already {self.status.value}")
        self.status = status
        self.completed_at = now or _utcnow()
        if outcome is not None:
            self.outcome = outcome[:_MAX_SUMMARY]
        self.outcome_hash = outcome_hash(
            intent_hash_value=self.intent_hash,
            status=self.status.value,
            outcome=self.outcome,
            completed_at=self.completed_at,
        )


__all__ = [
    "TOOL_CATALOG",
    "InvocationStatus",
    "Parameter",
    "ParameterType",
    "ToolDefinition",
    "ToolInvocation",
    "ToolInvocationId",
    "arguments_digest",
    "resolve_tool",
    "validate_arguments",
]
