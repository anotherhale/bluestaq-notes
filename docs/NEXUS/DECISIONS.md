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

## D-076 — an operator-grant rationale is optional by provenance, not by convenience (NEX-076)

**D-076a — the column is nullable because `granted_by` is.** The `rationale` column mirrors the
provenance field exactly: a grant with a human issuer must carry a reason, and a configuration
grant must not. This is the pairing rule, enforced in the domain, not two independent optional
fields a caller could combine freely. Making `rationale` unconditionally required would force the
configuration seeder to invent a reason for a decision whose reason is the reviewed deployment
itself; making it unconditionally optional would let the one case that matters — a human handing
out privilege — omit the justification.

**D-076b — no backfill, and the gap is recorded rather than filled.** Every pre-existing row is a
configuration grant by construction: the write path that sets `granted_by` was introduced in
NEX-074, so no human-issued grant can predate the rationale requirement. There is nothing in the
database from which to reconstruct a reason. Fabricating one would put a plausible-sounding
sentence into an audit record that no human ever wrote, which is strictly worse than an honest
NULL. A check constraint forbids the empty string so that "no record" and "a record that says
nothing" stay distinguishable.

**D-076c — one bound, declared twice, tested at both.** The length bound lives in
`MAX_RATIONALE_LENGTH` and is applied by both the Pydantic request schema and the domain value
object. This is deliberate duplication of a *constant*, not of logic: the boundary needs a cheap
`422` with a field name, and the domain needs to be safe for callers that never pass through HTTP.
They share the constant, so they cannot drift.

**Deliberately not done.** No rationale on the *revocation* path (there is no revocation path
yet), and no free-text field on the configuration seeder — see D-076a.

**D-079a — revocation is a fact on the row, not a delete of it.** A grant is kept precisely so
an auditor can read what authority existed. `DELETE` would remove the grant and, with it, the
evidence that the authority was withdrawn — the one fact the audit record is for. Both revokers
therefore write `revoked_at`/`revoked_by` and leave the row in place. Consequence accepted: every
read path must exclude revoked rows, and `OperatorGrant.is_active` is the single place that does —
both adapters (`active_grants`) route through it rather than filtering in their own query, so
there is one definition of "active" and no path can quietly omit the check.

**D-079e — a revoked grant is inactive regardless of the caller's clock.** `is_active` checks
`revoked_at` before any time comparison and does not compare it to `now`. Revocation only ever
removes authority, so honouring it "from the moment the revoker's clock says" could resurrect the
grant for a caller whose clock runs behind. Same family as R29, resolved in the fail-safe
direction. A future-dated revocation record still confers nothing immediately.

**D-079b — the pairing is enforced twice, on purpose.** `revoked_at` without `revoked_by`, or the
reverse, is a half-written fact that reads as a complete one. The domain refuses the pair, and a
`CHECK` constraint refuses it for any writer that bypasses the use case (a seeder, a migration,
psql). Unlike D-076c this is not duplication of a constant but of an *invariant*, and it is
justified because the two enforcers protect different populations: the domain protects callers,
the constraint protects the table.

**D-079c — `revoked_by` is SET NULL, not CASCADE.** A revocation must not be erasable by an
unrelated action. If the revoking operator's account is deleted, `CASCADE` would delete the
revocation record with it, silently restoring a grant in the audit trail's reading. `SET NULL`
keeps the instant and loses only the attribution — a degradation, and a visible one: the pairing
constraint forces `revoked_at` to stay, so a NULL `revoked_by` on a revoked row is legible as
"revoked, revoker removed".

**D-079d — no rationale field on the revocation path.** D-076 argued the *rationale* field
existed to justify handing privilege *out*. Withdrawing privilege is the fail-safe direction:
making it require a written reason would add friction exactly where the system should be cheap to
make safer, and would tempt a hurried operator to type "x". `revoked_by` + `revoked_at` is the
audit minimum. If revocation review is ever needed, it belongs in an approval workflow, not a
free-text column.

**Deliberately not done.** No cascade from revocation to the holder's sessions or tokens: an
operator grant is re-read per request from the repository, so revocation takes effect on the next
call without a token to invalidate. If a token cache is ever introduced, that reasoning stops
holding and this needs revisiting.

---

## D-082 — The operator access trail pages by keyset cursor, not by offset

**Context.** `GET /ops/access-report` returned at most `limit` newest entries and offered no
way to read further back, while retention keeps the log for years. NEX-081 had just made a
*paging* mistake visible on a neighbouring endpoint and it would have been easy to repeat it
here: reach for `offset`.

**Decision.** A keyset cursor. `OperatorAccessEntry.position` is an opaque, monotonic
insertion ordinal; the client passes the previous page's `next_position` back as
`before_position`, and the read returns entries strictly older than it.

**Why offset is wrong *here* specifically.** This endpoint is not a plain read: an operator
reading the trail is itself a granted operator read, and the gate records that read before
the report reads its page. Every request therefore inserts a row at the head of the very log
it is paging. An `OFFSET`-based page shifts by one row per page — page *n+1* repeats an entry
page *n* already showed and skips one it has not. A trail with invisible holes is worse than
a capped read, because it looks complete. NEX-081's sweep had the same lesson with a different
mechanism (a stable `(created_at, id)` order made an offset *total* there); the difference is
that the sweep does not append to what it reads.

**Consequences.**

- The SQL adapter's surrogate `id` is the position: unique, so a page boundary can never land
  between two entries. A cursor is anchored to an entry, not to a count.
- The in-memory ring must use a monotonic counter, not `len(entries)` — the ring evicts its
  oldest entry on overflow, so a length-derived ordinal would reuse a position a surviving
  entry still holds, and a cursor would skip rows.
- `position` is *not* a count: retention deletes rows and the ring evicts them, so position 42
  does not mean "forty-two entries exist". Documented on the port so no caller reads it as one.
- A non-positive cursor is rejected rather than read as "everything from now on". The
  capability gate still runs first, so a non-operator gets `404` and cannot infer from a `400`
  that the route parses `before_position`.
- `truncated` is true for a middle page as well as a filled one: "under-full" is not "the whole
  trail", and a client that treats an under-full page as completion will stop early.

**Not adopted.** No `total` count. Counting the retained trail costs a scan proportional to its
(retained for years) size on every page, and the number would be wrong the moment a read
appends. A client that needs to know it reached the end follows `next_position` until it is
null.

---

## D-097 — The roster excludes the terminal membership state only, not "anything not active" (NEX-097)

`SqlAlchemyOrganizationMembershipRepository.list_for_organization` now filters
`status != DEPROVISIONED`. The obvious alternative is `status == ACTIVE`, and it is wrong.

**Alternatives considered.**

- *`status == ACTIVE`.* Simplest to state, and it passes the two offboarding tests this run added.
  It also hides `SUSPENDED`, `INVITED` and `PENDING` memberships. Those are all states an
  administrator can move back to active, and the roster is where the administrator finds the
  membership id to act on. Filtering them would make suspension a one-way door through the only
  list that names the person. Rejected.
- *Filter in the router rather than the repository.* The router already computes
  `PageMeta(total=len(items))` from the repository's return value, so a router-side filter would
  leave `total` counting the hidden rows — a roster that says it has 41 members and shows 38. The
  defect this task fixes was exactly that miscount, so fixing the rows while keeping the count
  wrong would have been half a fix. Rejected: the filter belongs where `total` is computed.
- *Hard-delete the membership on offboarding.* Destroys the audit record of who was a member and
  when they left, and `DEPROVISIONED` exists precisely to retain it. Rejected in NEX-001.

**Adopted.** Filter only the terminal `DEPROVISIONED` state, in the repository, so rows and
`total` agree. `test_a_suspended_member_stays_in_the_roster` is the guard against the rejected
alternative, and it is a **proven** guard, not an assumed one: this run replaced the predicate
with `== ACTIVE` and the test went red (`1 failed, 2 passed`), then restored it and re-ran green
(`3 passed`). Do not "strengthen" it onto the deprovision path — its job is to fail for the wrong
fix, and a test that also fails for the right fix cannot do that job. Equally, do not weaken it
into "suspended rows are absent", which would agree with the wrong fix instead of contradicting
it.

## D-098 — The roster's total is counted by a separate query, not carried on the list (NEX-098)

*Context.* `GET /organizations/{id}/members` returned the whole roster and built `PageMeta(total=len(items))`.
`PageMeta` already had `limit` and `offset` fields; nothing populated them. There is no
per-organization member cap.

*Decision.* Page the roster and make the total a **separate `count_for_organization` call**, not a
property of the listing result.

*Why not the cheaper shape.* The cheapest change is to return a page plus the count computed in the same
statement — which is what most ORMs push you toward. Two reasons rejected it:

- A total carried **on** the list can be reconstructed from the list by a careless caller, and once one
  caller writes `total=len(items)` the defect is indistinguishable from correct code at the call site.
  A separate method makes `len()` structurally unavailable where a count is required.
- The total must survive a page that is **empty because it is past the end**. `len(items)` is `0` there
  and a client's pager would conclude the roster is empty and stop. The API test
  `test_a_page_beyond_the_roster_is_empty_not_an_error` pins this: on a one-member roster,
  `limit=10&offset=5` returns `items: []` with `meta.total == 1`.

*Why the count and the listing share `_roster_predicate`.* A count selecting a different set from the
listing reports a total no page can reach. That is the *same* defect as `len(items)` one layer down, and
it is the defect NEX-097 was opened to fix — so it must not be reintroduced while closing the bound.

*Why the count is an aggregate.* It reads no rows, so it costs the same at any tenant size. A `len()`
over a second full read would double the very cost the page exists to bound.

*Why `limit=None` still means the whole roster.* Internal callers (authorization checks, team membership
validation) legitimately need the set and never serialize it to a client. Only the client-facing read
bounds itself. `test_no_limit_still_returns_the_whole_roster` stops a later "bound everything" sweep from
breaking them.

*Why the order is `(created_at, id)`, not just `created_at`.* `OFFSET` paging over a non-total order is
not merely unstable: it can show one row twice **and** never show another. Memberships invited in one
request burst share `created_at` down to the timestamp, so the tie is not hypothetical. The persistence
test seeds every row at a single `TIED_AT` and inserts them in **reverse id order**, then asserts the
whole listing equals its id-sorted sequence — the ordering claim is pinned directly, not inferred from a
walk that a lucky engine would pass either way. Verified by dropping the `id` key: the test goes red.
Rejected: keyset paging on `(created_at, id)`. It is the better long-term answer, but it changes the wire
contract to a cursor, and this response already shipped `limit`/`offset` inside `PageMeta`. The trade is
recorded deliberately: `OFFSET` degrades on a very large roster, and a cursor is the upgrade to make if a
tenant ever gets there.

*Scope line.* `FoldersRepository.list_for_owner` stays unpaged. It is user-authored, small, and on no
paged route, so its page size *is* its total and the number is honest. Bounding it now would add a `Query`
surface and migration risk for no consumer. The rule is that a read gets a window when a **client** can
grow it, not when a docstring says it is large.

## D-099

*The demo identity provider is an authentication path and must obey the tenant-access seam.* NEX-006
closed the offboarding gap on the three human identity paths; NEX-017 closed it for agent-token
resolution (a fourth). The demo provider is a **fifth** and was not counted, so it selected a user after
checking only `is_active` and never the membership rule. A suspended or deprovisioned membership kept
authenticating in development.

*Why "only in development" is not a mitigation.* The demo provider is a live authentication path in every
dev and test deployment, and the component tests run against it (`tests/conftest.py`'s `client` is the
demo-auth app). A rule enforced on four paths and skipped on the fifth teaches the test suite, and anyone
reading it, that losing a membership does not end authenticated access — the exact model NEX-006 exists to
reject. The `tenant_access.py` docstring already claimed the seam was "shared by every path that
establishes an identity"; the honest fix was either to make it true or stop saying it. We made it true.

*Reuse the seam, do not re-derive it.* `demo_provider` calls `ensure_tenant_access`, the same use case the
OIDC, session, bearer and agent paths call, rather than reading memberships itself. A second copy of the
rule is how the paths drifted apart in the first place.

*Reconciling two tests, and why it is not weakening them.* `test_suspend_removes_all_authority` and
`test_reactivate_does_not_revive_a_deprovisioned_member` asserted a 404 for a caller who had just lost
their *only* membership. They were green before only because the demo path let that caller stay
authenticated past the boundary. Under the corrected rule the caller gets 401 at authentication, which is
what the bearer and session paths already do and what the tests in `test_tenant_access_bearer.py` assert.
Changing the expected status to 401 would have quietly redefined those tests from "the org is invisible"
to "the caller is signed out" — a different, weaker property. Instead each test now gives Bob a *second,
active* membership, so he remains authenticated and the original 404/422 assertions test the property they
name. The assertions are unchanged; only the caller's other tenant was added.

*Load-bearing, measured.* Reverting only `demo_provider.py` on this tree turns exactly the four refusal
tests in `test_tenant_access_demo.py` red and leaves both positive controls green. The positive controls
(active member, unaffiliated user) are the reason the file cannot pass by refusing everyone: they pin
`UNAFFILIATED` → bootstrap-allowed and `ACTIVE` → authenticated, which is the same shape NEX-017 decided.
