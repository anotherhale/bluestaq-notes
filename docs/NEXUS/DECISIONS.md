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

---

## D-R39 — Lease holder identity is the session, not the pid

**Run** `NEX-2026-11-27-46`. **Commit** `64759f4`. **Supersedes** the `host-pid` default
introduced with the coordination substrate (ADR-013).

**Decision.** `default_holder()` in `tools/nexus_coord/lease.py` returns, in order:
`NEXUS_HOLDER` if set, else `host-s<sid>` from `os.getsid(0)`, else `host-<pid>` only where
`os.getsid` is unavailable or raises. `Holder.parse` accepts both shapes.

**Why the identity must be session-scoped.** Two properties are required and they pull in
opposite directions:

1. *Stability within a run.* Every `nexus_coord` invocation is a fresh process. The holder that
   `acquire` writes must equal the holder that `release` and `renew` compute, or the lease is
   single-use and every acquire blocks the next run for the full TTL. This is R39.
2. *Distinctness between runs.* Two concurrent runs on one host must not compute the same
   holder, or they would each believe they hold the other's lease.

A pid gives (2) and fails (1) — the defect. A per-invocation run id gives both, but it is a
briefing value passed by hand, so coordination would silently degrade to "works only if the
operator remembered the flag". The POSIX session id gives both for free: one terminal session
drives a run, every process it spawns inherits the session, and overlapping runs have different
sessions. This is the same reasoning that made the original `host-pid` choice look reasonable,
carried one step further — the author was thinking about *a process* when the unit of work is
*a run*.

**Alternatives considered**

- *Accept a run id and require callers to pass it.* Rejected: makes correctness depend on human
  recall at every call site, and fails silently when forgotten. Kept as the `NEXUS_HOLDER`
  override for harnesses whose session semantics differ.
- *Treat "holder pid is dead" as permission to take over.* Rejected, and this is the important
  one: it converts a visible lockout into a silent mutual-exclusion failure. With `host-pid`, the
  recorded pid is dead on the next call by construction, so the rule degenerates to "always
  reclaim". Liveness stays reporting-only; expiry alone frees a live lease.
- *Store a timestamp of acquisition and compare wall clocks.* Already the mechanism for TTL; it
  says nothing about *who* holds the lease.

**Consequences to watch.** The fix is only as good as session stability. A harness that spawns
each invocation under its own session (for example via `setsid`) reproduces R39 exactly — that
is the case `NEXUS_HOLDER` exists for, and it is documented rather than left to be discovered.
A recycled session-leader pid can make `holder_liveness` report `alive` for a dead run; that is
reporting inaccuracy only, because acquisition is gated on expiry, never on liveness, so it
cannot produce a false takeover. Legacy leases written with `host-pid` remain un-releasable by
the new code and must be allowed to expire; the ref must never be force-deleted.

