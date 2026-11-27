"""The governed tool gateway: one path for every agent tool invocation.

An agent never calls a function. It presents a token, names a tool from
:data:`~notes_service.domain.agent.tools.TOOL_CATALOG`, and supplies arguments.
This use case is the only thing that turns that into an effect, and it applies
the same sequence to every tool, in this order:

1. **Resolve the actor.** The token is resolved through
   :class:`~notes_service.application.use_cases.agents.ResolveAgentToken`, which
   re-checks token, delegation, and agent on *every* call, so revocation is
   immediate here too.
2. **Resolve the tool.** An unknown name raises. There is no dynamic
   registration and no name-to-callable lookup, so an unlisted tool cannot be
   reached at all.
3. **Validate the arguments** against the tool's declared schema, rejecting
   undeclared keys.
4. **Authorize.** The tool's capability must be inside the token's capability
   set. A missing capability is a denial, and it is *audited* as a denial.
5. **Gate sensitive tools on human approval.** A tool whose capability is
   sensitive does not execute on the agent's say-so; it needs an approved
   request, decided by a human other than the agent.
6. **Execute, then record the outcome** — success or failure — on the audit row
   written before step 6 began.

The audit row is written *before* execution, in its own committed transaction,
so a crash inside a handler leaves a visible ``started`` invocation rather than
no trace at all.

Two limits are stated rather than implied:

* Arguments are passed to the handlers as validated, but a handler that touches a
  note still goes through the note use cases, so per-resource authorization is
  enforced there as well. A delegated ``read_note`` lets an agent *attempt* a
  read; it does not grant a read of a note the delegator cannot see.
* Idempotency is recorded and enforced (a repeated key with the same arguments
  replays the recorded outcome instead of re-executing; a repeated key with
  different arguments is refused). It is *not* yet a durable resume: an
  invocation left ``started`` by a crash is refused rather than retried, because
  the gateway cannot know whether the side effect landed. NEX-006 makes that
  case resumable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, NoReturn
from uuid import UUID

from notes_service.application.errors import (
    AccessDenied,
    ApplicationError,
    ApprovalRequired,
    Conflict,
    InvalidInput,
)
from notes_service.application.ports.authorization import AuthorizationProvider
from notes_service.application.ports.clock import Clock
from notes_service.application.ports.policy_versions import PolicyVersionRepository
from notes_service.application.ports.repositories import UnitOfWork
from notes_service.application.use_cases.agents import ResolveAgentToken, ResolvedToken
from notes_service.application.use_cases.base import UseCase
from notes_service.application.use_cases.notes import (
    CreateNote,
    DeleteNote,
    GetNote,
    ListNotes,
    UpdateNote,
)
from notes_service.domain.agent.entities import ApprovalRequest
from notes_service.domain.agent.tools import (
    _MAX_IDEMPOTENCY_KEY,
    InvocationStatus,
    ToolDefinition,
    ToolInvocation,
    resolve_tool,
    validate_arguments,
)
from notes_service.domain.agent.value_objects import ApprovalStatus, TokenAudience
from notes_service.domain.errors import DomainError
from notes_service.domain.governance.policy import PolicyEffect, PolicyObligation, evaluate
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email
from notes_service.domain.notes.value_objects import NoteId


@dataclass(frozen=True, slots=True)
class ToolResult:
    """The outcome of one invocation, as returned to the caller.

    ``replayed`` is true when this response came from an earlier invocation
    under the same idempotency key rather than from executing the tool again, so
    a caller can tell the two apart.
    """

    invocation: ToolInvocation
    output: dict[str, object] = field(default_factory=dict)
    replayed: bool = False


def _actor_principal(resolved: ResolvedToken) -> Principal:
    """The principal a tool handler acts as: the human who delegated.

    The agent is an instrument of its delegator, not a separate owner. Handlers
    therefore see the delegator's identity, and every per-resource check inside
    them applies to the delegator's own access — an agent can never reach a note
    its delegator could not.
    """
    return Principal(
        user_id=resolved.delegation.delegator_user_id,
        email=Email(f"{resolved.delegation.delegator_user_id}@delegated.invalid"),
        display_name="Delegated agent",
        provider="agent-delegation",
        subject=str(resolved.agent.id),
        claims={"agent_id": str(resolved.agent.id), "token_id": str(resolved.token.id)},
    )


class InvokeTool(UseCase):
    """Invoke one governed tool on behalf of an agent token.

    The note use cases are constructed lazily from this use case's own unit of
    work factory, so a handler and the audit record that describes it can share
    one transaction where that matters.
    """

    def __init__(
        self,
        *,
        uow_factory: Callable[[], UnitOfWork],
        authorization: AuthorizationProvider,
        clock: Clock,
        resolve_token: ResolveAgentToken,
        policy_versions: PolicyVersionRepository | None = None,
    ) -> None:
        super().__init__(uow_factory=uow_factory, authorization=authorization, clock=clock)
        self._resolve_token = resolve_token
        self._policy_versions = policy_versions

    def run(
        self,
        *,
        secret: str,
        tool_name: str,
        arguments: dict[str, object] | None = None,
        idempotency_key: str,
    ) -> ToolResult:
        resolved = self._resolve_token.run(secret=secret, audience=TokenAudience.TOOL_GATEWAY.value)
        tool = resolve_tool(tool_name)
        cleaned = validate_arguments(tool, arguments)

        resource_ref = _resource_ref(tool, cleaned)

        if tool.capability not in resolved.token.capabilities:
            # Denied before any handler is reachable. Recorded, because a denied
            # attempt is exactly what an audit trail exists to show. The prior
            # lookup runs first so a retried denial replays the recorded refusal
            # instead of colliding with the (agent_id, idempotency_key) constraint.
            prior = self._find_prior(
                resolved=resolved,
                tool=tool,
                cleaned=cleaned,
                idempotency_key=idempotency_key,
                resource_ref=resource_ref,
            )
            if prior is not None:
                return prior
            with self._uow_factory() as uow:
                invocation = self._begin_invocation(
                    uow,
                    resolved=resolved,
                    tool=tool,
                    cleaned=cleaned,
                    resource_ref=resource_ref,
                    idempotency_key=idempotency_key,
                )
                invocation.deny(outcome=f"token lacks capability {tool.capability.value}")
                uow.tool_invocations.add(invocation)
                uow.commit()
            raise AccessDenied(f"agent token lacks capability {tool.capability.value}")

        consulted, obligations = self._enforce_published_policy(
            resolved=resolved,
            tool=tool,
            cleaned=cleaned,
            resource_ref=resource_ref,
            idempotency_key=idempotency_key,
        )

        principal = _actor_principal(resolved)

        # An allow that carries an obligation is not permission to proceed: the
        # obligation must be met first (security architecture section 5). Only
        # ``approval`` has a real enforcement adapter behind it in this gateway —
        # the human approval flow — and even that only for a capability the domain
        # already treats as sensitive: ``ApprovalRequest.open`` refuses to gate a
        # non-sensitive capability, so an approval obligation cannot be invented by
        # policy for a tool the platform does not consider human-gated. Whether a
        # capability needs a human is a static decision in code, not something a
        # policy version can widen.
        #
        # Every other obligation is refused here rather than ignored: an obligation
        # with no enforcement adapter that was silently dropped would be worse than
        # no policy at all, because the policy would read as enforced.
        satisfiable = tool.requires_approval and obligations == (PolicyObligation.APPROVAL,)
        if obligations and not satisfiable:
            names = ", ".join(obligation.value for obligation in obligations)
            reason = (
                f"allowed only under obligation(s) {names} that this gateway cannot satisfy "
                f"for capability {tool.capability.value}"
            )
            self._record_policy_denial(
                resolved=resolved,
                tool=tool,
                cleaned=cleaned,
                resource_ref=resource_ref,
                idempotency_key=idempotency_key,
                outcome=reason,
                policy_versions=consulted,
            )
            raise AccessDenied(
                f"tool {tool.name!r} is {reason}; refusing rather than proceeding "
                "with an unmet obligation"
            )

        if tool.requires_approval or PolicyObligation.APPROVAL in obligations:
            return self._run_gated(
                resolved=resolved,
                tool=tool,
                cleaned=cleaned,
                principal=principal,
                resource_ref=resource_ref,
                idempotency_key=idempotency_key,
                policy_versions=consulted,
            )
        return self._run_direct(
            resolved=resolved,
            tool=tool,
            cleaned=cleaned,
            principal=principal,
            resource_ref=resource_ref,
            idempotency_key=idempotency_key,
            policy_versions=consulted,
        )

    # -- published policy enforcement ------------------------------------

    def _enforce_published_policy(
        self,
        *,
        resolved: ResolvedToken,
        tool: ToolDefinition,
        cleaned: dict[str, object],
        resource_ref: str,
        idempotency_key: str,
    ) -> tuple[tuple[str, ...], tuple[PolicyObligation, ...]]:
        """Refuse the call when a published policy explicitly denies it (NEX-051).

        Until now the policy lifecycle could store and publish versions but
        nothing consulted them: ``evaluate`` had no production caller, so a
        published policy was documentation rather than an enforced control. This
        is that caller. It runs after the capability check (a token without the
        capability is refused before policy is even considered) and before any
        handler is reachable, so a policy denial is a real gate, not a log line.

        **Deny-only, and only on a matched rule.** ``evaluate`` returns ``DENY``
        for an empty policy set — the correct default for a bare evaluator, but
        fatal if used naively here: it would deny every tool call in a deployment
        that has published no policy at all. So a denial requires a decision whose
        effect is ``DENY`` *and* whose ``matched_rules`` is non-empty. No policy,
        or policies that match nothing, leave the gateway's existing controls in
        charge. Policies can therefore only subtract authority from what the
        capability and approval checks already allow, never grant it — an allow
        rule does not bypass a capability check that already failed.

        **Fails closed.** A defect in a policy (a condition compared against an
        attribute of the wrong type, say) raises during evaluation; that is
        treated as a denial rather than a pass, because "the policy could not be
        evaluated" must never mean "the action was permitted".

        The refusal is audited exactly like a capability denial — a ``denied``
        invocation row is written before raising — so a policy-blocked attempt is
        as visible in the trail as a capability-blocked one.

        Returns the versions that were in force and the obligations that the
        matching allow rules impose, so the attempt the caller is about to seal
        can cite them (NEX-052) and so the obligations are enforced rather than
        merely reported (NEX-053). Both are empty only when no policy repository
        is wired at all.
        """
        if self._policy_versions is None:
            return (), ()

        published = self._policy_versions.all_published()
        consulted = tuple(version.ref for version in published)
        attributes = _policy_attributes(resolved=resolved, tool=tool, resource_ref=resource_ref)
        try:
            decision = evaluate(published, attributes)
        except DomainError as exc:
            self._record_policy_denial(
                resolved=resolved,
                tool=tool,
                cleaned=cleaned,
                resource_ref=resource_ref,
                idempotency_key=idempotency_key,
                outcome=f"policy evaluation failed: {type(exc).__name__}",
                policy_versions=consulted,
            )
            raise AccessDenied(
                f"tool {tool.name!r} refused: published policy could not be evaluated"
            ) from exc

        if decision.effect is PolicyEffect.DENY and decision.matched_rules:
            self._record_policy_denial(
                resolved=resolved,
                tool=tool,
                cleaned=cleaned,
                resource_ref=resource_ref,
                idempotency_key=idempotency_key,
                outcome=f"denied by policy {', '.join(decision.matched_rules)}",
                policy_versions=consulted,
            )
            raise AccessDenied(
                f"tool {tool.name!r} refused by published policy "
                f"({', '.join(decision.matched_rules)})"
            )
        return consulted, decision.obligations

    def _record_policy_denial(
        self,
        *,
        resolved: ResolvedToken,
        tool: ToolDefinition,
        cleaned: dict[str, object],
        resource_ref: str,
        idempotency_key: str,
        outcome: str,
        policy_versions: tuple[str, ...] = (),
    ) -> None:
        """Persist a ``denied`` invocation row for a policy refusal.

        A retried denial replays the recorded refusal rather than colliding with
        the ``(agent_id, idempotency_key)`` constraint, matching the capability
        denial path.
        """
        prior = self._find_prior(
            resolved=resolved,
            tool=tool,
            cleaned=cleaned,
            idempotency_key=idempotency_key,
            resource_ref=resource_ref,
        )
        if prior is not None:
            return
        with self._uow_factory() as uow:
            invocation = self._begin_invocation(
                uow,
                resolved=resolved,
                tool=tool,
                cleaned=cleaned,
                resource_ref=resource_ref,
                idempotency_key=idempotency_key,
                policy_versions=policy_versions,
            )
            invocation.deny(outcome=outcome)
            uow.tool_invocations.add(invocation)
            uow.commit()

    # -- direct tools ----------------------------------------------------

    def _run_direct(
        self,
        *,
        resolved: ResolvedToken,
        tool: ToolDefinition,
        cleaned: dict[str, object],
        principal: Principal,
        resource_ref: str,
        idempotency_key: str,
        policy_versions: tuple[str, ...] = (),
    ) -> ToolResult:
        prior = self._find_prior(
            resolved=resolved,
            tool=tool,
            cleaned=cleaned,
            idempotency_key=idempotency_key,
            resource_ref=resource_ref,
        )
        if prior is not None:
            return prior

        # Commit the "started" row first: a crash in the handler must not erase
        # all evidence that the attempt happened.
        with self._uow_factory() as uow:
            invocation = self._begin_invocation(
                uow,
                resolved=resolved,
                tool=tool,
                cleaned=cleaned,
                resource_ref=resource_ref,
                idempotency_key=idempotency_key,
                policy_versions=policy_versions,
            )
            uow.tool_invocations.add(invocation)
            uow.commit()

        try:
            output = self._execute(tool=tool, arguments=cleaned, principal=principal)
        except ApplicationError as exc:
            self._finalise(invocation, failed=True, outcome=exc.code)
            raise
        except DomainError as exc:
            self._finalise(invocation, failed=True, outcome=type(exc).__name__)
            raise InvalidInput(str(exc)) from exc

        self._finalise(invocation, failed=False, outcome="ok")
        return ToolResult(invocation=invocation, output=output)

    # -- approval-gated tools --------------------------------------------

    def _run_gated(
        self,
        *,
        resolved: ResolvedToken,
        tool: ToolDefinition,
        cleaned: dict[str, object],
        principal: Principal,
        resource_ref: str,
        idempotency_key: str,
        policy_versions: tuple[str, ...] = (),
    ) -> ToolResult:
        prior = self._find_prior(
            resolved=resolved,
            tool=tool,
            cleaned=cleaned,
            idempotency_key=idempotency_key,
            resource_ref=resource_ref,
        )
        if prior is not None:
            return prior

        approval = self._approved_request(resolved=resolved, tool=tool, resource_ref=resource_ref)
        if approval is None:
            self._defer_awaiting_approval(
                resolved=resolved,
                tool=tool,
                cleaned=cleaned,
                resource_ref=resource_ref,
                idempotency_key=idempotency_key,
                policy_versions=policy_versions,
            )

        # Spend the approval before the side effect, in the same transaction as
        # the audit row. If the conditional UPDATE finds the request already
        # consumed or expired, this attempt is treated as unapproved rather than
        # executing on a stale decision.
        with self._uow_factory() as uow:
            if not uow.approvals.consume(approval.id, now=self._clock.now()):
                invocation = self._begin_invocation(
                    uow,
                    resolved=resolved,
                    tool=tool,
                    cleaned=cleaned,
                    resource_ref=resource_ref,
                    idempotency_key=idempotency_key,
                    approval_request_id=approval.id,
                    policy_versions=policy_versions,
                )
                invocation.deny(outcome="approval request no longer usable")
                uow.tool_invocations.add(invocation)
                uow.commit()
                raise AccessDenied(
                    f"approval request {approval.id} is spent or expired; a fresh "
                    "human approval is required"
                )
            invocation = self._begin_invocation(
                uow,
                resolved=resolved,
                tool=tool,
                cleaned=cleaned,
                resource_ref=resource_ref,
                idempotency_key=idempotency_key,
                approval_request_id=approval.id,
                policy_versions=policy_versions,
            )
            uow.tool_invocations.add(invocation)
            uow.commit()

        try:
            output = self._execute(tool=tool, arguments=cleaned, principal=principal)
        except ApplicationError as exc:
            self._finalise(invocation, failed=True, outcome=exc.code)
            raise
        except DomainError as exc:
            self._finalise(invocation, failed=True, outcome=type(exc).__name__)
            raise InvalidInput(str(exc)) from exc

        self._finalise(invocation, failed=False, outcome="ok")
        return ToolResult(invocation=invocation, output=output)

    def _defer_awaiting_approval(
        self,
        *,
        resolved: ResolvedToken,
        tool: ToolDefinition,
        cleaned: dict[str, object],
        resource_ref: str,
        idempotency_key: str,
        policy_versions: tuple[str, ...] = (),
    ) -> NoReturn:
        """Open (or reuse) an approval request and park this attempt, never failing it.

        The denial is audited under a *deferred* key, not the caller's operational
        one. ``(agent_id, idempotency_key)`` is unique, so recording the refusal
        under the real key would permanently occupy it: the retry after a human
        approves would find the denial, replay it, and the approval could never be
        honoured. The deferred key keeps the audit trail complete while leaving the
        operational key free for the attempt that actually runs.
        """
        deferred_key = _deferred_idempotency_key(idempotency_key)
        with self._uow_factory() as uow:
            now = self._clock.now()
            request = _open_or_reuse_approval_request(
                uow, resolved=resolved, tool=tool, resource_ref=resource_ref, now=now
            )
            already_recorded = uow.tool_invocations.find_by_idempotency_key(
                resolved.agent.id, deferred_key
            )
            if already_recorded is None:
                invocation = self._begin_invocation(
                    uow,
                    resolved=resolved,
                    tool=tool,
                    cleaned=cleaned,
                    resource_ref=resource_ref,
                    idempotency_key=deferred_key,
                    approval_request_id=request.id,
                    policy_versions=policy_versions,
                )
                invocation.deny(outcome=f"human approval required for {tool.capability.value}")
                uow.tool_invocations.add(invocation)
            uow.commit()
        raise ApprovalRequired(
            f"tool {tool.name} requires human approval; request {request.id} is awaiting a decision"
        )

    def _approved_request(
        self, *, resolved: ResolvedToken, tool: ToolDefinition, resource_ref: str
    ) -> ApprovalRequest | None:
        """An approved, unspent, unexpired approval for this (agent, capability, resource)."""
        now = self._clock.now()
        with self._uow_factory() as uow:
            for request in uow.approvals.list_for_agent(resolved.agent.id):
                if (
                    request.capability is tool.capability
                    and request.resource_ref == resource_ref
                    and request.is_usable(now=now)
                ):
                    return request
        return None

    # -- shared helpers --------------------------------------------------

    def _find_prior(
        self,
        *,
        resolved: ResolvedToken,
        tool: ToolDefinition,
        cleaned: dict[str, object],
        idempotency_key: str,
        resource_ref: str,
    ) -> ToolResult | None:
        """Replay a recorded invocation under the same key, or refuse key reuse.

        A key reused with *different* arguments is not a retry; it is a different
        operation borrowing a key, and executing it would silently break the
        caller's idempotency guarantee. Refusing is the only safe answer.
        """
        with self._uow_factory() as uow:
            existing = uow.tool_invocations.find_by_idempotency_key(
                resolved.agent.id, idempotency_key
            )
        if existing is None:
            return None

        from notes_service.domain.agent.tools import arguments_digest

        if existing.arguments_digest != arguments_digest(tool, cleaned):
            raise Conflict("idempotency key was already used for a different tool invocation")
        if existing.status is InvocationStatus.STARTED:
            raise Conflict(
                "an invocation under this idempotency key did not finish; refusing to "
                "re-execute because the gateway cannot confirm whether its effect landed"
            )
        if existing.status is InvocationStatus.SUCCEEDED:
            return ToolResult(invocation=existing, output={}, replayed=True)
        # A previously failed or denied invocation is replayed as-is: re-running
        # it would turn a recorded denial into an execution on a mere retry.
        raise AccessDenied(
            f"invocation under this idempotency key previously ended as {existing.status.value}"
        )

    def _begin_invocation(
        self,
        uow: UnitOfWork,
        *,
        resolved: ResolvedToken,
        tool: ToolDefinition,
        cleaned: dict[str, object],
        resource_ref: str,
        idempotency_key: str,
        approval_request_id: UUID | None = None,
        policy_versions: tuple[str, ...] = (),
    ) -> ToolInvocation:
        """Begin an audited invocation, sealed into the agent's hash chain.

        The chain link is read from ``uow`` — the same transaction that will
        insert the entry — so the sequence number and predecessor hash are
        consistent with what is committed. A concurrent insert that claims the
        same sequence is rejected by the unique constraint rather than silently
        forking the chain.

        ``policy_versions`` are the published policy versions in force when the
        gateway decided this call. They are sealed into the intent hash, so the
        citation is as tamper-evident as the rest of the attempt.
        """
        sequence, previous = uow.tool_invocations.next_chain_link(resolved.agent.id)
        invocation = ToolInvocation.begin(
            agent_id=resolved.agent.id,
            token_id=resolved.token.id,
            tool=tool,
            idempotency_key=idempotency_key,
            arguments=cleaned,
            resource_ref=resource_ref,
            approval_request_id=approval_request_id,
            previous_hash=previous,
            chain_sequence=sequence,
            policy_versions=policy_versions,
        )
        # Move the anchor in the same transaction that will insert the entry, so
        # the two cannot disagree. Doing it here rather than at each call site
        # means no insert path can forget it.
        uow.tool_invocations.advance_chain_anchor(resolved.agent.id, invocation)
        return invocation

    def _finalise(self, invocation: ToolInvocation, *, failed: bool, outcome: str | None) -> None:
        if not invocation.is_terminal:
            if failed:
                invocation.fail(outcome=outcome)
            else:
                invocation.succeed(outcome=outcome)
        with self._uow_factory() as uow:
            uow.tool_invocations.save(invocation)
            uow.commit()

    def _execute(
        self, *, tool: ToolDefinition, arguments: dict[str, object], principal: Principal
    ) -> dict[str, object]:
        """Dispatch to the handler for one catalogued tool.

        This is a closed dispatch over the catalog, not a dynamic lookup: adding a
        tool means adding a branch here, which is the review gate that keeps the
        tool surface finite.
        """
        name = tool.name
        if name == "search_notes":
            page = self._list_notes().run(
                principal,
                query=str(arguments.get("query") or "") or None,
                limit=int(str(arguments.get("limit") or 20)),
            )
            return {
                "total": page.total,
                "notes": [_note_summary(note) for note in page.items],
            }
        if name == "list_shared_notes":
            page = self._list_notes().run(principal, view="shared")
            return {
                "total": page.total,
                "notes": [_note_summary(note) for note in page.items],
            }
        if name == "read_note":
            note = self._get_note().run(principal, note_id=NoteId.parse(str(arguments["note_id"])))
            return {"note": _note_detail(note)}
        if name == "create_note":
            note = self._create_note().run(
                principal,
                title=str(arguments["title"]),
                content=str(arguments["content"]),
            )
            return {"note": _note_detail(note)}
        if name == "append_to_note":
            note_id = NoteId.parse(str(arguments["note_id"]))
            current = self._get_note().run(principal, note_id=note_id)
            appended = f"{current.content}{arguments['content']}"
            note = self._update_note().run(
                principal,
                note_id=note_id,
                title=str(current.title),
                content=appended,
                expected_version=current.version,
            )
            return {"note": _note_detail(note)}
        if name == "delete_note":
            self._delete_note().run(principal, note_id=NoteId.parse(str(arguments["note_id"])))
            return {"deleted": True}
        # Unreachable while the catalog and this dispatch agree; if they ever
        # diverge, fail closed rather than silently doing nothing.
        raise InvalidInput(f"no handler is registered for tool {name!r}")

    # -- handler factories ----------------------------------------------

    def _list_notes(self) -> ListNotes:
        return ListNotes(
            uow_factory=self._uow_factory,
            authorization=self._authorization,
            clock=self._clock,
        )

    def _get_note(self) -> GetNote:
        return GetNote(
            uow_factory=self._uow_factory,
            authorization=self._authorization,
            clock=self._clock,
        )

    def _create_note(self) -> CreateNote:
        return CreateNote(
            uow_factory=self._uow_factory,
            authorization=self._authorization,
            clock=self._clock,
        )

    def _update_note(self) -> UpdateNote:
        return UpdateNote(
            uow_factory=self._uow_factory,
            authorization=self._authorization,
            clock=self._clock,
        )

    def _delete_note(self) -> DeleteNote:
        return DeleteNote(
            uow_factory=self._uow_factory,
            authorization=self._authorization,
            clock=self._clock,
        )


def _deferred_idempotency_key(idempotency_key: str) -> str:
    """A sibling key for the audit row of a deferred attempt.

    Readable for short keys, and digests the operational key when the combined
    form would exceed the column limit, so a long caller-supplied key cannot turn
    a pause into a validation error.
    """
    suffix = "#awaiting-approval"
    if len(idempotency_key) + len(suffix) <= _MAX_IDEMPOTENCY_KEY:
        return f"{idempotency_key}{suffix}"
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"deferred:{digest}"[: _MAX_IDEMPOTENCY_KEY - len(suffix)] + suffix


def _open_or_reuse_approval_request(
    uow: UnitOfWork,
    *,
    resolved: ResolvedToken,
    tool: ToolDefinition,
    resource_ref: str,
    now: datetime,
) -> ApprovalRequest:
    """Reuse this agent's open request for the same action, or open a new one.

    Advancing a paused run repeatedly must not pile up duplicate requests for one
    decision: the human revisits the queue and would face the same approval many
    times over. Only a still-decidable request for the identical
    (capability, resource) pair is reused; anything else opens afresh.

    ``now`` is the gateway's clock and is used for *both* the reuse test and the
    new request's validity bound, so a request is judged against the same clock
    that stamped it.
    """
    for existing in uow.approvals.list_for_agent(resolved.agent.id):
        if (
            existing.status is ApprovalStatus.PENDING
            and existing.capability is tool.capability
            and existing.resource_ref == resource_ref
            and not existing.expired_at(now=now)
        ):
            return existing
    request = ApprovalRequest.open(
        agent=resolved.agent,
        capability=tool.capability,
        resource_ref=resource_ref,
        now=now,
    )
    uow.approvals.add(request)
    return request


def _policy_attributes(
    *, resolved: ResolvedToken, tool: ToolDefinition, resource_ref: str
) -> dict[str, object]:
    """The attribute snapshot a published policy is evaluated against.

    Deliberately small and non-secret: the capability being exercised, the tool
    name, the resource reference (``note:<id>`` or ``tool:<name>`` — never note
    content), and the agent's owner. A policy cannot reference an attribute that
    is not here, and a missing attribute never matches (the evaluator's rule), so
    an unknown attribute fails toward *not* matching a deny rather than granting.
    The vocabulary is what the enforcement path can attest to; widening it is a
    deliberate change, not an incidental one.
    """
    return {
        "capability": tool.capability.value,
        "tool": tool.name,
        "resource": resource_ref,
        "agent_owner": str(resolved.agent.owner_user_id),
    }


def _resource_ref(tool: ToolDefinition, arguments: dict[str, object]) -> str:
    """A stable, non-secret reference to the resource a tool acts on.

    Used to match an approval request to the action it authorises and to make the
    audit trail readable, without copying note content into it.
    """
    note_id = arguments.get("note_id")
    if isinstance(note_id, str) and note_id:
        return f"note:{note_id}"
    return f"tool:{tool.name}"


def _note_summary(note: Any) -> dict[str, object]:
    return {
        "id": str(note.id),
        "title": str(note.title),
        "version": int(note.version),
        "updated_at": note.updated_at.isoformat(),
    }


def _note_detail(note: Any) -> dict[str, object]:
    return {
        "id": str(note.id),
        "title": str(note.title),
        "content": str(note.content),
        "version": int(note.version),
        "updated_at": note.updated_at.isoformat(),
    }


__all__ = ["InvokeTool", "ToolResult"]
