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
    RateLimited,
)
from notes_service.application.ports.authorization import AuthorizationProvider
from notes_service.application.ports.clock import Clock
from notes_service.application.ports.policy_versions import PolicyVersionRepository
from notes_service.application.ports.rate_limit import RateLimiter
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
from notes_service.domain.agent.audit_chain import DecisionEvidence
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
from notes_service.domain.identity.assurance import STEP_UP_FLOOR
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import Email
from notes_service.domain.notes.value_objects import NoteId
from notes_service.domain.redaction import redact


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


@dataclass(frozen=True, slots=True)
class _PolicyGate:
    """What the published-policy check established about one attempt.

    Bundles the two citations the attempt must carry (``consulted`` versions for
    NEX-052, ``evidence`` for NEX-055) with the obligations that still have to be
    enforced (NEX-053/054), so every path that seals a row passes the same three
    values rather than re-deriving them from a decision it no longer has.
    """

    consulted: tuple[str, ...] = ()
    obligations: tuple[PolicyObligation, ...] = ()
    evidence: DecisionEvidence | None = None

    @property
    def redaction_required(self) -> bool:
        return PolicyObligation.REDACTION in self.obligations

    @property
    def approval_required(self) -> bool:
        return PolicyObligation.APPROVAL in self.obligations

    @property
    def rate_limit_required(self) -> bool:
        return PolicyObligation.RATE_LIMIT in self.obligations

    @property
    def second_approver_required(self) -> bool:
        """Whether the published policy demands two distinct human approvers.

        Implies :attr:`approval_required`: a request that needs two signatures
        needs approval. A policy author who writes only ``second-approver``
        therefore gets a gate, not a requirement attached to nothing.
        """
        return PolicyObligation.SECOND_APPROVER in self.obligations


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
        # The agent has no assurance of its own; the handler acts as the human,
        # so it carries the assurance that human proved when granting (ADR-015).
        assurance=resolved.delegation.assurance,
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
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        super().__init__(uow_factory=uow_factory, authorization=authorization, clock=clock)
        self._resolve_token = resolve_token
        self._policy_versions = policy_versions
        self._rate_limiter = rate_limiter

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

        gate = self._enforce_published_policy(
            resolved=resolved,
            tool=tool,
            cleaned=cleaned,
            resource_ref=resource_ref,
            idempotency_key=idempotency_key,
        )
        consulted = gate.consulted
        obligations = gate.obligations

        principal = _actor_principal(resolved)

        # An allow that carries an obligation is not permission to proceed: the
        # obligation must be met first (security architecture section 5). Four kinds
        # have a real mechanism behind them in this gateway:
        #
        # * ``approval`` — the human approval flow, and only for a capability the
        #   domain already treats as sensitive: ``ApprovalRequest.open`` refuses to
        #   gate a non-sensitive capability, so policy cannot *invent* a human gate
        #   for a tool the platform does not consider human-gated. Whether a
        #   capability needs a human is a static decision in code, not something a
        #   policy version can widen.
        # * ``redaction`` — the response is filtered by the same shape-based
        #   judgement the log formatter uses (``domain.redaction``), so a
        #   credential-bearing field is withheld from the caller rather than
        #   returned. It is applied to the output, never to the stored note, and
        #   the audit row records that it happened.
        # * ``rate-limit`` — a volume bound, enforced against the configured
        #   :class:`RateLimiter` on a per-tool key (NEX-056). It is checked before
        #   the approval gate so a flood cannot open approval requests it will
        #   never use.
        # * ``step-up`` — a minimum *authentication* strength for the human behind
        #   the agent (NEX-058). Its adapter always exists: the assurance the
        #   delegator proved when granting, recorded on the delegation. It is
        #   satisfiable as a mechanism, but a given delegation may not *meet* the
        #   floor — that is a separate, explicit refusal below, so "the gateway
        #   cannot enforce step-up" is never confused with "this delegation was
        #   granted at a weaker assurance".
        # * ``second-approver`` — the same human-approval flow as ``approval``,
        #   with the threshold raised to two *distinct* humans (NEX-059). It
        #   implies ``approval``: a request needing two signatures needs one, so
        #   a policy that names only ``second-approver`` gets a gate. Like
        #   ``approval`` it is bound to a capability the domain already treats as
        #   sensitive — policy may tighten an existing human gate to two people,
        #   it may not invent a human gate for a tool the platform does not
        #   consider human-gated.
        #
        # Every other obligation is refused here rather than ignored: an obligation
        # with no enforcement adapter that was silently dropped would be worse than
        # no policy at all, because the policy would read as enforced. One
        # unenforceable member refuses the whole call — a set is only satisfiable
        # when every member is, so an author cannot smuggle an unenforceable
        # requirement alongside a real one.
        unsatisfiable = [
            obligation
            for obligation in obligations
            if not (
                (obligation is PolicyObligation.APPROVAL and tool.requires_approval)
                or (obligation is PolicyObligation.SECOND_APPROVER and tool.requires_approval)
                or obligation is PolicyObligation.REDACTION
                or obligation is PolicyObligation.ENHANCED_AUDIT
                or obligation is PolicyObligation.STEP_UP
                or (obligation is PolicyObligation.RATE_LIMIT and self._rate_limiter is not None)
            )
        ]
        if unsatisfiable:
            names = ", ".join(obligation.value for obligation in unsatisfiable)
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

        if PolicyObligation.STEP_UP in obligations:
            self._enforce_step_up_obligation(
                resolved=resolved,
                tool=tool,
                cleaned=cleaned,
                resource_ref=resource_ref,
                idempotency_key=idempotency_key,
                policy_versions=consulted,
            )

        redaction_required = gate.redaction_required
        if gate.rate_limit_required:
            # ``rate-limit`` is enforced here rather than deferred, and before the
            # approval gate: a caller over its budget must be refused *now*, not
            # after it has opened an approval request a human will be asked to rule
            # on. The key is per agent and per tool, so one hot tool cannot exhaust
            # the budget for every other tool the same agent may call — a bound the
            # policy author asked for on *this* capability, not a global throttle
            # (the boundary's own ``AgentRateLimitDep`` already does that).
            self._enforce_rate_limit_obligation(
                resolved=resolved,
                tool=tool,
                cleaned=cleaned,
                resource_ref=resource_ref,
                idempotency_key=idempotency_key,
                policy_versions=consulted,
            )
        if tool.requires_approval or gate.approval_required or gate.second_approver_required:
            return self._run_gated(
                resolved=resolved,
                tool=tool,
                cleaned=cleaned,
                principal=principal,
                resource_ref=resource_ref,
                idempotency_key=idempotency_key,
                policy_versions=consulted,
                redaction_required=redaction_required,
                decision_evidence=gate.evidence,
                # The two-person requirement is stamped on the request itself, so
                # the human queue shows what is being asked of them and the
                # gateway cannot forget the threshold when it later spends it.
                required_approvals=2 if gate.second_approver_required else 1,
            )
        return self._run_direct(
            resolved=resolved,
            tool=tool,
            cleaned=cleaned,
            principal=principal,
            resource_ref=resource_ref,
            idempotency_key=idempotency_key,
            policy_versions=consulted,
            redaction_required=redaction_required,
            decision_evidence=gate.evidence,
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
    ) -> _PolicyGate:
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

        Returns what the attempt must carry: the versions that were in force, the
        obligations the matching allow rules impose, and — when the decision
        imposes ``enhanced-audit`` — the extra evidence that obligation requires
        (NEX-055). The first two let the attempt cite them (NEX-052) and get them
        enforced rather than merely reported (NEX-053/054); the third is that
        enforcement for ``enhanced-audit``, which has no separate mechanism
        because the *record* is its mechanism.
        """
        if self._policy_versions is None:
            return _PolicyGate()

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

        evidence: DecisionEvidence | None = None
        if PolicyObligation.ENHANCED_AUDIT in decision.obligations:
            # The obligation is met by recording more than the default, so the
            # record *is* the mechanism: no separate adapter, nothing to defer.
            # The evidence names the rules that matched (the default trail names
            # only the versions), the obligations imposed (so a reader sees the
            # allow was conditional), and the attribute snapshot (so the decision
            # is reproducible rather than merely plausible). Built from the same
            # ``decision`` and ``attributes`` the gate just used — never re-derived
            # from the repository — so the evidence cannot describe a different
            # evaluation than the one that allowed the call.
            evidence = DecisionEvidence(
                matched_rules=tuple(decision.matched_rules),
                obligations=tuple(obligation.value for obligation in decision.obligations),
                attributes=tuple(sorted((name, str(value)) for name, value in attributes.items())),
            )
        return _PolicyGate(
            consulted=consulted,
            obligations=decision.obligations,
            evidence=evidence,
        )

    def _enforce_step_up_obligation(
        self,
        *,
        resolved: ResolvedToken,
        tool: ToolDefinition,
        cleaned: dict[str, object],
        resource_ref: str,
        idempotency_key: str,
        policy_versions: tuple[str, ...],
    ) -> None:
        """Require the delegating human to have authenticated with multi-factor (NEX-058).

        ADR-015 names the only deterministic signal a tool gateway can honestly
        use: the *recorded* authentication assurance of the human behind the
        agent, captured at login from the IdP's ``acr``/``amr`` claims and fixed
        onto the delegation when consent was given. An agent has no assurance of
        its own, so reading it live from a request (or from a claim the agent
        presents) would let the agent assert its own step-up.

        The floor is :data:`STEP_UP_FLOOR`, a static application constant. It is
        deliberately not read from the policy version or the rule: a policy author
        must be able to *ask* for step-up, but never to define what counts as
        stepped up — otherwise the same rule that requests the control could
        weaken it.

        A refusal is audited as a ``denied`` invocation naming the obligation and
        the assurance actually held, so a weaker delegation is visible in the
        trail rather than only as a 403 the caller saw.
        """
        held = resolved.delegation.assurance
        if held.meets(STEP_UP_FLOOR):
            return
        reason = (
            f"allowed only under obligation step-up: the delegation was granted at "
            f"{held.value} assurance, below the required {STEP_UP_FLOOR.value}"
        )
        self._record_policy_denial(
            resolved=resolved,
            tool=tool,
            cleaned=cleaned,
            resource_ref=resource_ref,
            idempotency_key=idempotency_key,
            outcome=reason,
            policy_versions=policy_versions,
        )
        raise AccessDenied(
            f"tool {tool.name!r} is {reason}; the delegating human must re-authenticate "
            "with multi-factor and grant a new delegation"
        )

    def _enforce_rate_limit_obligation(
        self,
        *,
        resolved: ResolvedToken,
        tool: ToolDefinition,
        cleaned: dict[str, object],
        resource_ref: str,
        idempotency_key: str,
        policy_versions: tuple[str, ...],
    ) -> None:
        """Enforce a policy's ``rate-limit`` obligation against the configured limiter (NEX-056).

        The obligation previously had no adapter, so the gateway refused any call
        a policy placed it on: honest, but it left the policy language unable to
        express the one bound an operator most often wants ("this agent may call
        this tool N times per window"). The mechanism now exists and reuses the
        same :class:`RateLimiter` port the HTTP boundary already depends on, so a
        deployment that swaps in a shared store gets the obligation enforced
        against that store without any change here.

        Three deliberate choices:

        * **The key is per agent *and* per tool.** The boundary's
          ``AgentRateLimitDep`` is a global throttle on an agent's request rate;
          this is the narrower bound a policy author asked for on one capability.
          Sharing one key would let a hot tool exhaust the budget of every other
          tool the agent may call, which is not what the rule says.
        * **An idempotent retry does not consume budget.** A retry under a
          recorded key is a replay of a decision already made, so the limiter is
          not consulted and the replay path returns the original outcome. Charging
          twice would make a network retry indistinguishable from abuse.
        * **A refusal is audited as a denial.** The row is the same ``denied``
          invocation a capability or policy refusal writes, naming the obligation
          and the retry window, so a throttled agent is visible in the trail
          rather than only in a 429 the caller saw.

        Fails closed when no limiter is wired: the obligation is then reported as
        unsatisfiable by the caller's check and the call is refused, never run
        unbounded.

        The bound itself comes from the deployment's configured limiter
        (``rate_limit_requests`` / ``rate_limit_window_seconds``), not from the
        rule: ``PolicyObligation`` is a closed vocabulary of *names* with no
        parameters, so a rule says "a volume bound applies" and the operator sets
        the number. A per-rule bound would need a parameterised obligation, which
        is a vocabulary change and therefore an ADR, not a quiet widening here.
        """
        if self._rate_limiter is None:  # pragma: no cover - guarded by the caller
            raise AccessDenied(
                f"tool {tool.name!r} is allowed only under obligation rate-limit, "
                "which this gateway has no limiter for"
            )
        prior = self._find_prior(
            resolved=resolved,
            tool=tool,
            cleaned=cleaned,
            idempotency_key=idempotency_key,
            resource_ref=resource_ref,
        )
        if prior is not None:
            return
        decision = self._rate_limiter.check(f"agent-tool:{resolved.agent.id}:{tool.name}")
        if decision.allowed:
            return
        reason = (
            f"allowed only under obligation rate-limit: {decision.limit} calls per window "
            f"exhausted for {tool.capability.value}; retry after {decision.retry_after}s"
        )
        self._record_policy_denial(
            resolved=resolved,
            tool=tool,
            cleaned=cleaned,
            resource_ref=resource_ref,
            idempotency_key=idempotency_key,
            outcome=reason,
            policy_versions=policy_versions,
        )
        raise RateLimited(
            f"tool {tool.name!r} refused: published policy imposes rate-limit "
            f"({decision.limit} calls per window); retry after {decision.retry_after}s",
            retry_after=decision.retry_after,
        )

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
        redaction_required: bool = False,
        decision_evidence: DecisionEvidence | None = None,
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
                decision_evidence=decision_evidence,
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

        if redaction_required:
            output, redacted = _redact_output(output)
            self._finalise(
                invocation,
                failed=False,
                outcome=_redacted_outcome(redacted),
            )
            return ToolResult(invocation=invocation, output=output)

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
        redaction_required: bool = False,
        decision_evidence: DecisionEvidence | None = None,
        required_approvals: int = 1,
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
                decision_evidence=decision_evidence,
                required_approvals=required_approvals,
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
                    decision_evidence=decision_evidence,
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
                decision_evidence=decision_evidence,
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

        if redaction_required:
            output, redacted = _redact_output(output)
            self._finalise(invocation, failed=False, outcome=_redacted_outcome(redacted))
            return ToolResult(invocation=invocation, output=output)

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
        decision_evidence: DecisionEvidence | None = None,
        required_approvals: int = 1,
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
                uow,
                resolved=resolved,
                tool=tool,
                resource_ref=resource_ref,
                now=now,
                required_approvals=required_approvals,
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
                    decision_evidence=decision_evidence,
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
        decision_evidence: DecisionEvidence | None = None,
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

        ``decision_evidence`` is passed only when a published policy imposed
        ``enhanced-audit`` on this call (NEX-055); it is sealed into the same
        intent hash, so the extra detail the obligation demands is as
        tamper-evident as the citation it accompanies. ``None`` is the normal
        case and records that no enhanced-audit obligation was in force.
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
            decision_evidence=decision_evidence,
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
    required_approvals: int = 1,
) -> ApprovalRequest:
    """Reuse this agent's open request for the same action, or open a new one.

    Advancing a paused run repeatedly must not pile up duplicate requests for one
    decision: the human revisits the queue and would face the same approval many
    times over. Only a still-decidable request for the identical
    (capability, resource) pair is reused; anything else opens afresh.

    A reused request is *raised* to the threshold the current policy demands, never
    lowered. Reuse is an efficiency, and it must not become a way to escape a
    stricter obligation: if the policy gained ``second-approver`` between two
    advances, an already-approved single-signature request would otherwise be
    spent under the weaker rule that was in force when it was opened.

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
            if existing.required_approvals < required_approvals:
                existing.raise_required_approvals(required_approvals)
                uow.approvals.save(existing)
            return existing
    request = ApprovalRequest.open(
        agent=resolved.agent,
        capability=tool.capability,
        resource_ref=resource_ref,
        now=now,
        required_approvals=required_approvals,
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


def _redact_output(output: dict[str, object]) -> tuple[dict[str, object], tuple[str, ...]]:
    """Apply the ``redaction`` obligation to a tool result.

    Returns the filtered output and the names of the top-level keys that were
    replaced, so the audit row can say *that* redaction happened without
    recording *what* was withheld — the field name is a hint an attacker should
    not get for free, but an operator needs to know the obligation fired.

    Filtering happens on the way out only: the note the handler read is untouched
    in the store, because the obligation governs what this caller may see, not
    what the platform may keep.
    """
    redacted = redact(output)
    withheld = tuple(sorted(key for key in output if not _same(redacted.get(key), output[key])))
    return redacted, withheld


def _same(left: object, right: object) -> bool:
    """Whether a redacted value equals the original, tolerating odd ``__eq__``.

    A value whose comparison raises is treated as *changed*, so an unusual object
    is reported as withheld rather than silently dropped from the count.
    """
    try:
        return bool(left == right)
    except Exception:
        return False


def _redacted_outcome(withheld: tuple[str, ...]) -> str:
    """An audit outcome that records the obligation fired.

    ``ok`` would be indistinguishable from an unredacted success, so an operator
    auditing the trail could not tell that a policy had filtered a response. The
    count, not the field names, is recorded: the trail is already readable by
    more people than the response was.
    """
    if not withheld:
        return "ok (redaction applied; nothing withheld)"
    return f"ok (redaction applied; {len(withheld)} field(s) withheld)"


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
