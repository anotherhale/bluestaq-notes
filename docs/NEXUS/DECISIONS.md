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


## NEX-047 (run `NEX-2026-11-27-50`) — policy semantics chosen where silence would be a decision

These are coordinator-level decisions. They shape a security-relevant contract, so
they are recorded rather than left to be inferred from the code.

**1. A missing attribute never satisfies a condition, under every operator including
`!=`.** The natural reading of `!=` is "the value differs", and an absent value
arguably differs from `"secret"`. That reading is a privilege escalation: a policy
written as `classification != secret` — the obvious way to say "not classified" —
would *pass* a resource that has no classification at all. Absence is not evidence, so
absence denies. Pinned across all four operators by a parametrised test.

**2. A wrong-typed attribute value raises; a missing one denies.** These look like the
same case and are not. A missing attribute is normal: not every resource carries every
attribute, and deny is the right answer. A value of the wrong type is a *defect* —
something wrote a string where the schema promised a number. Denying quietly would
report that defect as "not permitted", which is the one place an auditor will believe
it. Raising sends the reader to the actual problem.

**3. Retire before publish.** Publishing a new version must leave exactly one version
in force. Doing the retire first means a crash between the two writes leaves *nothing*
published, which denies; doing it the other way round leaves two in force, which is a
policy question with no correct answer. Fail-closed is achieved by write ordering, not
by a transaction the in-memory adapter does not have.

**4. Rollback is a new release.** The tempting implementation restores the old rules
in place. That erases the evidence that a rollback happened and makes the history
unable to answer "was v3 ever in force". Drafting the old rules under a new number
costs one row and keeps the history honest.

**5. The store assigns version numbers, never the caller.** A caller-supplied number
lets two authors both write v4 and makes "what was in force" ambiguous. This mirrors
the repository-wide rule that no caller identity or owner id comes from a request body.

**6. `approve` is not implemented, deliberately.** The lifecycle's full form includes
approval, which needs separation of duties (M3.3). A gate that always passes would be a
policy surface that *looks* governed and is not, and the failure would be invisible
because the surface exists. An absent gate is visible; a fake one is not.

**7. `name` on the adapter reports `"in-memory"`.** This follows NEX-046's
`source_name` precedent: an adapter that cannot be mistaken for durable storage is
worth a string constant. It is not cosmetic — it is the difference between a reader
asking "where is this stored" and assuming an answer.

**Carried forward.** Durable `PolicyVersionRepository` (NEX-048) must keep decisions 3
and 5 true at the database level, not only in the use case: a unique constraint on
`(policy_id, version_number)` and a guard that two rows cannot both be PUBLISHED for
one policy id. Reimplementing the lifecycle in SQL is not the goal; enforcing its two
invariants is.

## D-054 — Obligations are a separate axis on `PolicyDecision` (ADR-014)

**Run:** NEX-2026-11-27-54 · **Status:** decided, implementation not started

`PolicyEffect` has two members and its docstring argues deliberately against a third. The
security architecture names three effects and requires obligations be satisfied before an
operation proceeds. Both are right, and they are not in conflict once you separate the
questions: effect answers *may this proceed at all*, obligations answer *what must happen
for it to proceed*.

So obligations become a tuple on `PolicyDecision`, not a third effect. An obligation on a
`DENY` is a contradiction and raises. An obligation the gateway has no handler for
**denies** — that is the fail-closed rule, and it is what allows the type to land before
every obligation kind is implemented, without a window where it fails open.

Satisfaction happens at the enforcement point, not in `evaluate()`. `evaluate()` is pure
and cannot observe approvals or the clock; asking it to would require trusting a
caller-supplied "satisfied" flag, which is the exact claim the gateway exists to refuse.

Recorded as ADR-014 rather than implemented directly because it changes the shape of the
decision type that every enforcement path consumes. That is an architecture decision, and
the repository's rule is that architecture documents are contracts — a shape change gets an
ADR before code.

## D-069 — AMR factor independence (NEX-067)

**D-069a — a closed factor-family vocabulary, not an open one.** Each AMR method maps to one of
six families (`knowledge`, `one-time-code`, `sms`, `hardware-key`, `biometric`, `generic-mfa`)
and assurance requires two distinct families. The table is closed on purpose: an open or
prefix-matched scheme would let a new spelling (`otp-sms`, `webauthn-2`) be invented that reads
as a second factor. An unrecognised method is **ignored**, which can only understate assurance.
Understating costs a step-up prompt; overstating spends an obligation that exists to stop an
action.

**D-069b — `otp` and `sms` are separate families, deliberately.** A one-time code and an SMS
message are different channels even though the code is often *delivered* by SMS, and the review
named `sms + tel` as same-family while treating `otp + sms` as valid. Splitting them keeps
`pwd+otp` and `otp+fpt` multi-factor without letting `sms+tel` (one channel, two names) through.

**D-069c — did not use ACR values.** The review offered "factor category (or issuer-specific
ACR semantics)". ACR is issuer-defined, so a relying party that trusts it for *strength* is
trusting the issuer's policy rather than its own; a misconfigured issuer could assert a strong
ACR for a weak login, and there is no local evidence to contradict it. `amr` names methods,
which the relying party can classify itself. This keeps the decision inside our trust boundary.

**D-069d — a failing test was fixed, not worked around.** A first draft asserted
`["otp","totp","sms"]` was single-factor; it returned multi and failed. The test was wrong —
one-time code and SMS are two families — so the case was replaced with three genuinely
same-family spellings. The assertion was not loosened to fit.
