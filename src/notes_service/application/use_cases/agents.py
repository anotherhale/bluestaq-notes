"""Agent lifecycle, delegation, and token-exchange use cases.

These are the platform's central security boundary. Every rule is deterministic
application code; no model output ever grants authority. The invariants:

* **An agent never exceeds its owner.** Registration takes the owner from the
  authenticated principal, never the request body.
* **A delegation never exceeds the agent's ceiling** — enforced by
  :func:`~notes_service.domain.agent.delegation.attenuate`.
* **A token never exceeds its delegation** — the same attenuation runs again at
  issuance, so narrowing composes across hops.
* **Revocation is immediate** — token resolution re-checks the delegation and the
  agent on every use, so a revoked delegation or agent kills derived tokens on
  the next request rather than at expiry.

One scope note, stated rather than implied: a *user's* authority over a specific
note is per-resource (owner, or an explicit share). Delegation narrows an
agent's capability *ceiling*; the per-resource check still happens in the note
use cases via the existing ``AuthorizationProvider``. Delegating ``read_note``
therefore lets an agent *attempt* reads — it does not grant reads of notes the
delegator cannot see.
"""

from __future__ import annotations

from dataclasses import dataclass

from notes_service.application.errors import AccessDenied, InvalidInput, ResourceNotFound
from notes_service.application.ports.repositories import UnitOfWork
from notes_service.application.use_cases.base import UseCase
from notes_service.application.use_cases.tenant_access import resolve_tenant_access
from notes_service.domain.agent.audit_chain import ChainVerdict, verify_chain
from notes_service.domain.agent.delegation import DelegatedAuthorization, attenuate
from notes_service.domain.agent.entities import AgentIdentity, ApprovalRequest
from notes_service.domain.agent.token import AgentToken, hash_agent_token
from notes_service.domain.agent.tools import ToolInvocation
from notes_service.domain.agent.value_objects import (
    NON_DELEGABLE_CAPABILITIES,
    SENSITIVE_CAPABILITIES,
    AgentId,
    ApprovalRequestId,
    ApprovalStatus,
    Capability,
    DelegatedAuthorizationId,
    TokenAudience,
)
from notes_service.domain.errors import InvariantViolation
from notes_service.domain.identity.principal import Principal
from notes_service.domain.organization.tenant_access import TenantAccessStatus


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A freshly minted token: the aggregate plus the raw secret, returned once."""

    token: AgentToken
    secret: str


@dataclass(frozen=True, slots=True)
class ResolvedToken:
    """The outcome of resolving a presented token: its agent and capabilities."""

    agent: AgentIdentity
    delegation: DelegatedAuthorization
    token: AgentToken


def parse_capabilities(values: list[str] | set[str] | frozenset[str]) -> frozenset[Capability]:
    """Parse wire values into capabilities, rejecting unknown names.

    Rejecting rather than ignoring an unknown capability is deliberate: silently
    dropping one would let a caller believe it had been granted.
    """
    parsed: set[Capability] = set()
    for value in values:
        try:
            parsed.add(Capability(value))
        except ValueError as exc:
            raise InvalidInput(f"unknown capability: {value!r}") from exc
    if not parsed:
        raise InvalidInput("at least one capability is required")
    return frozenset(parsed)


def load_owned_agent(uow: UnitOfWork, *, principal: Principal, agent_id: str) -> AgentIdentity:
    """Load an agent the caller owns, else 404.

    A non-owner gets 404, not 403: an agent's existence is itself private, so it
    must not be probeable.
    """
    agent = uow.agents.get(AgentId.parse(agent_id))
    if agent is None or agent.owner_user_id != principal.user_id:
        raise ResourceNotFound("agent not found")
    return agent


class RegisterAgent(UseCase):
    """Register an agent owned by the authenticated caller.

    The owner is the principal. ``org_search`` is refused: it is reachable only
    through service-agent policy, never a user's registration.
    """

    def run(
        self,
        principal: Principal,
        *,
        display_name: str,
        runtime_identity: str,
        capabilities: list[str] | set[str],
        max_token_ttl_seconds: int | None = None,
    ) -> AgentIdentity:
        caps = parse_capabilities(capabilities)
        forbidden = caps & NON_DELEGABLE_CAPABILITIES
        if forbidden:
            raise AccessDenied(
                "capability is reserved for service-agent policy: "
                + ", ".join(sorted(c.value for c in forbidden))
            )
        with self._uow_factory() as uow:
            if uow.agents.get_by_runtime_identity(runtime_identity.strip().lower()) is not None:
                raise InvalidInput(f"runtime identity already registered: {runtime_identity}")
            kwargs: dict[str, object] = {}
            if max_token_ttl_seconds is not None:
                kwargs["max_token_ttl_seconds"] = max_token_ttl_seconds
            agent = AgentIdentity.register(
                display_name=display_name,
                owner_user_id=principal.user_id,
                runtime_identity=runtime_identity,
                capabilities=caps,
                **kwargs,  # type: ignore[arg-type]
            )
            uow.agents.add(agent)
            uow.commit()
            return agent


class ListAgents(UseCase):
    """List the caller's own agents. Never another user's."""

    def run(self, principal: Principal) -> list[AgentIdentity]:
        with self._uow_factory() as uow:
            return uow.agents.list_for_owner(principal.user_id)


class GetAgent(UseCase):
    """Fetch one agent the caller owns; anything else is 404."""

    def run(self, principal: Principal, *, agent_id: str) -> AgentIdentity:
        with self._uow_factory() as uow:
            return load_owned_agent(uow, principal=principal, agent_id=agent_id)


class GrantDelegation(UseCase):
    """Record the caller's consent for one of their agents to act, narrowed.

    The requested capabilities are attenuated against the agent's ceiling, so a
    delegator cannot hand out more than the agent was registered with. The agent
    must belong to the delegator: a user cannot delegate on behalf of another.
    """

    def run(
        self,
        principal: Principal,
        *,
        agent_id: str,
        capabilities: list[str] | set[str],
    ) -> DelegatedAuthorization:
        with self._uow_factory() as uow:
            agent = load_owned_agent(uow, principal=principal, agent_id=agent_id)
            if not agent.is_operational:
                raise AccessDenied(f"agent is {agent.status.value} and cannot be delegated to")
            # Raises InvariantViolation when the request exceeds the ceiling or
            # asks for a policy-only capability; both map to 403 at the edge.
            delegation = DelegatedAuthorization.grant(
                agent=agent,
                delegator_user_id=principal.user_id,
                capabilities=parse_capabilities(capabilities),
            )
            uow.delegations.add(delegation)
            uow.commit()
            return delegation


class IssueAgentToken(UseCase):
    """Exchange an active delegation for a short-lived, narrower token.

    Attenuation runs *again* here against the delegation's set, so a token is
    always a subset of the delegation that produced it. The agent must still be
    operational and the delegation still active, so a revocation between
    delegation and issuance is honoured.
    """

    def run(
        self,
        principal: Principal,
        *,
        delegation_id: str,
        capabilities: list[str] | set[str],
        audience: str,
        ttl_seconds: int,
    ) -> IssuedToken:
        with self._uow_factory() as uow:
            delegation = uow.delegations.get(DelegatedAuthorizationId.parse(delegation_id))
            # Only the delegator may redeem their own consent.
            if delegation is None or delegation.delegator_user_id != principal.user_id:
                raise ResourceNotFound("delegation not found")
            delegation.assert_usable()

            agent = uow.agents.get(delegation.agent_id)
            if agent is None or agent.owner_user_id != principal.user_id:
                raise ResourceNotFound("delegation not found")

            try:
                token_audience = TokenAudience(audience)
            except ValueError as exc:
                raise InvalidInput(f"unknown token audience: {audience!r}") from exc

            # Fail closed on a revoked/suspended agent or an over-long TTL before
            # narrowing, so the agent's own policy bounds the token.
            agent.assert_can_issue(audience=token_audience, ttl_seconds=ttl_seconds)

            narrowed = attenuate(
                granted=delegation.capabilities,
                requested=parse_capabilities(capabilities),
                require_delegable=False,
            )
            token, secret = AgentToken.issue(
                agent_id=agent.id,
                delegation_id=delegation.id,
                audience=token_audience,
                capabilities=narrowed,
                ttl_seconds=ttl_seconds,
                now=self._clock.now(),
            )
            uow.agent_tokens.add(token)
            uow.commit()
            return IssuedToken(token=token, secret=secret)


class ResolveAgentToken(UseCase):
    """Resolve a presented token to its agent, re-checking every hop.

    This is where revocation becomes immediate. The token's own expiry is not
    enough: the delegation may have been revoked, or the agent suspended, since
    issuance. Each is re-checked here, so authority cannot outlive its source.
    """

    def run(self, *, secret: str, audience: str) -> ResolvedToken:
        try:
            token_audience = TokenAudience(audience)
        except ValueError as exc:
            raise InvalidInput(f"unknown token audience: {audience!r}") from exc

        with self._uow_factory() as uow:
            token = uow.agent_tokens.get_by_token_hash(hash_agent_token(secret))
            if token is None:
                raise AccessDenied("invalid agent token")
            if token.audience is not token_audience:
                # A token minted for the tool gateway must not work on the Notes
                # API and vice versa.
                raise AccessDenied("agent token is not valid for this audience")

            # Revocation is expressed as a domain invariant on the token and the
            # delegation. Translate it here: an expired authority is a refusal,
            # not a broken invariant, and must not surface as a 500 at the edge.
            try:
                token.assert_live(now=self._clock.now())
                delegation = uow.delegations.get(token.delegation_id)
                if delegation is None:
                    raise AccessDenied("agent token delegation no longer exists")
                delegation.assert_usable()
            except InvariantViolation as exc:
                raise AccessDenied(str(exc)) from exc

            agent = uow.agents.get(token.agent_id)
            if agent is None or not agent.is_operational:
                raise AccessDenied("agent is not operational")

            # The token must still be a subset of both its delegation and the
            # agent's current ceiling; narrowing either since issuance revokes it.
            if not token.capabilities <= delegation.capabilities:
                raise AccessDenied("agent token exceeds its delegation")
            if not token.capabilities <= agent.capabilities:
                raise AccessDenied("agent token exceeds the agent's capabilities")

            # The agent acts *as its delegator*, so it must not outlive the
            # delegator's entitlement to a tenant. Without this, suspending or
            # deprovisioning a person left their agent fully operational — the
            # same offboarding gap ``ensure_tenant_access`` closes on the three
            # human identity paths, reopened here because token resolution is a
            # fourth way to establish an identity. ``UNAFFILIATED`` is allowed
            # (a brand-new identity must be able to bootstrap), but every
            # per-resource check still runs as the delegator downstream.
            if (
                resolve_tenant_access(uow, user_id=delegation.delegator_user_id).status
                is TenantAccessStatus.DENIED
            ):
                raise AccessDenied("agent token delegator is not entitled to any tenant")

            return ResolvedToken(agent=agent, delegation=delegation, token=token)


class RevokeDelegation(UseCase):
    """Revoke one delegation and every token derived from it, immediately."""

    def run(self, principal: Principal, *, delegation_id: str) -> None:
        now = self._clock.now()
        with self._uow_factory() as uow:
            delegation = uow.delegations.get(DelegatedAuthorizationId.parse(delegation_id))
            if delegation is None or delegation.delegator_user_id != principal.user_id:
                raise ResourceNotFound("delegation not found")
            delegation.revoke(now=now)
            uow.delegations.save(delegation)
            uow.agent_tokens.revoke_all_for_delegation(delegation.id, now=now)
            # A granted approval is live authority, and it names no delegation
            # of its own. Once this consent is withdrawn, any approval the agent
            # still holds cannot be attributed to a delegation the delegator
            # still stands behind — so it is withdrawn too. Leaving it would let
            # a fresh delegation revive an authorisation granted under the old
            # one, which the delegator never re-granted.
            uow.approvals.revoke_granted_for_agent(
                delegation.agent_id,
                now=now,
                reason=f"delegation {delegation.id} revoked",
            )
            uow.commit()


class RevokeAgent(UseCase):
    """Emergency stop: revoke an agent, all its delegations, and all its tokens.

    Ordering matters. The agent is revoked first, then its delegations, then the
    tokens, so an in-flight request that resolves a token between steps still
    fails closed on the agent check.
    """

    def run(self, principal: Principal, *, agent_id: str) -> AgentIdentity:
        now = self._clock.now()
        with self._uow_factory() as uow:
            agent = load_owned_agent(uow, principal=principal, agent_id=agent_id)
            agent.revoke(now=now)
            uow.agents.save(agent)
            uow.delegations.revoke_all_for_agent(agent.id, now=now)
            uow.agent_tokens.revoke_all_for_agent(agent.id, now=now)
            # Approvals outlive the agent otherwise: they are keyed by agent and
            # carry no delegation, so a reactivated agent would inherit grants
            # made before the emergency stop. Withdraw them with the rest.
            uow.approvals.revoke_granted_for_agent(
                agent.id,
                now=now,
                reason="agent revoked",
            )
            uow.commit()
            return agent


class OpenApprovalRequest(UseCase):
    """An agent asks a human to authorise one sensitive action.

    The agent must already hold the capability; the approval gates *use* of it,
    it does not grant it.
    """

    def run(
        self,
        principal: Principal,
        *,
        agent_id: str,
        capability: str,
        resource_ref: str,
    ) -> ApprovalRequest:
        try:
            cap = Capability(capability)
        except ValueError as exc:
            raise InvalidInput(f"unknown capability: {capability!r}") from exc
        if cap not in SENSITIVE_CAPABILITIES:
            raise InvalidInput(f"capability {cap.value} is not sensitive and needs no approval")
        with self._uow_factory() as uow:
            agent = load_owned_agent(uow, principal=principal, agent_id=agent_id)
            request = ApprovalRequest.open(agent=agent, capability=cap, resource_ref=resource_ref)
            uow.approvals.add(request)
            uow.commit()
            return request


class DecideApprovalRequest(UseCase):
    """A human approves or denies a sensitive action.

    Separation of duties is enforced in the aggregate: the approver must be a
    distinct human identity. The caller here is always a human principal; an
    agent has no user id and cannot reach this path.
    """

    def run(
        self,
        principal: Principal,
        *,
        approval_id: str,
        approve: bool,
        reason: str | None = None,
    ) -> ApprovalRequest:
        now = self._clock.now()
        with self._uow_factory() as uow:
            request = uow.approvals.get(ApprovalRequestId.parse(approval_id))
            if request is None:
                raise ResourceNotFound("approval request not found")
            # The approver must own the agent the request was opened for, so one
            # user cannot decide another user's agent's requests.
            agent = uow.agents.get(request.agent_id)
            if agent is None or agent.owner_user_id != principal.user_id:
                raise ResourceNotFound("approval request not found")
            if approve:
                request.approve(approver_user_id=principal.user_id, now=now, reason=reason)
            else:
                request.deny(approver_user_id=principal.user_id, now=now, reason=reason)
            uow.approvals.save(request)
            uow.commit()
            return request


class RevokeApprovalRequest(UseCase):
    """Withdraw an approval that was granted but not yet spent.

    ``DecideApprovalRequest`` covers a request that is still pending. This covers
    the gap after a human says yes and before the agent acts: an approval is live
    authority from the moment it is granted, so a mistaken or no-longer-desired
    grant must be cancellable without waiting for its validity window to lapse.

    Ownership is checked exactly as on the decision path — the caller must own the
    agent the request belongs to — and a non-owner gets 404 rather than 403, so an
    approval id cannot be used to probe for another user's agent.
    """

    def run(
        self,
        principal: Principal,
        *,
        approval_id: str,
        reason: str | None = None,
    ) -> ApprovalRequest:
        now = self._clock.now()
        with self._uow_factory() as uow:
            request = uow.approvals.get(ApprovalRequestId.parse(approval_id))
            if request is None:
                raise ResourceNotFound("approval request not found")
            agent = uow.agents.get(request.agent_id)
            if agent is None or agent.owner_user_id != principal.user_id:
                raise ResourceNotFound("approval request not found")
            request.revoke(revoked_by_user_id=principal.user_id, now=now, reason=reason)
            uow.approvals.save(request)
            uow.commit()
            return request


class ListApprovalRequests(UseCase):
    """List approval requests for one of the caller's agents.

    ``status`` defaults to ``pending`` because that is the actionable queue a
    reviewer needs first, and it is what this endpoint returned before filtering
    existed. Every other value selects from the agent's *full* history, which is
    what makes a decision reviewable after the fact: a decided approval used to
    disappear from the only read path, so an approver could not confirm what they
    had approved, and an auditor could not see the grant at all through the API.
    """

    def run(
        self,
        principal: Principal,
        *,
        agent_id: str,
        status: str | None = None,
    ) -> list[ApprovalRequest]:
        wanted = ApprovalStatus(status) if status is not None else ApprovalStatus.PENDING
        with self._uow_factory() as uow:
            agent = load_owned_agent(uow, principal=principal, agent_id=agent_id)
            if wanted is ApprovalStatus.PENDING:
                return uow.approvals.list_pending_for_agent(agent.id)
            return [
                request
                for request in uow.approvals.list_for_agent(agent.id)
                if request.status is wanted
            ]


class GetApprovalRequest(UseCase):
    """Read one approval request belonging to one of the caller's agents.

    The decision and revocation routes both take an approval id, but there was no
    way to read that id back afterwards. A reviewer who approved or revoked a
    sensitive action could not confirm the resulting record, and the audit trail
    held the only copy. Ownership is checked as everywhere else in this context:
    a non-owner gets 404, so an approval id cannot probe for another user's agent.
    """

    def run(self, principal: Principal, *, approval_id: str) -> ApprovalRequest:
        with self._uow_factory() as uow:
            request = uow.approvals.get(ApprovalRequestId.parse(approval_id))
            if request is None:
                raise ResourceNotFound("approval request not found")
            agent = uow.agents.get(request.agent_id)
            if agent is None or agent.owner_user_id != principal.user_id:
                raise ResourceNotFound("approval request not found")
            return request


class ListToolInvocations(UseCase):
    """List the tool-invocation audit trail for one of the caller's agents.

    The gateway writes a row for *every* attempt, including refusals, but until
    this use case existed nothing outside the gateway itself could read one back.
    An audit trail nobody can read is not an audit trail.

    Scoped to the agent's owner, matching ``ListApprovalRequests``: the trail says
    which tools an agent reached for and when, so it is the owning human's
    evidence. A non-owner gets ``404``, never ``403``, so the endpoint cannot be
    used to probe for the existence of another user's agent.
    """

    def run(
        self,
        principal: Principal,
        *,
        agent_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ToolInvocation]:
        with self._uow_factory() as uow:
            agent = load_owned_agent(uow, principal=principal, agent_id=agent_id)
            return uow.tool_invocations.list_for_agent(agent.id, limit=limit, offset=offset)


class VerifyToolInvocationChain(UseCase):
    """Recompute one agent's audit chain and report the first contradiction.

    Reading the trail (``ListToolInvocations``) shows what the database *claims*
    happened. This use case is what makes the claim checkable: every entry's
    seals are recomputed from its own stored fields and its predecessor's hash,
    so a deleted, reordered or edited entry is reported rather than believed.

    The whole trail is loaded, not a page. A window cannot be verified on its
    own — its first entry links to a predecessor the window does not contain —
    so paging here would produce a verdict that means nothing. The scope is
    bounded by agent ownership instead, and the endpoint is owner-only, so a
    non-owner gets ``404`` exactly as with the listing.
    """

    def run(self, principal: Principal, *, agent_id: str) -> ChainVerdict:
        with self._uow_factory() as uow:
            agent = load_owned_agent(uow, principal=principal, agent_id=agent_id)
            return verify_chain(uow.tool_invocations.list_chain(agent.id))


__all__ = [
    "DecideApprovalRequest",
    "GetAgent",
    "GrantDelegation",
    "IssueAgentToken",
    "IssuedToken",
    "ListAgents",
    "ListApprovalRequests",
    "ListToolInvocations",
    "OpenApprovalRequest",
    "RegisterAgent",
    "ResolveAgentToken",
    "ResolvedToken",
    "RevokeAgent",
    "RevokeDelegation",
    "VerifyToolInvocationChain",
    "load_owned_agent",
    "parse_capabilities",
]
