"""Read the process-wide withholding totals (R34).

:class:`~notes_service.application.use_cases.approval_advisory.RecommendApprovalDecision`
already records one advisory's over-cap withholdings and logs the running total.
A log line is not an aggregate an operator can alert on: answering "how often are
advisories thinner than they should be" meant reading individual responses.

This use case is the read side of that recorder. It is deliberately **not** a
per-user view. The recorder counts every advisory the *process* served, so a
snapshot necessarily spans all tenants: exposing it to any authenticated caller
would let one user infer another user's advisory volume. It is therefore gated on
an explicit operator allowlist and fails closed in both directions:

* an empty allowlist means nobody may read it, not everybody;
* a caller who is not on the list gets ``404``, not ``403``, so the endpoint's
  existence is not advertised to an ordinary user.

The snapshot carries counts only — never a field name, value, note id or user id.

The allowlist and its record now live in
:class:`~notes_service.application.use_cases.operator_gate.OperatorGate`, shared
with every other operational view, so the two cannot drift and each granted read
is recorded. See that module for the gate's own guarantees.

It takes no unit of work: reading a counter mutates nothing and must not open a
transaction to say so.
"""

from __future__ import annotations

from notes_service.application.ports.withholding import (
    WithholdingRecorder,
    WithholdingSnapshot,
)
from notes_service.application.use_cases.operator_gate import OperatorGate
from notes_service.domain.identity.principal import Principal

OPERATION = "withholding-report"


class GetWithholdingReport:
    """Return the process-wide withholding totals to an authorized operator."""

    def __init__(
        self,
        *,
        withholding_recorder: WithholdingRecorder,
        operator_gate: OperatorGate,
    ) -> None:
        self._withholding_recorder = withholding_recorder
        self._operator_gate = operator_gate

    def run(self, principal: Principal) -> WithholdingSnapshot:
        # Platform operations must never be reachable by content access alone, so
        # the gate is explicit rather than an AuthorizationProvider decision. A
        # granted read is recorded; a refused one is not.
        self._operator_gate.authorize(principal, operation=OPERATION)
        return self._withholding_recorder.snapshot()


__all__ = ["GetWithholdingReport"]
