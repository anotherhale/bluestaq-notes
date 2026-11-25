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

It takes no unit of work: reading a counter mutates nothing and must not open a
transaction to say so.
"""

from __future__ import annotations

from notes_service.application.errors import ResourceNotFound
from notes_service.application.ports.withholding import (
    WithholdingRecorder,
    WithholdingSnapshot,
)
from notes_service.domain.identity.principal import Principal
from notes_service.domain.identity.value_objects import UserId


class GetWithholdingReport:
    """Return the process-wide withholding totals to an authorized operator."""

    def __init__(
        self,
        *,
        withholding_recorder: WithholdingRecorder,
        operator_ids: frozenset[UserId],
    ) -> None:
        self._withholding_recorder = withholding_recorder
        self._operator_ids = operator_ids

    def run(self, principal: Principal) -> WithholdingSnapshot:
        # Explicit, single-point gate. Deliberately not an AuthorizationProvider
        # decision: that policy decides note *content* access, and platform
        # operations must never be reachable by content access alone.
        if principal.user_id not in self._operator_ids:
            raise ResourceNotFound("not found")
        return self._withholding_recorder.snapshot()


__all__ = ["GetWithholdingReport"]
