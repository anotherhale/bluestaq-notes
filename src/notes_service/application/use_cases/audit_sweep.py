"""Verify every agent's audit trail in the deployment (NEX-042).

``VerifyToolInvocationChain`` answers "is this one agent's trail intact" for the
agent's owner. Nothing answered the platform question — *is any trail in this
deployment broken* — so an integrity regression could sit undetected until the
affected owner happened to open their own agent. Detection is not an owner's
duty; it is the platform's.

The sweep is gated by the shared
:class:`~notes_service.application.use_cases.operator_gate.OperatorGate`, exactly
as ``GetWithholdingReport`` is, and for the same reason. Its output names agents
and their owners, which is precisely what ``AgentRepository.list_for_owner``
refuses to disclose to an ordinary user; so the one method that enumerates
agents across owners (``list_all_for_integrity_sweep``) is reachable only through
that allowlist. The gate fails closed in both directions:

* an empty allowlist means nobody may sweep, not everybody;
* a caller who is not on the list gets ``404``, not ``403``, so the endpoint's
  existence is not advertised to an ordinary user.

The gate also records each granted sweep, so "who ran the integrity check" is
answerable from inside the system. See that module for the record's shape.

It takes no unit of work: verifying a chain reads rows and mutates nothing, so
opening a transaction to say so would be noise.
"""

from __future__ import annotations

from notes_service.application.ports.repositories import UnitOfWork
from notes_service.application.use_cases.base import UnitOfWorkFactory
from notes_service.application.use_cases.operator_gate import OperatorGate
from notes_service.domain.agent.audit_chain import (
    ChainAuditFinding,
    ChainAuditReport,
    verify_chain,
)
from notes_service.domain.agent.entities import AgentIdentity
from notes_service.domain.identity.principal import Principal

OPERATION = "audit-integrity"

# A cap, not a target. One sweep walks every agent and loads each whole trail,
# so an uncapped scan of a large deployment would hold the database open for
# longer than a request should. The report says ``truncated`` when the cap bit,
# which is the difference between "all chains are intact" and "no break among
# the ones I looked at".
DEFAULT_MAX_AGENTS = 500


class SweepAuditIntegrity:
    """Verify every agent's trail and report each contradiction found."""

    def __init__(
        self,
        *,
        uow_factory: UnitOfWorkFactory,
        operator_gate: OperatorGate,
        max_agents: int = DEFAULT_MAX_AGENTS,
    ) -> None:
        self._uow_factory = uow_factory
        self._operator_gate = operator_gate
        self._max_agents = max_agents

    def run(self, principal: Principal) -> ChainAuditReport:
        # Platform-wide integrity data must never be reachable by content access
        # alone, so the gate is explicit rather than an AuthorizationProvider
        # decision. A granted sweep is recorded; a refused one is not.
        self._operator_gate.authorize(principal, operation=OPERATION)

        with self._uow_factory() as uow:
            total = uow.agents.count_all_for_integrity_sweep()
            agents = uow.agents.list_all_for_integrity_sweep(limit=self._max_agents)
            findings = tuple(
                finding
                for finding in (self._sweep_agent(uow, agent) for agent in agents)
                if finding is not None
            )
            return ChainAuditReport(
                agents_total=total,
                agents_checked=len(agents),
                truncated=len(agents) < total,
                broken=findings,
            )

    def _sweep_agent(self, uow: UnitOfWork, agent: AgentIdentity) -> ChainAuditFinding | None:
        verdict = verify_chain(
            uow.tool_invocations.list_chain(agent.id),
            anchor=uow.tool_invocations.chain_anchor(agent.id),
        )
        if verdict.verified:
            return None
        # ``verify_chain`` guarantees a first_break when ``verified`` is False;
        # the assertion documents that invariant rather than trusting it.
        assert verdict.first_break is not None
        return ChainAuditFinding(
            agent_id=str(agent.id),
            owner_user_id=str(agent.owner_user_id),
            checked=verdict.checked,
            reason=verdict.first_break.reason,
            first_break_sequence=verdict.first_break.sequence,
        )


__all__ = ["DEFAULT_MAX_AGENTS", "SweepAuditIntegrity"]
