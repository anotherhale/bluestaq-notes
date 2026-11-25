# NEXUS Coordinator Decisions

Implementation decisions taken by the coordinator that do not rise to the level of an ADR.
Architectural decisions affecting domain boundaries, security, persistence, contracts, major
dependencies or platform direction belong in `docs/ADR/`, not here.

Each entry records the decision, the alternatives considered, and why the rejected ones were
rejected — so a later run does not have to rediscover the reasoning.

---

## D-001 — Keep `is_expired` and delegate it to `expired_at` (NEX-026, `b46e2e3`)

**Context.** `ApprovalRequest` had two ways to ask whether its bound had passed: `is_expired`
(no argument, reads the module wall clock) and, after NEX-026, `expired_at(*, now)`. The
decision path used the first while the bound had been stamped by the caller's `Clock` port,
which is the R29 defect.

**Decision.** `expired_at(*, now)` is the only place the comparison is written. `is_expired`
remains on the aggregate as a wall-clock convenience and is implemented as
`return self.expired_at(now=datetime.now(UTC))`.

**Alternatives rejected.**

- *Delete `is_expired` outright.* Tidier, and it would make the two-clock mistake impossible to
  repeat. Rejected because it silently changes the contract for callers that legitimately hold
  no clock (and for the repository adapter's NULL handling), and a broad signature change is a
  much larger blast radius than the defect requires. NEX-026 is one security slice; it should
  not also be an API break.
- *Leave `is_expired` as a separate implementation.* Rejected outright: two copies of the
  NULL-means-lapsed rule is exactly how the original divergence happened.

**Consequence to watch.** Because `is_expired` still exists, a future caller can still reach the
wall clock by accident. That is acceptable only while the production paths pass a clock; if a
new decision path appears that calls `is_expired`, R29 reopens. The mitigation is the invariant
recorded in R29, not the removal of the method.
