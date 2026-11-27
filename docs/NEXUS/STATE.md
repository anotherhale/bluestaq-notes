# NEXUS State

Snapshot of where the platform is. Git and repository evidence are the source of
truth; this file records intent and reconciliation.

## Current position

| Field | Value |
| --- | --- |
| Project phase | Phase 2 **complete**. Phase 5 (durable runtime): **NEX-001…NEX-046 VERIFIED.** Phase 3 (governance): NEX-047 / NEX-048 / NEX-049 / NEX-050 merged to `main` via PR #3 (`4d57f39`). **NEX-051 / NEX-052 / NEX-053 VERIFIED** in PR #5. **NEX-054 VERIFIED** (`1878cd1`): the `redaction` obligation has a real output-filtering adapter. **NEX-055 VERIFIED** (`c55fb9f`): the `enhanced-audit` obligation now records evidence sealed into the hash chain, so it is satisfiable rather than silently ignored. **Remaining gaps, named:** `step-up`, `second-approver` and `rate-limit` still fail closed by refusing the call (honest, but inexpressible), plus risk-based approval. |
| Branch | `feature/agentic-enterprise-platform` |
| Starting commit (run 56) | `691f9c8` — read from `git ls-remote` at run start, not from a stored field. |
| Head commit (measured) | Product commit `c55fb9f` (NEX-055), pushed and verified by an independent `git ls-remote` after the push. The docs commit that writes this file descends from it; read the live head with `git rev-parse HEAD`, never from a SHA recorded here. |
| Last CI result | **`c55fb9f` (NEX-055): Backend (ruff, mypy, pytest, migrations) and Frontend (typecheck, lint, test, build) both `completed/success`**, polled 2026-11-27T17:31Z. Previous anchor `1878cd1` (NEX-054) also green. |
| Tracking issue | #2 (open) — last reported NEX-053. **Delivery debt: NEX-054 and NEX-055 not yet reported.** The issue stays open: the programme is not done. |
| Draft PR | **#5 (unmerged)** — *Enforce published governance policy at the tool gateway*, `feature/agentic-enterprise-platform` → `main`, mergeable. Body carries NEX-052 and NEX-053 evidence. **Delivery debt: the NEX-054 and NEX-055 sections are not yet appended.** Automated merging is prohibited; a human reviewer decides. |
| CI status | `ci_status` in `CHECKPOINT.json` is the authority — per-slice results with timestamps. Standing rule: a docs commit reads `completed/cancelled` once superseded in the concurrency group — never evidence. A **concluded** run is never retracted, which is why evidence anchors to one. |
| Authorized to merge to `main` | No — automated merging is prohibited |

## NEX-049 PUSHED (run `NEX-2026-11-27-52`) — the lifecycle, behind a gate the router cannot skip

NEX-047 built the lifecycle and NEX-048 made it durable, but nothing outside a test could
reach it. `/api/v1/ops/policies` now offers `draft`, `publish`, `rollback` and `history`.

The design point worth recording is *where the authorization went*. The operator-scope
check lives in the application use cases (`DraftPolicy`, `PublishDraft`, `RollbackPolicy`,
`GetPolicyHistory`), not in the router. A gate in the router would be a second copy of an
authorization rule, and the second copy is always the one that gets forgotten when the
next caller arrives. Putting it in the use case means an agent, a workflow or a CLI added
later is gated by exactly the code HTTP is gated by.

Scopes are **per operation** — `policy-draft`, `policy-publish`, `policy-rollback`,
`policy-history`. Drafting a policy is not publishing it. A single `policy-admin` scope
would have collapsed a lifecycle with a deliberate human checkpoint into one grant. The
API suite asserts the separation directly: a `policy-draft` grant gets `403` on publish,
and `404` on a policy it never drafted (existence is not disclosed to a caller who cannot
see it).

One boundary defect was caught by the tests and is worth naming. The router passed
`str(principal.user_id)` as `published_by`; the domain's `_validate_identifier` requires a
lowercase identifier starting with a letter, and a UUID starts with a digit, so publishing
failed with a `ValidationError`. The fix is one function at the boundary —
`_publisher_label` returns `user-<uuid>` — because a domain that validates an actor label
and a boundary that holds a UUID need exactly one place that knows both.

Status: IMPLEMENTED, TESTED, COMMITTED, PUSHED, **CI PASSED** at `22124f2` (4/4
`completed/success` on both the `push` and `pull_request` events, polled
2026-11-27T07:34Z). Reported to issue #2 (comment `7220681767`). PR #3 does not yet
describe this slice or NEX-048 — that is the remaining delivery debt.

## NEX-048 PUSHED (run `NEX-2026-11-27-50`) — a published policy that a restart cannot empty

NEX-047 gave the platform a policy model, but stored it in a process-local dict. Every
deploy therefore started with **no published policy at all**, which denies everything. That
is fail-closed, so it is not a vulnerability — but it meant policy could not survive the
process that wrote it, and a governance surface you must re-publish on every restart is not
a governance surface an operator can rely on.

`SqlAlchemyPolicyVersionRepository` is a second adapter behind the **unchanged**
`PolicyVersionRepository` port. No use case, no port, and no caller changed; the in-memory
adapter stays for tests, and it keeps naming itself `in-memory` so a test that accidentally
depends on durability is visible.

Three things are worth knowing:

- **Decoding re-validates.** `policy_codec.py` re-runs every domain validator when a row is
  read. A corrupt or hand-edited row raises `ValidationError` and logs a WARNING carrying
  the policy id and version. The alternative — skipping validation on read because "we wrote
  it" — would let a damaged row degrade into a *weaker* policy silently, which is the one
  failure mode an auditor must never have to discover by accident.
- **The invariants live in the schema, not only in the use case.** `UNIQUE
  (policy_id, version_number)` stops two authors claiming `v4`; a partial unique index on
  `(policy_id) WHERE status = 'published'` keeps at most one version of a policy in force.
  `PublishDraft` already retires the predecessor before publishing, but a caller that
  bypasses the use case cannot corrupt history through SQL.
- **No backfill.** Nothing in the in-memory adapter was durable, so there is nothing to
  migrate. The migration only creates the table and its indexes.

Validation on the committed tree: **1246 passed, 4 skipped**; architecture layering test
13 passed; `ruff check` and `ruff format` clean; `mypy` clean over 134 files; `alembic
upgrade head` applies `f7b3c0d95a12 -> 4b1e7a9c3f28` on a fresh SQLite database with both
indexes present, and `alembic heads` reports a single head.

**Honest status:** IMPLEMENTED, TESTED, COMMITTED, PUSHED at `810d1dc`, and CI on that
commit is **4/4 `completed/success`** (polled 2026-11-27T13:47Z, Backend + Frontend on both
the `push` and `pull_request` events). The push landed at this run's budget, so the poll was
done afterwards and the result is recorded rather than assumed.

**Still open after this slice:** there is no HTTP surface (NEX-049), `approve` is still
absent pending separation of duties (NEX-050), and the migration is SQLite-verified only —
the Postgres path is exercised by CI's migration job, which has not yet seen this revision.


## M3.1 OPEN (run `NEX-2026-11-27-50`, NEX-047) — a policy model that cannot be talked into executing

Phase 3 had no policy surface at all. Authorization lived in use cases, which is fine
for a handful of rules and cannot answer "what was in force when this decision was
made". NEX-047 adds the model, its lifecycle and its history.

**The AST is closed by construction.** `Comparison`, `AllOf`, `AnyOf` and `Not` over a
fixed operator vocabulary. There is no string to `eval`, and no fifth node type a
caller can invent. That is the security property, and it is structural rather than
enforced by review: a policy cannot become code because there is nowhere to put code.

**Three semantics are pinned, because these are the ones that go wrong quietly.**

*Missing attributes never satisfy a condition, under every operator including `!=`.*
A policy requiring `classification != secret` must not pass an unclassified resource,
and the natural reading of `!=` gives exactly the wrong answer. Pinned by a
parametrised test across all four operators, not one.

*A wrong-typed value raises rather than denying.* A silent deny would hide a data
defect in the one place an auditor most needs to see it. A missing attribute is the
different case and denies; a value of the wrong type is a bug and says so.

*Explicit deny overrides allow, and an empty or non-matching set denies.* Stated once
in `evaluate`, tested in both rule orders so the answer cannot depend on insertion
order.

**Publishing is one operation, and its write order is the safety property.** The
predecessor is retired **before** the successor is published, so a crash between the
two writes leaves *nothing* published — which denies — rather than two versions in
force. Exactly one version per policy id is ever in force. The store assigns version
numbers, so two authors cannot both claim v4.

**Rollback is a new release, not a rewrite.** `rollback` reads the named version's
rules and drafts them under the next number. The originals stay readable and the fact
that a rollback happened is itself in the history. Rewriting would erase the evidence
that anyone had tried the other thing.

**Deliberately absent: `approve`.** The full lifecycle includes approval, and it
depends on separation of duties (M3.3), which is not built. A gate that always passes
would be a policy surface that looks governed and is not — worse than an absent one,
because the absence is visible. Recorded as a residual rather than faked.

**Recorded gaps.** The adapter is in-memory and reports `name == "in-memory"`, so a
reader cannot mistake it for durable storage; a restart forgets every published
version. There is no HTTP surface yet. Both belong to the next governance slice, and
neither should reimplement the lifecycle.

**Status: VERIFIED, not merely pushed.** Local gates green (ruff format and check clean,
mypy 132 files clean, pytest 1228 passed / 4 skipped), CI 4/4 `completed/success` at
`379a98a`, issue #2 comment `7219198697` posted and read back, and the PR #3 section
appended and read back with exactly one NEX-047 heading. The product commit is `379a98a`;
the state that records it is `16bab4b`.

## R40 CLOSED (run `NEX-2026-11-27-49`, NEX-046) — a grant that outlives the process

NEX-045 made an operator permission a record with a lifetime and a holder, but the
only adapter was a dict. A restart forgot every bounded grant, and each worker in a
multi-worker deployment held its own copy, so a grant issued on one worker was
invisible to the others. R40 recorded that precisely and rated it low: it failed
closed, so it was never a security hole. But a grant nobody can see is a grant
nobody can audit, which is the question NEX-045 exists to answer.

**The port did not change.** `OperatorGate` and `ListOperatorGrants` depend on
`OperatorGrantRepository`, not on a store. That is what the port was for, and this
slice is the test of whether it was worth having: the store swapped and neither
caller moved.

**Two port promises, and how they survive a table.** `active_grants` must return a
stable order, so ordering is by a surrogate autoincrement `id` and never by
`granted_at` — two grants in one seed share a timestamp, and sorting by it would
make the answer implementation-defined. `add` must be idempotent, so
`(user_id, scope, granted_at)` is unique and `add` updates in place: a re-issued
grant extends its deadline instead of duplicating, and a seed that runs on every
boot cannot accumulate one grant per restart.

**`replace_configured` is the load-bearing change, and it exists because of the
store.** With a dict, re-seeding on boot was harmless because the dict was empty.
With a table it is not: a seed stamping `granted_at` from the boot clock is a *new*
grant on every restart, so the operator count would grow by the whole configuration
each time while nobody was granted anything. The seed path now reconciles instead —
grants that came from configuration (`granted_by is None`) are replaced, and grants
a human issued are left exactly as they are. A bounded incident grant must not be
erased by a redeploy, and configuration owns what configuration issued and nothing
else.

**Expiry stays a domain decision.** `active_for` and `active_grants` both filter
through `OperatorGrant.is_active` rather than in SQL, so the validity rule is
evaluated in exactly one place no matter which adapter answered. Filtering in the
query would duplicate the rule and let the two drift — the same shape as R29, where
a bound written by one clock and read by another was wrong in both directions.

**What is tested.** 23 persistence tests: a restart (a second adapter over the same
file sees the grant), cross-adapter visibility, insertion order, both expiry
boundaries, `not_before`, per-user scoping, uniqueness, re-issue extending a
deadline, `replace_configured` idempotency across restarts and its refusal to touch
human-issued grants, the empty-scope CHECK, the user foreign key, cascade delete,
and that the table exists in the migrated schema. Migration `f7b3c0d95a12` has a
single head, a correct parent, and no model drift.

**Honest limits.** Four PostgreSQL-marked tests are skipped locally, so the
`length(scope) > 0` CHECK and the `UTCDateTime` round trip have not met a
PostgreSQL server directly. CI at `0e86304` is 4/4 green and its Backend job does
run the migration path, so the schema is exercised there; what CI does not do is
run those four PostgreSQL-marked tests, and that gap stays recorded rather than
reported as closed.

## R36 CLOSED, second half (run `NEX-2026-11-27-48`, NEX-045) — a stored grant

NEX-044 narrowed each grant but left two holes that no narrower environment
variable could close. A grant could not **expire**: revoking an incident grant
meant editing configuration and redeploying, so a temporary grant was permanent in
practice. And nobody could **see who holds what**: a config value holds strings,
not holders, so an audit of the privileged surface could not answer a question
about itself.

**The record.** `OperatorGrant` is a user, a scope, a `granted_at`, an optional
`not_before`, an optional `expires_at`, and an optional `granted_by`. `None` for
`granted_by` is an honest "configuration issued this", not a missing field.
Validity is fail-closed at both ends: dead at the instant it expires
(`now >= expires_at`), and dead before it was issued or before its `not_before`.
A window that has not opened is not a permission, and a deadline is a deadline
rather than an approximation of one. The constructor rejects the mistakes that
would otherwise look like working grants until the first check: an empty or
whitespace-padded scope, a deadline at or before issue, an empty validity window,
and a `str` where a `UserId` belongs.

**The gate reads the store.** `OperatorGate` takes an `OperatorGrantRepository`
instead of a dict. It reads `now` once from its own clock and threads that value
through both the permission check and the access-log entry, so the two cannot
disagree about "now" — the R29 failure mode, in miniature. A grant that lapses
mid-process stops working with no restart and no configuration change. Refusal
still happens before recording, so an access entry remains evidence of
authorisation rather than of interest, and that invariant is pinned against the
new store.

**Configuration became a seed path.** `OPS_OPERATOR_USER_IDS` still describes a
deployment's *initial* operator set, so nothing an existing deployment relies on
changes. It now seeds one unbounded grant per configured scope. A deployment that
wants a grant to lapse issues it through the store rather than encoding an expiry
in an environment variable — bounded grants exist so they can be given out for an
incident and then be gone.

**The read side.** `ListOperatorGrants` reports every holder with at least one
active scope, the scopes they hold, and the earliest deadline among their grants —
or `None` when one grant is unbounded, because the strongest grant a holder has is
what describes how long they keep access. It is gated by the same scope as the
access report and recorded when granted, so enumerating holders is auditable too.
Exposed at `GET /api/v1/ops/operator-grants`; a non-operator gets `404`.

**Tests, which the slice did not previously have.** Expiry is the whole point and
was untested. Added: 17 domain tests for the lifetime boundary and the
construction rejections, 13 application tests for expiry at the gate mid-process,
no-entry-on-refusal and the holder-list folding, and 4 API tests for the route,
schema and 404 behaviour.

**Residual — R40.** The store is process-local. A restart forgets a bounded grant,
and each worker in a multi-worker deployment holds its own copy, so a grant issued
on one worker is invisible to the others. This is the same shape as the access log
and is honest for a demo adapter: `source_name` reports `in-memory` so a reader
cannot mistake it for durable. The fix is a durable adapter behind the existing
port; the gate and use case do not change. It is not a security regression — a
lost grant fails closed.

## R36 CLOSED, governance half (run `NEX-2026-11-27-48`, NEX-044) — a grant that cannot be narrowed must be handed out whole

The operator allowlist was a single flat set of user ids. Everyone on it could read
every platform-wide view: advisory withholding totals, the audit-integrity sweep,
and the operator access report. That is not a convenience, it is a
privilege-escalation shape. The engineer who reads withholding rates to tune a
threshold has no reason to read every tenant's audit trail, and the only way to
give them the one thing they need was to give them everything.

**Scoping.** A grant is now `<user-id>:<scope>`, and *a scope that is not named is
not granted*. `*` is the only token that means "all current and future
operations"; it is explicit and greppable, so a deployment that granted it can be
found by reading configuration instead of inferring it from an operator who
happens to pass every check. Nothing grants `*` by default. Entries accumulate, so
one operator can hold two scopes across two entries without a list syntax.

**An empty scope set is not an operator.** A listed id with no scopes admits
nobody, and `operator_ids` reports only ids with at least one scope. Reporting an
id that can read nothing as an operator would overstate the allowlist, which is
the one number an operator audit is supposed to be able to trust.

**Refusal stays 404.** A wrong-scope caller is refused exactly like an unlisted
one. A 403 here would confirm two facts an ordinary user has no need for: that the
endpoint exists, and that the caller is *some* kind of operator. The refusal still
happens before the access log is written, so an entry remains evidence of
authorisation rather than of interest.

**One parser, so "validates" means "parses".** The field validator and the
`operator_scopes` property call the same function. Two implementations would let a
value pass validation and then parse differently — the classic way an allowlist
becomes subtly wrong.

**A defect the tests caught, which is the point of writing them first.** `UserId`
subclasses `uuid.UUID`, whose constructor takes a hex string, not a `UUID` object.
The parser passed a `UUID`, so *every valid grant was rejected as a non-UUID id*.
This is fail-closed, which is the right direction, but it would have locked every
operator out at startup with a message blaming their configuration. The bootstrap
test asserted that a valid grant **parses**, not merely that invalid ones raise;
an assert-the-error suite would have passed straight through this.

**Residual, recorded rather than closed.** Expiry and holder visibility are still
missing. Both need a stored grant — user, scope, granted-at, expires-at,
granted-by — rather than a narrower string, so they belong to the next slice, not
this one.

**Tests.** A per-operation scoping block in `test_operator_gate.py` (including the
empty-scope-set case), `tests/bootstrap/test_operator_grants.py` for the
startup-refusal contract, and a `conftest.operator_grant(user, scope)` helper that
the three ops API test files now use so the scoped shape is exercised at the HTTP
boundary, not only in the unit.

## R36 CLOSED, read side (run `NEX-2026-11-27-48`) — a log nobody reads is not an audit trail

**The gap.** The previous run gave the operator allowlist a record: every granted read of a
platform-wide operational view appends an `OperatorAccessEntry` naming caller, operation and
moment. Nothing read that record back. `OperatorAccessLog.record()` had a caller and
`recent()` had none outside its own tests, so "who read the platform-wide totals" was still
unanswerable from inside the system — the write half of an audit trail with no read half. An
append-only file that no code path can open is storage, not accountability.

**The read side.** `GetOperatorAccessReport` (`application/use_cases/operator_access_report.py`)
returns a frozen `OperatorAccessSnapshot`. Exposed at `GET /api/v1/ops/access-report`, gated
exactly like every other ops endpoint, with `OperatorAccessEntryResponse` /
`OperatorAccessReportResponse` carrying the wire shape.

**Three decisions, each a correctness argument rather than a style choice.**

- **Gate first, then read.** The refusal path must not touch the log. If a non-operator could
  cause a read, the endpoint would leak the log's *size* through timing and would let anyone
  append load to a security control. Refusal happens in `OperatorGate.authorize` before
  `recent()` is called at all.
- **Inspecting the trail is itself a granted operator read, and is recorded.** The gate records
  before returning, so the read that follows is already attributable. That is deliberate: an
  audit-view access is exactly the kind of privileged read the log exists to capture. The
  consequence to state plainly is that reading the report grows it — the report a caller sees
  includes their own current read as the newest entry. Tests pin that, so it reads as intent
  rather than an off-by-one.
- **The snapshot carries `log_name` and `capacity`.** The only implementation is a bounded,
  process-local ring. A bare list of entries would let an operator read "nothing since 09:14"
  as a statement about the deployment when it is a statement about one process's ring buffer.
  Naming the log and its capacity makes the window's scope visible in the payload, and
  `truncated` reports that the window *filled* — not that entries were proven withheld, which
  the port cannot express.

**The limit is clamped, not rejected silently.** A caller-supplied `limit` is bounded into
`[1, MAX_LIMIT]`, so a request cannot ask for an unbounded response or for zero. The HTTP
layer returns 422 for an out-of-range limit rather than quietly substituting a default,
because an operator asking for 10,000 entries should learn the ceiling rather than believe
they received 10,000.

**Why the tests are evidence.** Nineteen new tests. The application level pins newest-first
ordering, the clamp at both ends, that the granted read is recorded, that a refused caller
leaves no entry, and that an empty allowlist admits nobody. The API level pins 404 (not 403)
for a non-operator, so probing cannot confirm the endpoint exists, and 422 for limits outside
the accepted range.

**Still open, unchanged.** The allowlist remains a static environment variable with no expiry
and no per-operation scoping. This slice makes the use of a grant *readable*; rotation and
scoping remain with the scoped-capability-grant slice. Reading is a prerequisite for rotating
anything responsibly, which is why it came first.

## R36 CLOSED (run `NEX-2026-11-27-47`) — one operator gate, and a record of who used it

**The defect.** `GetWithholdingReport` (NEX-037) and `SweepAuditIntegrity` (NEX-042) each
carried their own copy of the same allowlist check: compare the caller against
`OPS_OPERATOR_USER_IDS`, raise `ResourceNotFound` if absent. Two copies of a security gate is
already one too many — they can drift, and nothing would notice. Worse, neither recorded that
it had been used, so "who read the platform-wide totals" could not be answered from inside the
system at all. The only trace of a privileged read was whatever a log pipeline happened to
retain. That is the auditable half of R36, and it was the half that could be built correctly
now without inventing a capability system inside a security gate.

**The fix.** One `OperatorGate` use case (`application/use_cases/operator_gate.py`) behind an
`OperatorAccessLog` port (`application/ports/operator_access.py`), constructed **once** in
`build_container` and shared by both use cases. Every *granted* read appends exactly one
`OperatorAccessEntry` naming caller, operation and the moment.

**Two orderings are the security argument, not style.**

- **Refuse before recording.** A denied caller leaves no trace, so an entry in the log is
  evidence that a caller *was* authorised. A denial written into the same log would make it
  answer "who was refused" — a question nobody asked — while making "who read this" noisier.
- **The gate stamps the record with the request's own `Clock`, not wall time.** R29 already
  cost this programme once for exactly this: a bound written by one clock and read by another
  gives the wrong answer in both directions. A record timed by a second clock would disagree
  with the request that produced it.

**Why it is not an `AuthorizationProvider` decision.** That policy governs note *content*.
Platform-wide operational data must never become reachable as a side effect of content access,
so it stays a separate, explicit gate. The module docstring says so, because the next reader
will otherwise try to fold it in.

**What this does not solve, stated plainly.** The allowlist is still a static environment
variable with no expiry and no per-operation scoping. Rotation and scoping belong to the
scoped-capability-grant slice; this slice makes the *use* of a grant visible, which is the
prerequisite for ever rotating one. R36's governance gap is narrowed, not closed.

**Why the tests are evidence.** Fourteen tests pin this. Nine in `tests/application/test_operator_gate.py` pin the
gate and the adapter: an empty allowlist admits nobody; a non-operator is refused as
`ResourceNotFound` (so a probe cannot distinguish a forbidden endpoint from a missing path); a
grant records caller, operation and the gate's clock time; a refusal records nothing; repeated
grants each leave their own entry; the ring is bounded and newest-first; and a non-positive
capacity is rejected at construction. Two further tests in `test_withholding_report.py` prove
the ordering at the use-case boundary — a granted read appears in the log, a refused read does
not — and `test_audit_sweep.py` now drives the same shared gate, so the drift R36 described
cannot silently reappear in one of the two callers.

**Validation.** ruff format clean, ruff check clean, mypy clean (120 source files), `pytest`
**1079 passed, 4 skipped, 0 failed**. Product commit `1eb1d3b`, pushed and `ls-remote`-verified.

## R39 (run `NEX-2026-11-27-46`) — the lease could be acquired but never released

**Found by trying to be polite.** After the previous run's docs were pushed, it called
`lease release` to hand the lease back. It exited with `lease held by another agent`, naming
that run's own holder. The next run opened with the same lease still held — 1405 seconds
remaining, `holder_liveness: dead`, `mine: false`.

**The defect.** `release` and `renew` compare the live lease's `holder` against the caller's
holder to decide "is this mine?". The default holder was `host-pid`. Every `nexus_coord`
invocation is a fresh process, so the pid recorded at acquire time is never the pid doing the
release. `mine` is false on the very next call, and both verbs fail. A lease was single-use per
acquisition, so every successful acquire blocked the next run for the full TTL.

**Why R38 was not bad luck.** R38 was recorded as "a stale lease held by a dead process". It
was the predictable output of a run that acquired and could not hand back. The two defects are
one defect; fixing this one removes the source of the other.

**The fix.** The holder must be identical across every process of one run and different between
two concurrent runs on one host. A pid satisfies the second and fails the first. The run id
satisfies both but is a briefing value passed by hand — coordination would then depend on a
human remembering a flag. The POSIX **session id** is exactly the required identity: one
terminal session drives a run, every process it spawns inherits the session, and overlapping
runs have different sessions. `default_holder()` resolves `NEXUS_HOLDER`, then `host-s<sid>`,
then `host-<pid>` only where `os.getsid` is unavailable or fails.

**Why the tests are evidence.** The decisive property is stability *across processes*, which no
single-process test can express — two calls in one process share a pid and pass under the
broken implementation too. `test_the_holder_is_identical_across_separate_processes` spawns two
real subprocesses and compares their answers. Negative control on disk: reverting
`default_holder()` to `host-pid` turns exactly two tests red (the cross-process one and the
shape one); the file was restored from a backup and re-verified before commit.

A five-process acceptance run against a scratch repository with a bare origin confirmed the
whole cycle: acquire, then `status` in a *different* process reporting `mine: true`, then
`renew`, then `release`, then `held: false`. A sixth process with a foreign identity was still
refused, so the fix widened self-recognition without widening exclusion.

**Validation.** ruff format 249 files clean, ruff check clean, mypy clean (117 source files),
`pytest` **1068 passed, 4 skipped, 0 failed**. Commit `64759f4`, pushed,
`git ls-remote` confirms the remote head equals local `HEAD`.

**Honest limits.** The identity is only as stable as the session; a harness that spawns each
invocation under its own session (via `setsid`) reproduces the original defect, which is what
`NEXUS_HOLDER` is for. A recycled session-leader pid could make `holder_liveness` report
`alive` for a dead run — reporting inaccuracy only, because acquisition is gated on expiry,
never on liveness, so it cannot cause a false takeover.

## R37 (run `NEX-2026-11-27-44`) — the cap bounded parsing, not memory

**The defect.** `HttpDecisionProvider` refused a response larger than 64 KiB, but it checked
that bound against `response.content` — a body httpx had already read in full. The check was
correct and useless: the memory had been spent before the question was asked. A peer could
make the process hold an arbitrarily large body and the adapter would still call the answer
"oversized" rather than "never read".

**The fix, in three ordered rules.** The response is opened with `httpx.stream` rather than
`post`, and:

1. A declared `Content-Length` above the cap returns `MALFORMED_OUTPUT` before the body is
   touched at all.
2. `_read_bounded` consumes the stream in 8 KiB chunks, accumulating at most
   `_MAX_RESPONSE_BYTES + _READ_CHUNK_BYTES`, and returns `None` the instant the cap is passed.
   The `with` block then closes the stream instead of draining it.
3. The non-200 branch returns before any read, so a vendor error body — which can echo the
   request, an internal id, or the credential — never reaches the audit trail through a
   rationale.

**Why the tests are evidence and not decoration.** Three of the six new tests fail against the
old implementation, and they fail for the right reason: they instrument the *body object*, not
the parsed result. `test_an_unbounded_stream_is_abandoned_rather_than_drained` counts the
chunks the body was asked for; a drain would raise that count and fail. The announced-oversize
and non-200 tests use a body that raises if read at all. A test that only asserted
"abstains with MALFORMED_OUTPUT" would have passed on the broken code.

**What this does not fix.** The cap bounds size, not duration. That remains recorded in the
module docstring as an honest limit, not a closed one.

**Validation.** ruff format/check clean (178 files), mypy clean (115 source files),
**1027 passed / 4 skipped**. Negative control: reverting only the implementation turned exactly
the three streaming tests red and left the other 54 green.

## NEX-037 (run `NEX-2026-11-25-37`) — the count existed, the aggregate did not

R34 described a monitoring gap precisely: `WithholdingRecorder` counts every advisory and
every over-cap withholding across the process, `RecommendApprovalDecision` logs the running
total, and nothing exports it. Answering "how often are advisories thinner than they should
be" meant reading individual responses. A log line is not a number an operator can alert on.

This slice is the read side of that recorder, and the design question was not how to expose
the number but **who may see it**.

The aggregate spans every tenant. It is not a view of the caller's own data — it is a view of
the whole process — so exposing it to any authenticated caller would let one user infer
another user's advisory volume, and would make the endpoint a cross-tenant side channel that
grew more informative the more precisely it was polled. That ruled out the obvious shape
(a normal authenticated route) and ruled out reusing `AuthorizationProvider`, whose job is
note *content* access: platform operations must never be reachable by content access alone.

What shipped instead:

- `GetWithholdingReport` (`application/use_cases/withholding_report.py`) returns the snapshot
  to a caller on an explicit allowlist, and raises `ResourceNotFound` for anyone else.
- `OPS_OPERATOR_USER_IDS` gates it. **Empty by default, and empty means nobody** — the
  allowlist is a grant, not a role inherited by being logged in. An unconfigured deployment
  therefore exposes nothing, which is the correct failure direction for a setting nobody has
  thought about yet.
- A non-operator gets **404, not 403**. `403` would confirm that a platform-operations
  surface exists at that path, which is itself a small disclosure to someone probing the API.
- `GET /api/v1/ops/withholding-report` returns counts only — `advisories_total`,
  `over_cap_advisories_total`, `over_cap_fields_total`, `over_cap_rate`. No field name,
  value, note id or user id. That is why a single process-wide number can be disclosed at all
  without disclosing content: the counts are about *withholding*, never about what was withheld.
- `over_cap_rate` is reported beside its numerator and denominator, because `0.0` over three
  advisories and `0.0` over thirty thousand are different claims and a bare rate conflates them.
- Malformed operator ids fail **at startup**, not at request time. The gate compares `UserId`
  values, so a typo would silently match nobody and the endpoint would look broken for the one
  operator it was meant to serve — a failure that is hard to distinguish from a permissions bug.

Seven tests cover the gate at both layers: three at the use case (operator reads totals; empty
allowlist denies; a listed-other-user is refused as not-found) and four at the HTTP boundary
(the route, schema and container wiring agree; empty allowlist admits nobody; a non-operator
sees 404 rather than 403). Both layers matter here — the use case raising `ResourceNotFound`
and the router *not* remapping it are separate claims, and only an HTTP-level test pins the
second.

Status: **VERIFIED** — CI at `5c0575b` is 4/4 `completed/success`, every check run reporting
`head_sha` `5c0575b5df0b2aa30e1d4a28f622ac327871f902`, polled 2026-11-25T18:57Z. Two earlier
polls (18:52Z, 18:54Z) saw the same runs still `queued`/`in_progress`, which is why the claim
carries a timestamp at all. Local gates: `ruff format` clean (169 files), `ruff check` clean,
`mypy` clean over 109 source files, full `pytest` **920 passed / 4 skipped**.

One arithmetic caveat, recorded rather than smoothed: NEX-036 recorded 916 passed and this
slice adds 7 tests, which predicts 923. The measured number is 920. NEX-036's 916 was written
without a measured baseline, so the discrepancy is most likely in the prior figure — but that
is not proven, and 920 is what the suite actually reported.


## NEX-036 (run `NEX-2026-11-25-37`) — the caller was audited, and the advisory outlived its question

The previous run's `next_action` asked for a caller audit on one question: does any
production caller ignore a `DecisionEvaluation` and proceed, silently turning "the provider
could not judge" into "allowed"? The premise was checked against the code first, because the
two preceding runs had both left stale `next_action` fields.

`RecommendApprovalDecision` is the only production caller. It was probed end to end through
the real use case with a stub provider, before any fix:

- A **pending** request returns score `0.5` and its status is still `pending` afterwards.
  Advice never decides.
- A **`DISABLED`** provider returns score `None` with reason `DISABLED`. The abstention
  reaches the caller; nothing swallows it. This is the fail-closed property the audit existed
  to test, and it held.
- The subject's content does not travel: the note title is withheld by the classification
  ceiling and named in `excluded_context_fields`, distinct from `over_cap_context_fields`.
- **An `APPROVED` request produced an advisory. So did a `REVOKED` one.**

That last finding is the defect. The use case's own docstring and the route summary both
described advice about a *pending* approval, and the code asked only whether the caller owned
the agent. A score attached to a settled request invites a reviewer to read it as the opinion
that produced the decision, and a later reader — an auditor — to read it as one that informed
it. In an approval trail that is precisely the wrong inference, and it is the kind a
regulator is entitled to draw from the record. It also spent provider time and a withholding
count on a question nobody could act on.

The fix is a precondition, not a transition:

- `RecommendApprovalDecision` refuses with `409` when the request is not `PENDING`, or when
  its validity bound has passed. Nothing is mutated, so the check belongs in the use case
  rather than the aggregate, whose guards are written for state changes.
- Expiry is judged with the use case's **own clock** — the one that stamped the bound — never
  the wall clock. R29 is the same defect in a different place, and a single-clock test suite
  cannot see either.
- The docstring and route summary were corrected to state the 409, so the contract the code
  keeps and the contract the prose claims are now the same contract.

Five tests pin it: four refusals (approved, denied, revoked, expired-under-the-advising-clock)
and one asserting the live case still returns an advisory, so the fix cannot pass by refusing
everything. The four refusal tests were verified to **fail** with the guard removed — the
negative control was run on disk, then the file was restored from a backup and re-verified
line by line, because a heredoc in this environment corrupted the command that ran it.

CI was polled at `90eea0c` and is green: Backend `completed/success` (id 139429822539) and
Frontend `completed/success` (id 139429823232), one complete pair, 2026-11-25T18:45Z. A
duplicate Backend run reads `completed/cancelled` because the concurrency group killed the
superseded event run — neither pass nor fail, and not counted. The product commit was
pushed before the docs commit so the evidence names `90eea0c` rather than a docs head.

## NEX-035 (run `NEX-2026-11-25-36`) — the adapter was policed on its answer and trusted on its name

R30 asked for a conformance suite so the first real vendor adapter would not be the first
genuine test of the decision contract. The suite is written, and writing it found that the
contract had a blind spot: `EvaluateDecision` guarded everything a provider *answered* — the
rationale length, the confidence range, the choice set — and guarded nothing about what a
provider *called itself*. An adapter implements both `decide` and the `name` property; only
the first was treated as third-party code.

Five probes against the seam, run before any fix, established the defects rather than
assuming them:

- `provider_name` accepted 580 characters although `MAX_PROVIDER_NAME_LENGTH` is 80. The
  constant was declared and never read — dead code that looked like enforcement, which is the
  most misleading kind of missing control.
- An over-long `rationale` raised `ValidationError` inside `decide` and surfaced as
  `PROVIDER_ERROR`. Operationally that sends an operator to check connectivity when the
  payload is what violated the contract.
- A `name` property that raised propagated a `RuntimeError` out of `run`, breaking the module's
  own rule 2 ("a provider failure is an abstention").
- A non-`str` name was carried into the record unchanged — `provider_name` came back as
  `b'bytes-not-str'`.
- `name` was read once per run, which is the correct count; the probe pinned it so a future
  refactor cannot quietly make the name disagree with itself across a single evaluation.

The fixes are small and sit at the seam, not in the domain contract:

- `validate_provider_name` in `domain/agent/decisioning.py` checks the bound it always
  declared, plus the shape the value must have. A provider name lands in audit records,
  structured log fields, and API responses, so it must be a lowercase identifier — a newline
  in it is a log-injection vector, not a cosmetic wart. It raises rather than coercing: a
  silently truncated name would record a *different* provider than the one that answered.
- `_safe_provider_name` in the use case reads the name **once**, before anything else can
  fail, and degrades to `UNIDENTIFIED_PROVIDER` on a raise or a non-identifier value. Reading
  it lazily inside `_evaluation` would also have read it on the disabled path, where the
  provider was never called and its name may not resolve.
- `ValidationError` raised through `decide` is now classified `MALFORMED_OUTPUT`, caught
  ahead of the broad `except Exception` that would otherwise have reported it as a vendor
  outage.

The suite itself (`tests/agent/test_decision_provider_conformance.py`, 30 tests) is the R30
deliverable: `assert_provider_conformance` is the contract a real vendor adapter is run
through, and the meta-tests run a deliberately hostile fake that breaks one rule at a time and
assert the suite *rejects* it. A conformance suite that has never been shown to fail is not
evidence of conformance.

Status: **VERIFIED** — CI at `12f6fa9` is 4/4 `completed/success`, every check run reporting
`head_sha` `12f6fa9dc29fd67d4d4c2dc884535e7041aaf2ec`, polled 2026-11-25T18:14Z. Local gates:
`ruff format` clean, `ruff check` clean, `mypy` clean over 107 source files, full `pytest`
**911 passed / 4 skipped** (baseline 881; the delta is the 30 new tests).

## NEX-028 (run `NEX-2026-11-25-31`) — a hash of a secret is still a fact about the secret

NEX-027 closed by sending a SHA-256 of the context instead of the context, and called that a
declassification boundary. It is not one, and the difference matters. A digest is a
**fingerprint**: for low-entropy content — an email address, a note title, a two-word subject —
anyone holding the digest can confirm a guessed value by hashing it. Sending the hash of a
confidential field therefore still discloses a fact about that field. Hashing the *raw* context
made this invisible, because the code looked like it had already answered the question.

Context assembly is the half of M2.6 that answers it properly. One domain module,
`domain/agent/context.py`, now owns what may enter an outbound judgment. Context is gathered
into a `ContextEnvelope` of named `ContextField` values, each labelled with a
`DataClassification`; the envelope's hash is computed over **only** the fields at or below the
envelope's ceiling. A field above the ceiling is absent from the canonical input entirely, so
it can neither influence the digest nor be confirmed from it. The default ceiling is `INTERNAL`:
internal references may inform a judgment, the data subject's confidential material may not.

Three properties are structural, not conventions:

- **`RESTRICTED` cannot be named as a ceiling.** It is a construction error, so there is no
  runtime branch to get wrong and no configuration under which restricted content reaches an
  outbound digest.
- **Exclusion is recorded, never silent.** `excluded_field_names` names what was withheld —
  names, never values — and travels through `DecisionRequest` into `DecisionEvaluation`, so the
  audit record can say the provider was asked about less than the platform held. A silent drop
  would make "we asked with less" indistinguishable from "there was nothing more", which is the
  exact failure the slice exists to prevent.
- **Fields are named and bounded.** Names are stable lowercase identifiers so telemetry can
  aggregate them; values cap at 200 characters and fields at 32 per envelope, so a document
  cannot be smuggled in under one name.

`DecisionRequest.from_context` is the only sanctioned route from an envelope to a question,
because it is what guarantees the hash the provider sees was computed over content an envelope
approved. The one honest gap, documented in that method: `context_hash` is still a plain `str`,
so a caller *can* hand-assemble one — and doing so means taking the declassification decision
personally. A future slice can close it by narrowing the field to a type only an envelope can
construct.

Status: **VERIFIED** on observed CI at `a3d1b32`: **4/4 `completed/success`**, every check run
reporting `head_sha` `a3d1b3214fa990aab9abe2f93cdef642bde8c882`, polled 2026-11-25T14:42Z —
both the push and the pull_request run. Local gates green: `ruff format` clean, `ruff check`
clean, `mypy` clean over 104 source files, full `pytest` **824 passed / 4 skipped** (baseline
794; delta is exactly the 30 new tests — 28 in `tests/agent/test_context_envelope.py`, 2 added to
`tests/architecture/test_decision_provider_neutrality.py`). Negative control: making
`included_fields` ignore the ceiling turns three tests red, including
`test_confidential_value_cannot_influence_the_digest`, which asserts the property directly by
requiring two envelopes differing only in a confidential field to hash identically.

**R31 (new, low).** `ContextEnvelope` is exercised only by tests so far. The first production
caller (tool-gateway classification or approval recommendation) will be the first to test
whether the 200-character value cap and 32-field cap fit real content; if either is too tight,
widening it is a contract change.

## NEX-027 (run `NEX-2026-11-25-30`) — judgment that cannot become authority

M2.6 listed "decisioning and context assembly" as the two things it still needed. This slice
is the first half: the platform can now ask a judgment service a narrow question and get an
answer it can reason about, with no path from an answer to a permission.

The design problem is not "call a model". It is that a model-driven system's most common
security failure is letting an opinion become an authorization. Three properties are
structural rather than conventional, and each is enforced by the shape of the types:

- **A decision grants nothing.** `DecisionRequest` and `Decision` have no field that can name
  a permission, a capability, a principal, or a resource scope. There is nothing to *hold* an
  authorization in, so a decision cannot be mistaken for one. The decisioning modules do not
  import the authorization provider and cannot reach it — asserted by an AST test, not by
  review.
- **Every answer is bounded.** A choice must be one of the candidates the caller supplied; a
  score and a confidence are floats in `[0.0, 1.0]`; the rationale is capped at 400
  characters. There is no free-form output channel, so a provider cannot smuggle an
  instruction back into the application.
- **Abstention is a first-class outcome, not an error.** A provider that is disabled, unsure,
  slow, or malformed produces `ABSTAINED` with a named reason. Failing closed is expressed by
  *not deciding*, never by deciding "allow" — which is why there is no binary approve verdict
  anywhere in the vocabulary.

`EvaluateDecision` is the single route to a decision, so four rules apply to every provider:
the flag off abstains without calling the provider at all; a raised error or a non-`Decision`
payload becomes an abstention; `validate_against` discards an answer of the wrong kind or a
choice the caller never offered; and confidence below the caller's threshold abstains. The
threshold defaults to `0.5` and the stub adapter answers at `0.1`, so **enabling the flag
alone does not start handing out opinions** — the stub abstains under any real threshold.

`DecisionRequest` carries a SHA-256 of the context, never the context. Raw note text is not
handed to a third party by default, so there is nothing to retain, leak, or inject into. That
is also the seam context assembly will use next: it will decide *what* to hash.

Two smaller choices worth recording. The stub's choice answers use `min(candidates)` rather
than "first", so the result does not depend on input ordering — a stub whose answer changes
when you reorder a list is a flaky test generator. And `approval_recommendation` is a *score*,
not a yes/no, precisely so that no caller can read an advisory number as consent; the human
approval workflow remains the only thing that can approve an action.

Status: **VERIFIED** on observed CI. Local gates green on `fb8d5bd`: `ruff format` clean,
`ruff check` clean, `mypy` clean over 103 source files, full `pytest` **794 passed / 4
skipped** (baseline 754; the delta is exactly the 40 new tests — 36 in
`tests/agent/test_decisioning.py`, 4 in `tests/architecture/test_decision_provider_neutrality.py`).
Negative control verified: planting `from jev_sdk import Client` in
`application/ports/` turns two of the neutrality tests red with the offending file named;
the probe was deleted and the file re-verified clean. **CI at `fb8d5bd` is 4/4
`completed/success`, every `head_sha` equal to `fb8d5bd`, polled 2026-11-25T14:18Z** — so
NEX-027 is VERIFIED on a run at its own SHA, not on descendant-CI.

**R30 (closed 2026-11-25, run `NEX-2026-11-25-36`).** The stub is the only adapter, so the
port's shape had been exercised against one implementation that always answers cheaply — the
400-character rationale bound, the `[0.0, 1.0]` ranges and the candidate choice-set had never
met a payload that tested them. NEX-035 shipped the conformance suite R30 asked for
(`tests/agent/test_decision_provider_conformance.py`), whose `assert_provider_conformance` a
real vendor adapter is run through, with meta-tests that assert the suite rejects a hostile
fake one violation at a time. Building it found the seam was policed on the *answer* and
trusted on the *name*: `MAX_PROVIDER_NAME_LENGTH` was declared and never read, a raising
`name` property escaped as a `RuntimeError`, a non-`str` name was recorded verbatim, and a
contract-violating rationale was misreported as `PROVIDER_ERROR`. All four are fixed. Closed
on VERIFIED evidence: 4/4 `completed/success` at `12f6fa9`, polled 2026-11-25T18:14Z.
Residual: the suite proves a *conforming* adapter behaves; it cannot prove a vendor's model
produces good judgments. That is an evaluation question, not a contract one.

## NEX-026 (run `NEX-2026-11-25-29`) — an approval must be judged against the clock that stamped it

The approval gate compared two different clocks. `ApprovalRequest.open` derives `expires_at`
from the timestamp it is handed, and every production use case passes its own `Clock` port —
but the decision path asked `is_expired`, which reads the module-level wall clock. So the
bound was written by one clock and read by another, and the answer is wrong in both
directions whenever they disagree:

- opened on a clock **behind** the wall clock, a live request is judged already lapsed and a
  human cannot decide it. The gate refuses consent it should accept.
- opened on a clock **ahead** of the wall clock, a lapsed request is judged still valid, so an
  approval outlives the bound it was issued under. Consent survives longer than it was
  granted for.

This is the failure mode the whole `Clock` port exists to prevent, and it was invisible for
one specific reason: every existing expiry test used the same clock for opening and deciding,
so the two readings always agreed. A defect that requires *disagreement* to appear is exactly
the kind a single-clock test suite cannot see.

The repository adapter was already correct — `SqlAlchemyApprovalRequestRepository` compares
`expires_at > now` on the caller's clock and treats NULL as expired. The defect was confined
to the aggregate's own decision path, which is why no integration test caught it either.

**The fix is subtraction, not addition.** `expired_at(*, now)` becomes the one honest
comparison: a single instant against a single bound. `is_expired` survives as the wall-clock
convenience for callers that genuinely have no clock, and now *delegates* to `expired_at`, so
the NULL-means-lapsed rule is written once instead of twice. `approve`/`deny` take the
caller's instant and use it for **both** the decidability check and `decided_at` — previously
those were two separate readings of the wall clock, so the recorded decision timestamp could
name a different instant from the one that was actually checked.

**What to carry forward (R29).** A validity bound is meaningless unless you can name the clock
that stamped it, and the same clock must judge it. The general question for any bound: *who
writes this, and does the reader use the same one?* A new expiry-like field that reads a module
clock instead of the caller's reopens this class.

Status: **VERIFIED.** CI observed at `b46e2e3`: **4/4 `completed/success`**, every check run
reporting `head_sha` `b46e2e3`, polled 2026-11-25T13:34Z. The docs commit `8742faa` pushed
afterwards did not disturb it — `b46e2e3` still reads 4/4 success while `8742faa`'s own runs are
in flight. Local gates: `ruff format` clean, `ruff check` clean, `mypy` clean over 98 sources,
full `pytest` **754 passed / 4 skipped** (was 748; the delta is exactly the 6 new tests in
`tests/agent/test_approval_clock.py`). Negative control: reverting only `entities.py` to its
pre-slice version turns 5 of those 6 red; the sixth asserts the wall-clock property and must keep
passing.


## NEX-020 (run `NEX-2026-11-25-22`) — a stale decision must not resurrect spent authority

`ApprovalRequestRow` had no `version`, and `save` rewrote the whole row from whatever the
caller had loaded. Two writers that loaded the same request both committed and the second
won — so a `DecideApprovalRequest` holding a pre-revocation copy wrote `approved` back
over a `revoked` row, and a stale copy of a spent approval cleared `consumed_at`, making a
single-use approval spendable again. Neither raised.

This is NEX-019's lost-update class on the aggregate where losing the update hands back
*authority* rather than progress, which is why it was treated as P0.

The aggregate now records `persisted_version` and bumps `version` on every
authority-changing transition — `approve`, `deny`, `revoke` **and `consume`**. The
`consume` bump is load-bearing: without it a stale save still matches the stored version
and clears `consumed_at`, so the CAS alone would not have closed the double-spend.
`save` is one `UPDATE ... WHERE id AND version = :expected` raising `VersionConflict`
(HTTP 409), and `consume()` increments the version in the same statement that sets
`consumed_at`.

Status: **VERIFIED.** CI observed **4/4 `completed/success` at `460a26a`**, every check run
reporting `head_sha` `460a26a` (polled 2026-11-25T10:16:40Z) — Backend (ruff, mypy, pytest,
migrations) ×2, Frontend (typecheck, lint, test, build) ×2. Promoted on that observation,
not on descendant-CI. Local gates at `460a26a` agree: `ruff format` clean, `ruff check`
clean, `mypy` clean over 98 sources, full `pytest` **730 passed / 4 skipped** (was 725; the
delta is exactly the 5 new tests), and the migration round-trips
(`upgrade → downgrade -1 → upgrade`).

Negative control: deleting the `version == expected` predicate turns 3 of the 5 tests red,
so they fail for the lost update and not for some unrelated reason.

## NEX-019 (run `NEX-2026-11-25-21`) — a stale write must not erase run progress

`AgentRun.version` was incremented on every mutation and never compared on write. Two
writers that loaded the same run both committed; the second overwrote the first's step
progress with a state derived from its stale copy, and neither caller was told.

For a note that is a lost edit. For a durable run it defeats the point: a stale copy
committing after a crash flips an `interrupted` step back to `pending`, so the next resume
re-executes a step whose effect may already have landed.

The aggregate now records the version it was last persisted at (`persisted_version`,
seeded on load), and `save` is a compare-and-set: one `UPDATE ... WHERE id AND version =
:expected`, `VersionConflict` on a rowcount of 0. `version - 1` could not have served this
role, because several mutations can happen between two saves. `VersionConflict` already
maps to HTTP 409, so the loser reloads and retries instead of assuming its step settled.

Status: **VERIFIED** — CI **4/4 `completed/success` at `66e8cd6`**, polled 2026-11-25T09:46Z
with every check run's `head_sha` confirmed equal to `66e8cd6` (Backend ×2, Frontend ×2).
Because the green run is at the commit itself, NEX-019 needs no descendant-CI reasoning —
unlike NEX-018. Local gates, run bare on the feature branch: 725 passed / 4 skipped, ruff
format and check clean over 198 files, mypy clean over 98 source files. Three tests in
`tests/agent/test_run_concurrency.py` include a matching-version success case, so an
always-raising guard cannot pass.

## NEX-018 (run `NEX-2026-11-25-20`) — erasing the seal laundered the verdict

`verify_chain` recomputed an entry's `outcome_hash` only when one was present, so
the cheapest possible forgery worked: rewrite a `denied` row to `succeeded`, null
`outcome_hash` *and* `completed_at`, and there was nothing left to recompute. The
forged chain verified clean. Reproduced before the fix (`verified: True`), and
rejected after it (`terminal invocation carries no outcome hash`).

The existing API test flipped `status` but left `outcome_hash` intact, so the
stale-seal branch caught it. The stronger forgery was never exercised — R18's
shape again: the test attacked a weaker threat than the control must survive.

Fix: a non-`started` entry carrying no seal is now a break. Only `started` may
honestly lack one, because its outcome is not known yet. `_STARTED_STATUS` is a
literal, not an import, to avoid a cycle with `domain.agent.tools`.

Status: **VERIFIED** on descendant-CI: `2b3beb7` is 4/4 `completed/success` and
contains the fix commit `789d78a` as an ancestor. `789d78a` itself has no check
runs — the following docs commit cancelled its run in the concurrency group, so
the evidence is labelled descendant-CI rather than "green at this SHA". Six domain
tests in `tests/agent/test_audit_chain.py`, including the honest unfinished-
`started` case, so the fix cannot "pass" by refusing everything.

## NEX-016 (run `NEX-2026-11-25-16`) — the approval gate was structurally unusable

A run that reached an approval-gated step could never resume. Two independent
defects each alone made it impossible:

1. `AdvanceAgentRun` caught `AccessDenied` and called `fail_step`; a failed step
   drives the run to terminal `FAILED`, so the approval about to be granted had
   nothing left to resume.
2. The gateway recorded the pause as a `DENIED` invocation under the step's own
   idempotency key. `(agent_id, idempotency_key)` is unique, so that denial
   permanently owned the operational key: the retry after approval replayed the
   denial rather than executing.

The fix separates waiting from failing: `ApprovalRequired` (an `AccessDenied`
subtype, so the 403 contract is unchanged) causes `Run.defer_step` to return the
never-started step to `pending` and the run to `PENDING`; the deferral is audited
under a `#awaiting-approval` sibling key so the operational key stays free; and an
already-open request for the same capability/resource is reused so repeated
advances cannot flood the approver's queue.

Why the suite was green throughout: every approval test covered one half of the
path (that a request is raised, that a decision is stored, that expiry and
revocation work). None covered approve-then-continue. See risk **R22**.

## NEX-017 (run `NEX-2026-11-25-17`) — a suspended delegator's agent kept working

NEX-006 closed the offboarding gap by calling `ensure_tenant_access` on the three
human identity paths: the OIDC callback, per-request session resolution, and the
bearer provider. **Agent-token resolution is a fourth way to establish an
identity, and it never called it.**

That matters because `_actor_principal` makes every tool call act *as the
delegator*. So suspending or deprovisioning a person left their agent fully
operational for as long as its token lived — the exact offboarding hole NEX-006
closed, reopened one layer up.

`ResolveAgentToken` now re-checks tenant access after its existing delegation
checks (expiry, revocation, agent suspension, capability subset) and refuses
`DENIED`. It reuses `resolve_tenant_access`, so the decision stays in the pure
domain rule rather than becoming a second copy. Two deliberate choices:

- **`UNAFFILIATED` is still allowed.** A brand-new identity must be able to
  bootstrap; the check refuses only a *denied* membership. Per-resource
  authorization continues to run as the delegator downstream.
- **The refusal is a pause, not a tombstone.** A test reactivates the membership
  and asserts the same token works again, so the check cannot be satisfied by
  permanently breaking the token.

The two tests drive the real path end to end (HTTP membership suspend, then token
resolve) and were observed failing before the fix — the resolve returned `200`
for a suspended delegator. A skip-based version that asserted nothing was
discarded rather than kept. See risk **R23**.

**Status: VERIFIED at `990557d`.** CI on that exact SHA is 4/4
`completed/success` (observed 2026-11-25T08:56Z, `head_sha` confirmed on all four
check runs), with local gates green at the same commit (716 passed / 4 skipped,
ruff format + check clean, mypy clean, architecture 5 passed). See risk **R23**.

**Next best actions**

1. **PR #3 body is current at `88926ec`** — 31,612 additions / 10 deletions / 111
   files / 118 commits, 757 tests, eleven migrations, eleven routers, all measured
   from the PR object at that head and verified byte-identical after the PATCH
   (25,010 bytes, refreshed 2026-11-25T12:53:30Z). It names `c367746` (NEX-023) and
   `eaaae23` (NEX-024) alongside the earlier verified commits. Re-measure from the PR
   object after any future push: a docs commit moves the head and the counters with it.
2. **NEX-003 and NEX-006 were promoted to VERIFIED this run** on a
   *descendant-CI* basis — see `CHECKPOINT.json` → `validation.evidence_basis`.
   Neither product commit (`e5f1bb4`, `4c5a6ef`) has check runs of its own; both
   are ancestors of `fa387a9`, where CI was observed 4/4 `completed/success`, and
   the acceptance set was re-run locally (239 passed).
3. **Select the next dependency-ready Phase-5 slice from `ROADMAP.md`.**

## Correction recorded in run NEX-2026-11-25-14 (NEX-013 status was overstated)

NEX-013 was recorded VERIFIED on green CI. Its core invariant was **not**
enforced on the path that matters:

- `ApprovalRequestRepository.add()` built `ApprovalRequestRow` without
  `expires_at` or `consumed_at`. Every approval the tool gateway opened was
  therefore persisted with `expires_at = NULL`.
- `is_usable` read NULL as "never expires", and the `consume()` predicate
  accepted `expires_at IS NULL`. So the 72-hour bound NEX-013 introduced was
  **inert in production**; an approved agent could replay a delete indefinitely.
- The NEX-013 expiry test stayed green because it exercised `save()` — which
  does write the columns — and never `add()`, the path the gateway uses.

This is the R18 class: a test that proves the aggregate's logic while the
adapter that stores it drops the field. Fixed in `12584a1` (three independent
fail-closed changes plus tests that pin the production path), and CI on
`12584a1` is observed 4/4 green. NEX-013 is **VERIFIED** on that evidence — the
criterion holds, and now so do the tests that check it.


## NEX-021 (run `NEX-2026-11-25-23`) — a stale write must not resurrect a stopped agent

This run finished the CAS audit that NEX-019 and NEX-020 started. The audit asked one
question of every repository under `infrastructure/db/`: does `save()` rewrite
authority-bearing columns from caller-supplied state, and is there a version to compare
against?

| Aggregate | `version` column | `persisted_version` | CAS on `save` |
| --- | --- | --- | --- |
| `Note` | yes | via `expected_version` arg | yes |
| `AgentRun` | yes | yes | yes (NEX-019) |
| `ApprovalRequest` | yes | yes | yes (NEX-020) |
| **`AgentIdentity`** | **yes** | **no** | **no — unconditional overwrite** |
| `DelegatedAuthorization` | no | n/a | writes 3 fields; revoke monotonic |
| `AgentToken` | no | n/a | writes `revoked_at` only; monotonic |
| `ToolInvocation` | no | n/a | writes verdict and seal together |

`AgentIdentity` was the last offender and the worst one, because its revocation *is* the
emergency stop. It already bumped `version` on every authority-changing transition —
`suspend`, `activate`, `revoke`, `narrow_capabilities` — and `save()` ignored it. The
version was maintained and then thrown away.

The harm: two requests can hold the same agent id at once (a human revoking in the control
centre while an unrelated edit finishes). With no compare-and-set the later commit won
silently — a stale copy wrote `status=active` and `revoked_at=NULL` back over a revoked
row, undoing the kill switch; and a stale copy taken before a narrowing restored the
removed capabilities, re-widening a ceiling the domain only permits to shrink. Neither
raised.

Fixed by giving `AgentIdentity` a `persisted_version` (seeded in `__post_init__`, advanced
by `mark_persisted()`) and making `save()` one `UPDATE ... WHERE id AND version = :expected`
that raises `VersionConflict` (HTTP 409) on rowcount 0 — the same shape as NEX-019 and
NEX-020. `add()` also calls `mark_persisted()`. No migration: `agents.version` has existed
since `c3f1a9d47e02`.

Status: **VERIFIED** at `e459f98` (CI 4/4). Five new tests in `tests/agent/test_agent_identity_concurrency.py`
drive the production `save()` path. Negative control run this session: deleting the
`version == expected` predicate turns 4 of the 5 red while the honest-write test stays
green, so the suite detects the lost update rather than asserting "always raise".

**Deliberately out of scope:** `DelegatedAuthorization`, `AgentToken` and `ToolInvocation`
need no version. Each writes a strictly monotonic field (`revoked_at`, a terminal verdict),
so concurrent writers converge on the same value instead of erasing each other's authority.
Recorded so a later audit does not re-open it without cause.

**Next action:** observe CI on the commit carrying this slice; promote to VERIFIED only on
4/4 `completed/success` at that `head_sha`.

## Active task

| Field | Value |
| --- | --- |
| Task | NEX-032 — withholding is observable with a reason per field |
| Status | **IMPLEMENTED, TESTED, VERIFIED, COMMITTED, PUSHED, CI PASSED** at `fe5eadc` (860 passed / 4 skipped; 4/4 `completed/success` at that SHA, polled 16:47Z). PR #3 and issue #2 updated. |
| Next action | NEX-033: aggregate over-cap withholdings into a count (monitoring slice). Decide structured-log vs metric port first; no new metrics dependency without a demonstrated need. |

## NEX-032 (run `NEX-2026-11-25-34`) — a withheld field that cannot say why is not a signal

NEX-031 made an over-cap record-derived value *withheld* rather than fatal, which is the right
failure direction: a legal note must not deny an advisory. But it made withholding the quiet
path. `excluded_field_names` recorded *that* an input was held back and nothing recorded *why*,
so two opposite situations produced the same record:

- **ABOVE_CEILING** — the caller classified the value and the envelope's ceiling excluded it.
  The control working as designed.
- **OVER_CAP** — the value exceeded `MAX_FIELD_VALUE_LENGTH` and could not be represented at
  all. The advisory was computed on less context than the caller believes.

Only the second deserves attention, and the flat list made a routine design decision
indistinguishable from a quiet degradation. This is the R20/R24 shape again: a signal that
exists but cannot be distinguished is not a signal.

`ContextEnvelope.withheld_fields` now returns `(name, reason)` pairs and
`over_cap_field_names` exposes the subset worth watching. An input that is both over-cap and
above the ceiling is reported **once**, as `OVER_CAP`, so telemetry never double-counts one
input. Names only, never values — keeping the withheld value would leave the raw subject text
one refactor away from the canonical form, which is the thing withholding exists to prevent.
The reason travels end to end to `ApprovalAdvisoryResponse.over_cap_context_fields`, and
`DecisionRequest` enforces that the over-cap set is a **subset** of the excluded set: two
records that disagree about what was withheld are worse than one.

**Second defect found while testing.** `from_candidates` validated the classification only on
the under-cap path; the over-cap branch skipped it because the field was never constructed, so
a bare string slipped through there while raising under the cap. The path that *discards* the
value must not be a hole in the type check. `ContextField` still accepts `RESTRICTED` and lets
the ceiling withhold it — its documented "exclusion is recorded, not silent" contract, which an
earlier draft of this slice wrongly inverted by rejecting it.

**Tests.** 10 new cases, including the validation-bypass regression, the once-only rule, and an
API boundary test that creates a **real note**. That test was first written against a
fabricated `resource_ref` and returned `[]` — no real note, no title candidate, so the
assertion proved nothing. A test that passes because its subject does not exist is worse than
no test.


## NEX-031 (run `NEX-2026-11-25-33`) — a note is 300x the context cap, and that must not deny the advisory

NEX-030 closed by naming its own residual: the envelope bounds (32 fields, 200 chars/field) had
met real approval metadata but never real **note text**. The subject of an approval is a note,
and a note body may be `MAX_CONTENT_BYTES` — 64 KiB of UTF-8, roughly 300x the per-field cap.

`RecommendApprovalDecision` built its context with `ContextEnvelope.from_fields`, which
constructs every `ContextField` eagerly and therefore *raises* when a value exceeds
`MAX_FIELD_VALUE_LENGTH`. That is correct for hand-authored references, where the caller is
responsible for bounding input. It is wrong for record-derived content: a note title is legal at
the notes domain's `MAX_TITLE_LENGTH`, so "the title is long" became "the advisory failed" — a
denial of a read the caller is entitled to. The two caps are independent constants that happen to
both be 200 today, which is exactly what hid the defect: a title at the domain maximum sits *at*
the cap, so a coincidence read like a guarantee.

The fix is `ContextEnvelope.from_candidates` for record-derived values: an over-cap value is
**withheld instead of raised**. The field is never constructed — so it cannot enter the digest,
and the raw subject text is not left in memory one refactor away from the canonical form — and
its name is recorded in `withheld_field_names` so the audit trail still says the input existed.
`excluded_field_names` unions the two withholding reasons (above-ceiling classification,
over-cap value), because an audit reader needs the same fact either way: *this input was present
and not sent*. Construction rejects a withheld name that is not a legal field name, a name that
is both included and withheld, and repeated withheld names. `from_fields` is unchanged and still
refuses over-cap values; the distinction is caller-knows-the-bound versus value-comes-from-a-record.

Tests: the over-cap path is withheld and named; a withheld value never reaches the digest (two
envelopes differing only in the over-cap value hash identically); the two reasons union without
shadowing; illegal and contradictory names are rejected. The advisory gains the production-data
check — a real stored note with a **64 KiB body** still yields an advisory, its title withheld by
*classification* (CONFIDENTIAL, above the INTERNAL ceiling) rather than by length, and the
approval stays PENDING. A second test shrinks the context cap so the two caps diverge, proving the
divergence rather than relying on today's coincidence.

**Load-bearing:** reverting the fix fails the max-title test with `a context field value may be at
most 50 characters` at `context.py:133` — the exact denial-of-advisory the change removes.

## Last completed task

**NEX-030 (VERIFIED, `0727628`, CI 4/4)** — `DecisionRequest.context_hash` was a plain `str`, so a
caller could hand-assemble a digest that did not correspond to the envelope it was sending.
Asserting a hash asserts "these were the inputs", so a caller-assembled value takes on the
declassification decision ADR-012 gives to `ContextEnvelope` alone. `ContextHash` subclasses
`str`, so it serialises, compares and hashes as the plain hex digest — it is a construction-time
guard, not a new wire format. Only `ContextEnvelope.context_hash` can mint one. The stated limit
is recorded in the class docstring: `str.__new__` still bypasses the guard deliberately, so the
mistake is impossible to write by accident rather than impossible for a process already inside
this one.

## NEX-024 (run `NEX-2026-11-25-26`) — aborting is settling, so it must seal too

NEX-023 sealed the invocation a crash left open when an owner **rules** on an interrupted
step. It missed the owner's other way of settling a run — **aborting** it — so R27 survived
on exactly one path: the one taken when the owner declines to adjudicate.

Two records disagreed. `AgentRun.abort` flipped the run to `aborted` and touched nothing
else, leaving a `started` step inside a terminal run, and `AbortAgentRun` never closed the
gateway invocation, so the audit chain kept saying `started` — indistinguishable from a call
still in flight, forever.

Both halves now settle. The aggregate settles every non-terminal, non-`pending` step before
it flips the run's status (`started` → `failed`, `interrupted` → *effect not confirmed*);
`pending` steps are untouched because they never began. The use case records the mid-flight
sequences before aborting, then seals each matching invocation by the gateway's own key in
the same transaction as the run save. It writes `fail(...)`, not `succeed(...)`: aborting is
not a ruling that the effect landed, and claiming success would fabricate evidence. Absent
or already-terminal links are left alone, as in NEX-023.

Status: **VERIFIED** at `eaaae23` — 4/4 `completed/success` (two Backend `ruff, mypy,
pytest, migrations` and two Frontend `typecheck, lint, test, build`, for the push and
pull-request events), polled 2026-11-25T12:16Z; all four `head_sha` values equal the polled
commit. Local gates at the slice: `ruff format` clean (201 files), `ruff check` clean, `mypy`
clean over 98 sources, full `pytest` **742 passed / 4 skipped** (baseline 740, delta exactly
these 2 tests), `tests/agent/test_agent_runs.py` 21 passed. Negative control: removing only
the seal loop turns exactly `test_abort_seals_the_audit_link_the_crash_left_open` red.

## Last completed task

**NEX-024 (VERIFIED, `eaaae23`, CI 4/4)** — aborting a run settled it in one record and left
the other mid-flight. `AgentRun.abort` flipped the run to `aborted` and touched nothing else,
so a `started` step stayed inside a terminal run, and `AbortAgentRun` never closed the gateway
invocation, so the audit chain kept reporting `started` for a run that was definitively over —
indistinguishable, forever, from a call still in flight. Both halves now settle in the same
transaction: the aggregate abandons every non-terminal, non-`pending` step (`started` →
`failed`, `interrupted` → *effect not confirmed*) and the use case seals each matching
invocation as `fail(...)`. It never writes `succeed(...)`: aborting is not a ruling that the
effect landed, and claiming success would fabricate audit evidence. `pending` steps are
untouched because they never began. R27 is now closed on both settle paths that exist —
resolve (NEX-023) and abort (this slice).

Previously, **NEX-023 (VERIFIED, `c367746`, CI 4/4)** — a ruled-on effect left the audit trail asserting
"outcome unknown" forever. `InvokeTool` commits its `ToolInvocation` as `started` before
entering the handler, which is right: a crash must leave a trace. But
`ResolveInterruptedStep` settled the run and never closed that row, so after an owner ruled
whether the effect landed the chain still said `started`. Three costs, all permanent: the
trail is wrong about a fact a human has since established; `started` is exactly what a live
call looks like, so a crash-orphaned link and an in-flight one became indistinguishable; and
the deterministic key stayed open, so `_find_prior` raised `Conflict` on every retry — the
ruling meant to unblock the run did not unblock the gateway. The use case now seals the link
in the same transaction that settles the run (`succeed`/`fail`, no write when the link is
absent or already terminal), so the two records cannot disagree. Three tests pin it,
including that an already-settled link is never rewritten; reverting the use case turns
exactly the two sealing tests red.

Previously, **NEX-015 (VERIFIED, `414a222`, CI 4/4)** — a decision erased the approval record from the
only read path. `GET /agents/{id}/approvals` returned pending requests only, so
once a human approved or denied a sensitive action the request vanished from the
API: the approver could not confirm what they had authorised, and an auditor had no
API view of the grant at all. Reproduced before fixing (after a decision, the
listing returned `[]`), then fixed by adding a `?status=` filter that defaults to
`pending` (unchanged behaviour) and a `GET /agents/approvals/{id}` read route. Both
are owner-only and return 404 to a non-owner, so an approval id cannot probe for
another user's agent. Eight new tests pin approved/denied/revoked visibility, the
unchanged default, read-by-id, and the 404/422 paths; reverting the use case to
`list_pending_for_agent` fails three of them.

## Previous task

**NEX-012 — Tamper-evident audit chain** (run `NEX-2026-11-25-12`, `3aade7c`).
NEX-005 wrote a row for every gateway attempt and NEX-011 made those rows
readable, but the rows were still mutable: anyone with database write access
could flip a `denied` verdict to `succeeded` or delete a row, and nothing would
notice. A record that cannot prove it is intact is not audit evidence.

Each invocation now carries `chain_sequence`, `previous_hash`, `intent_hash` and
`outcome_hash`, sealed over the previous link. `GET
/api/v1/agents/{agent_id}/tool-invocations/verification` returns
`{verified, checked, first_break}`.

Three decisions carry the slice:

- **A break is reported, not raised.** `verify_chain` is pure and returns a
  `ChainVerdict` naming the sequence where history diverges. An exception would
  tell a caller only that something is wrong, when what an auditor needs is
  *where*.
- **`checked` is part of the contract.** Without it, `verified: true` could mean
  "walked nothing" — a verifier that reports success without examining rows is
  worse than no verifier.
- **The chain is listed unpaginated.** A window's first entry does not carry its
  predecessor's hash, so a window cannot be verified. `list_chain` is deliberately
  whole-trail.

The gateway seals intent before the call and outcome after it, refusals included,
because a blocked attempt is evidence too. `next_chain_link` is read inside the
inserting transaction and the unique `(agent_id, chain_sequence)` constraint
rejects a concurrent loser, so the chain cannot silently fork.

Pinned by `tests/api/test_audit_chain_verification_api.py` (7 tests), which tampers
through the database directly — that is the threat model; tampering through the API
would prove nothing. **Status: VERIFIED** — CI on `3aade7c` returned 4/4 `completed/success`.

**NEX-011 — Tool-invocation audit trail over HTTP** (run `NEX-2026-11-25-11`, `f50a78d`).
The governed tool gateway writes a `ToolInvocation` row for every attempt it
handles, refusals included, but nothing outside the gateway could read one back.
A trail nobody can read is not an audit trail: an owner could not answer "what did
my agent actually try, and what was refused?"

`GET /api/v1/agents/{agent_id}/tool-invocations` now serves that trail through a
`ListToolInvocations` use case over the **existing** `ToolInvocationRepository`
port and its `list_for_agent(limit, offset)` adapter method. The port, the table,
and the paged query all already existed; this slice adds the read path and
nothing else — no new port, no migration.

Three properties carry the slice, each pinned by a test rather than asserted in
prose:

- **Owner-scoped, 404 not 403.** The trail says which tools an agent reached for,
  so it is the owning human's evidence. Reusing `load_owned_agent` makes a
  non-owner and a nonexistent id indistinguishable, so the endpoint cannot be used
  to probe for another user's agents.
- **A digest, not the arguments.** The trail proves a call happened with a given
  shape without becoming a second copy of note content. A test asserts a note's
  private body never appears in the response.
- **Refusals are as visible as successes.** "The agent never tried" and "the agent
  was blocked" are different audit findings, so a denied call must leave a row. A
  test drives a capability-denied invocation and asserts a `denied` row carrying
  the gateway's outcome is present.

The one design departure: this route takes `limit`/`offset` directly instead of
the shared `_page` helper the other agent listings use. An audit trail grows
without bound, so a caller has to be able to walk it deliberately; `limit` is
capped at 200 and validated at the edge.

Pinned by `tests/api/test_audit_trail_api.py` (8 tests). **Status: VERIFIED** — it
reached VERIFIED on an observed 4/4 `completed/success`; the earlier "not yet
verified" note was a snapshot of the run that wrote this section and is no longer
true.

Previously, **NEX-009-HTTP — Durable agent runs over HTTP** (run `NEX-2026-11-25-10`, `e817d04`).
Slice 1 built the durable runtime but left it unreachable: the use cases existed
and nothing could call them, so durability was real and unusable (R11).

`presentation/api/routers/agent_runs.py` now mounts `/api/v1/agent-runs` with
plan, advance, get, list, resolve, and abort. The router is a thin edge — it
authenticates and delegates — so audience, revocation, capability, approval,
ordering, and audit stay in the use cases and the gateway rather than becoming a
second, weaker path to a tool.

The load-bearing decision is that two authorities meet on one resource. Planning
and advancing are the *agent's*, keyed on the agent token and reading nothing
else, so a demo identity cannot drive an agent's run. Reading, resolving, and
aborting are the *owner's*, because an interrupted step means the runtime cannot
tell whether a side effect landed and only the human who owns the agent may rule
on it. An agent therefore has no endpoint that settles its own ambiguous step.

Hardening found while writing the edge: `PlannedStep.capability` is now derived
from the tool catalog and is no longer an init field. A caller could previously
hand the use case a step that named a privileged tool while declaring a weaker
capability, and the run row is exactly what an operator reads when deciding
whether an interrupted effect landed — so understating authority would corrupt
the audit of the one case that matters. The mismatch is now unrepresentable
rather than merely rejected.

Pinned by `tests/api/test_agent_runs_api.py` (9 tests) and two new domain tests
in `tests/agent/test_agent_runs.py` (14 total). **Status: VERIFIED** — it reached
VERIFIED on an observed 4/4 `completed/success`; the earlier "CI has not yet run"
note was a snapshot of the run that wrote this section.

Previously, **NEX-009 slice 1 — Durable agentic runtime** (run `NEX-2026-11-25-09`, `0e74d4f`).
A governed tool invocation was atomic: it ran or it refused, and nothing survived
the process. An agent therefore could not carry a multi-step intention across an
interruption, and a crash mid-effect left no record of what had been attempted.

The run is now an aggregate (`domain/agent/runs.py`): an ordered plan of steps,
each recording the arguments it was asked to perform, with a lifecycle that
cannot skip a step or settle one out of order. A step is persisted as `started`
*before* the gateway is called and saved again on completion, so a resumed run
replays the persisted plan instead of re-deriving it.

The load-bearing decision is what happens on recovery. A step found `started` is
marked **INTERRUPTED** and the run **halts**. The runtime cannot know whether the
effect landed, and guessing is wrong in both directions: assume it landed and the
effect is silently lost; assume it did not and the effect is applied twice. Only
an owner may settle it (`ResolveInterruptedStep`), and the settled step is never
re-executed. That is the difference between durability and an at-least-once retry
loop, and it is what the tests pin.

Also in the slice, because durability alone is not safety: a run is scoped to the
agent its token resolves to, so one agent cannot read or abort another's run;
capabilities are checked at plan time against the token; and a revoked delegation
stops the run at the next advance rather than at token expiry.

Pinned by `tests/agent/test_agent_runs.py` (13 tests). **What is not done:** there
is no HTTP surface for runs, so no client can plan or advance one yet. That is
slice 2.

Previously, **NEX-010 — Direct tests for the OIDC token verifier** (run `NEX-2026-11-25-07`).
`HttpOidcRelyingParty._verify_id_token` was the only security-critical code in the
identity stack that **no test imported**: every login test injected
`FakeRelyingParty`, so NEX-002's criterion "an ID token with wrong
issuer/audience/expiry is rejected" was asserted only against the fake. It now
has `tests/authentication/test_oidc_relying_party.py` — 21 tests using real
in-process RSA keys and real RS256 signatures, stubbing only the JWKS lookup so
no network is touched.

The point of the slice was that the tests must be able to *fail*, so that was
proved by mutation rather than assumed: disabling `verify_aud`, `verify_iss`,
`verify_exp`, `verify_iat`, `verify_signature`, the nonce check, the nonce
comparison, the blank-subject guard, or the algorithm allowlist each turned the
suite red. (Deleting the `"require"` list survives — it is defence in depth, not
the enforcing mechanism; the behaviour it guards is still pinned by
`test_token_without_{expiry,issued_at,issuer,audience}_is_rejected`.)

Writing the tests found a **real defect**, fixed in this slice: the nonce
comparison called `secrets.compare_digest(nonce, expected_nonce)` on `str`, which
raises `TypeError: comparing strings with non-ASCII characters is not supported`
when the untrusted `nonce` claim contains non-ASCII. A hostile IdP response
therefore produced a **500 at the edge** instead of a refusal. It now compares
UTF-8 encodings — still constant-time, and unable to raise.

NEX-002 moves **IMPLEMENTED → TESTED → VERIFIED**. It became VERIFIED in run
`NEX-2026-11-25-08` when CI ran the full suite on `0fcc078` (4/4 green), because
local gates are not CI evidence and this run refused to conflate them.

Previously, **NEX-007 — Tenant-scoped capability answer** (run `NEX-2026-11-25-06`,
`e1cb0c5`). `ListMyOrganizations` filtered on membership rows alone, so a
**suspended or archived tenant was listed with its full capability set** while
`GetOrganization` refused the same tenant with `404` — a client would render a
tenant it could not then use. The domain rule already computed the correct set
(`TenantAccess.organization_ids`) but nothing consumed it. It is now exposed as
`actionable_organization_ids(uow, user_id=...)` — the single answer to "which
tenants count for this caller" — and the listing filters through it. The set
requires an active membership *and* an operational organization, so suspending
one tenant does not hide the caller's others. No caching, so a status change
takes effect on the next request. Pinned by
`tests/application/test_organization.py::TestSuspendedTenantIsNotListed`.

Previously, **NEX-005-HTTP — Expose the tool gateway over HTTP** (run `NEX-2026-11-25-05`,
`c18bac7`). `POST /api/v1/agents/tools/invoke` is the thinnest possible edge over
`InvokeTool`, so audience, revocation, capability, approval, and audit remain in
exactly one place. Two properties are load-bearing and pinned by
`tests/api/test_tool_gateway_api.py` (9 tests): the demo-identity header is
**ignored** (a demo user id is not authority to invoke a tool), and the token is
resolved for `TokenAudience.TOOL_GATEWAY`, so a Notes-API token is refused.
Refusals return `401`/`403`/`409`/`422`, never a `500`, and the token is never
echoed. Rate limiting keys on a digest of the presented token rather than the
client address, so agents cannot exhaust each other's budget.

Previously, **NEX-008 — Add continuous integration** (runs `NEX-2026-11-25-03`
and `-04`).
The repository had **no CI at all**; `.github/workflows/ci.yml` now runs the
backend gates (`ruff format --check`, `ruff check`, `mypy`, `pytest`), the
frontend gates (`typecheck`, `lint`, `test`, `build`), and an apply-then-revert
migration check on every push and pull request.

**CI is observed green, not merely configured.** Runs `45531483962` (push) and
`45531492783` (pull_request) both succeeded on `6f30020`; `gh api
.../commits/6f30020/check-runs` reports all four check runs `completed/success`.
Risk R3 is closed on that evidence.

The first run failed, and usefully so: `tests/persistence/test_migrations.py`
shelled out to the literal path `.venv/bin/alembic`, which exists on a developer
machine and nowhere else, so the three migration tests had never run outside a
local checkout. Fixed in `6f30020` by invoking `sys.executable -m alembic`.


Previously, **NEX-006 — Tenant access enforcement** (`4c5a6ef`). Authentication proves
identity; tenant access is a separate deterministic check that the caller still
belongs to an operational tenant, so a suspended or deprovisioned membership
stops working on the next request rather than at session expiry. Enforced at one
shared seam (`ensure_tenant_access`) across all three identity-establishing
paths: `CompleteLogin`, `ResolveSession`, and `OidcIdentityProvider.authenticate`.

Previously, **NEX-001** fixed two defects that blocked the organization slice:

1. `OrganizationUseCase` was referenced in `bootstrap/container.py` without being
   imported — this broke *every* test that built the container (121 setup
   errors, all one root cause).
2. `SqlAlchemyTeamMembershipRepository.list_for_team` returned soft-deleted
   (`DEPROVISIONED`) rows, so `DELETE .../teams/{id}/members/{id}` answered 204
   while the member still appeared in the roster.

## Identity & access: what is enforced today

Authentication proves *who* the caller is. **Tenant access** is a separate,
deterministic application-layer check that the caller *still belongs* to an
operational tenant, so a suspended or deprovisioned membership stops working on
the next request rather than at session expiry.

- Pure rule: `domain/organization/tenant_access.py` →
  `GRANTED` / `DENIED` / `UNAFFILIATED`.
- Shared seam: `application/use_cases/tenant_access.py` →
  `ensure_tenant_access(uow, user_id=...)`.
- Enforced on all three identity-establishing paths: `CompleteLogin`,
  `ResolveSession` (per request), and `OidcIdentityProvider.authenticate`
  (bearer tokens). One check, three call sites, no copies to drift.
- `UNAFFILIATED` is allowed so a brand-new identity can create its first
  organization; it grants nothing inside an existing tenant.
- The refusal message is uniform, so callers cannot probe for account existence.

## Verified implementation status

The organization slice is implemented across all layers:

- **Domain** — `domain/organization/{entities,value_objects}.py`: organizations,
  units (flat tree with cycle prevention), teams, organization memberships with
  manager links, team memberships, membership status lifecycle.
- **Application** — `use_cases/organization.py` (org/unit/team/member lifecycle,
  last-administrator guard, privilege-escalation guards),
  `use_cases/organization_policy.py` (role → permission mapping).
- **Ports** — `application/ports/organization_repositories.py`,
  `organization_authorization.py`; UnitOfWork extended.
- **Infrastructure** — `infrastructure/db/organization_repositories.py`,
  models, `uow.py`; Alembic migration `189c359206d3_organization_hierarchy.py`.
- **API** — `presentation/api/routers/organizations.py`, schemas registered.
- **Tests** — domain, application, security, API, and migration suites.

## Quality gates (latest local run)

| Gate | Result (run `NEX-2026-11-25-31`, bare as CI runs them) |
| --- | --- |
| `ruff format` | clean — 156 files unchanged (`src/`, `tests/`) |
| `ruff check` | All checks passed |
| `mypy` | clean (104 source files) |
| `pytest` (full) | **828 collected** on head `03ead43` (local collection at this head). The pass/skip split is quoted from CI rather than a local run: backend CI is green at `a3d1b32`. |
| Migration chain | no migration added; head unchanged. No schema change in this slice |
| Frontend `typecheck`/`lint`/`test`/`build` | not run locally this slice — no frontend files changed (CI ran them anyway and they passed) |

> **CI is the evidence.** Local gates above are for reproducibility, not proof (R9/R10).
> NEX-028 is **VERIFIED**: `a3d1b32`'s four check runs are 4/4 `completed/success`,
> every `head_sha` equal to `a3d1b32`, polled 2026-11-25T14:42Z.

## Blockers

**R38 — stale lease blocks coordination writes until 2026-11-27T09:34:54Z (`1795769694`).**
`refs/locks/nexus/lease` is held by `Andrews-Mac-mini.local-56084` (run `NEX-2026-11-27-43`).
That process is dead, but the lease is unexpired, so `lease acquire` exits 2 and refuses the
write. A second segment of this run wrote `606805f` and `5ecb5c7` without holding the lease —
a protocol violation, recorded rather than concealed. No state damage: the branch is linear,
the tree is clean, `main` is untouched, and CI on the head is 4/4 success. Product work is
unaffected; only coordination-file writes are blocked. Do **not** force-delete the lease ref;
it expires on its own. See R38 in `CHECKPOINT.json` for the evidence and the follow-up
(a liveness-aware takeover, so a crashed holder stops blocking writes for the full 45-minute TTL).

The following are known open items, not blockers:

- Tool gateway, Jev integration, and the admin console UI are **not yet
  implemented** — they are sequenced in `ROADMAP.md`, not regressions.

## Risks

- **R1 (closed 2026-11-25):** PR #3's description claimed the PR was
  "specification/design only" while the branch carried 19,228 insertions across
  85 files. It now reports the implemented phases, the two gateway defects, and
  the validation evidence. The PR is still a draft: bringing it to review is a
  human decision, not the coordinator's.
- **R2 (low):** The frontend has no admin console yet; the expansion spec's
  frontend testing requirements cannot be satisfied until that work lands.
- **R3 (closed 2026-11-25, runs `NEX-2026-11-25-03` / `-04`):** The repository
  had **no CI configuration at all** — no `.github/workflows` on any branch, and
  zero check runs on any commit. `.github/workflows/ci.yml` now runs the backend
  gates (`ruff format --check`, `ruff check`, `mypy`, `pytest`), the frontend
  gates (`typecheck`, `lint`, `test`, `build`), and an `alembic upgrade head` →
  `downgrade base` cycle, on every push and pull request. Closed on observed
  evidence, not on the file's presence: runs `45531483962` (push) and
  `45531492783` (pull_request) succeeded on `6f30020`, and `gh api
  .../commits/6f30020/check-runs` reports all four check runs
  `completed/success`. The first run (`b223e02`) failed and found a genuine
  portability defect — migration tests invoked `.venv/bin/alembic`, which does
  not exist in CI, so those tests had never run outside a local checkout; fixed
  in `6f30020`. Residual: CI covers the documented gates only, so a gap no test
  exercises is still a gap.
- **R4 (low):** `hypothesis` is not a project dependency, so the attenuation
  invariant is proved by exhaustive enumeration rather than property-based
  sampling. This is stronger for the current delegable universe but would not
  scale if the capability set grew substantially; revisit if capabilities
  multiply.
- **R5 (closed 2026-11-25, run `NEX-2026-11-25-05`):** The tool gateway had no
  HTTP/router exposure, so no API contract test covered it and no client could
  call it. `POST /api/v1/agents/tools/invoke` now exposes it, pinned by
  `tests/api/test_tool_gateway_api.py` (9 tests). Closed on **VERIFIED** evidence:
  committed and pushed at `c18bac7`, and CI is green on that exact commit (four
  check runs, all completed/success).
- **R6 (closed 2026-11-25):** the tool gateway now has a dedicated test file,
  `tests/agent/test_tool_gateway.py` (18 tests). Writing it found two real
  defects, both fixed in `74d3d98`: a retried *denial* hit the
  `(agent_id, idempotency_key)` unique constraint and raised `IntegrityError`
  instead of replaying the recorded refusal, and a revoked delegation surfaced
  the domain `InvariantViolation` (a 500 at the edge) instead of a refusal.
  Do not delete that file; both bugs return silently without it.
- **R10 (low, process):** a green local gate run is only evidence
  if it invokes the *same command CI invokes*. The pre-push run for `2dd7a0c` was
  scoped to `src tests`, so it skipped `docs/`, where ruff then failed the build
  on a Markdown-embedded Python fence. Run the gates bare. Recorded in
  `AGENTS.md` so the next run does not relearn it.
- **R11 (closed 2026-11-25, run `NEX-2026-11-25-10`):** the durable-run use cases
  had no HTTP surface, so durability was real and unreachable — no client could
  plan or advance a run. `presentation/api/routers/agent_runs.py` now mounts
  `/api/v1/agent-runs` (plan, advance, get, list, resolve, abort), pinned by
  `tests/api/test_agent_runs_api.py` (9 tests). **VERIFIED**: CI reported 4/4
  check runs `completed/success` on `e817d04`. Writing the edge also found a real
  hardening gap — `PlannedStep.capability` was caller-supplied, so a plan could
  name a privileged tool while declaring a weaker capability; it is now derived
  from the tool catalog and is not an init field.
- **R16 (new, low):** an approval's `expires_at` column is nullable, and
  `ApprovalRequest.is_usable` treats NULL as "never expires". That is deliberate
  for rows written by an older application version during a rolling deploy, but
  it means such a row is unbounded. Tighten the column to NOT NULL once no old
  writer can exist. The backfill does populate it for every pre-existing row, so
  NULL now only arises mid-deploy.
- **R17 (new, low, process):** a data-backfill migration that is only ever run
  against an *empty* database is untested, and its tests will stay green while it
  crashes on the first real row. NEX-013 shipped exactly that: `requested_at +
  timedelta` raised `TypeError` on SQLite's string value, aborting the upgrade and
  leaving `expires_at` NULL — i.e. the migration that exists to bound approvals
  left them unbounded. Any migration that reads existing rows needs a test that
  seeds a row at the previous revision. `TestApprovalValidityBackfill` is the
  pattern to copy.

## Next best actions

1. ~~**Promote NEX-027 on CI.**~~ **Satisfied** — 4/4 `completed/success` at `fb8d5bd`,
   polled 2026-11-25T14:18Z.
2. ~~**NEX-028 context assembly.**~~ **Satisfied** — VERIFIED, 4/4 `completed/success` at
   `a3d1b32`, polled 2026-11-25T14:42Z. M2.6 decisioning is now complete in both halves.
3. ~~**Refresh PR #3.**~~ **Satisfied this run** — refreshed from the PR object at head
   `03ead43`, PATCH 2026-11-25T14:52:08Z, read back byte-equal (30,024 chars). Counters now
   34,700 additions / 10 deletions / 124 files / 137 commits.
4. ~~**Wire `ContextEnvelope` into a real caller.**~~ **Satisfied** — NEX-029 (production
   caller), NEX-030 (`ContextHash`), NEX-031 (real note bodies) and NEX-032 (per-field
   observability) are all VERIFIED. M2.6 decisioning is complete in both halves and exercised
   against real stored data.
5. ~~**Confirm whether revoking a delegation or agent invalidates approvals already granted
   under it.**~~ **Satisfied** — closed on both paths by NEX-025 (R28).
6. ~~**Aggregate over-cap withholdings into a count — the natural successor to NEX-032.**~~
   **Satisfied by NEX-033**, which shipped the `WithholdingRecorder` port for exactly this.
   The checkpoint's `next_action` naming NEX-034 for this work was **stale** — written
   before NEX-033 delivered it. Run `NEX-2026-11-25-35` found this during reconciliation
   and re-scoped NEX-034 to a real defect rather than re-implementing a shipped feature.
   **Lesson:** a `next_action` field is intent recorded at one moment; check it against
   `git log` before executing it, the same way a `commit` field is checked against
   `ls-remote`.
7. ~~**Re-poll CI at `fe5eadc` and promote NEX-032 to CI-verified.**~~ **Satisfied.** 4/4
   `completed/success`, every `head_sha` = `fe5eadca918b…`, polled 2026-11-25T16:47Z. PR #3
   body PATCHed (43,921 chars) and issue #2 commented (7168592223) with that evidence.
8. ~~**NEX-034: harden `redact()`.**~~ **Satisfied** — IMPLEMENTED, TESTED and PUSHED at
   `e386e02`; CI poll pending. The matcher now decides on credential *shape* (separator and
   camelCase segmentation, plurals, non-sensitive compounds) instead of eleven exact
   strings, and a sequence under a sensitive key is replaced whole instead of leaking.
9. ~~**Poll CI at `e386e02` and promote NEX-034 if green.**~~ **Satisfied** — 4/4
   `completed/success`, every `head_sha` equal to `e386e02`, polled 2026-11-25T18:15Z. NEX-034
   is CI-verified. A docs commit that follows a product commit reads `completed/cancelled` once
   superseded — that is the concurrency group, never evidence.
10. ~~**Then R30, the one live risk with no mitigation.**~~ **Satisfied by NEX-035** — the
    conformance suite exists and R30 is closed. Every bound in the decision contract
    (400-char rationale, `[0.0, 1.0]` confidence, the candidate choice set) now has a test
    that exercises it against a hostile payload, not only against the stub. R29's stated
    `str.__new__` residual on `ContextHash` is a deliberate, documented trade-off, not a gap
    to close.
11. ~~**NEX-035: the provider identity seam.**~~ **Satisfied** — IMPLEMENTED, TESTED,
    COMMITTED, PUSHED and **CI VERIFIED**: 4/4 `completed/success` at `12f6fa9`, every
    `head_sha` equal to `12f6fa9`, polled 2026-11-25T18:14Z.
12. **Next: audit the decisioning path's *callers* for a swallowed abstention.** The module's
    contract is that an abstention is advice and enforcement stays deterministic — but a
    caller that ignores `DecisionEvaluation` and proceeds on `None` would silently turn
    "the provider could not judge" into "allowed". Enumerate every production caller of
    `EvaluateDecision.run`, and for each, pin with a test what it does when the outcome is
    `ABSTAIN` with reason `DISABLED`, `PROVIDER_ERROR` and `MALFORMED_OUTPUT`. This is the
    fail-closed question, and it is the one part of M2.6 no test covers yet. Verify the
    premise first: `grep -rn "EvaluateDecision\|\.run(" src/notes_service/application`
    before assuming callers exist.

**A terminal state in one record is not a terminal state in the other (R27).** NEX-023 is
the same shape as R26 (a column written and never read) but inverted: the run aggregate
reached a terminal status while the *audit* record for the same step stayed `started`
forever. Whenever a use case settles one record, ask which other records describe the same
event and whether any of them can be left mid-flight. A settled run next to a `started`
invocation is not a partial success; it is two records that disagree, permanently.

**Check the adapter, not just the aggregate (R18).** NEX-013 was VERIFIED while
`ApprovalRequestRepository.add()` silently dropped the two columns the whole
invariant depended on, because the expiry test used `save()`. When a slice adds
a field that a security check reads, pin it through the write path production
actually uses.

**Promote nothing on local gates alone.** NEX-009 slice 1, NEX-009-HTTP, NEX-011
and NEX-012 each reached TESTED on local runs and then VERIFIED only when CI went
green on the commit (`0e74d4f`, `e817d04`, `f50a78d`, `3aade7c`). A local green
suite is not evidence; R9/R10 exist because that conflation happened before.


## Next best actions (run `NEX-2026-11-25-37`, superseding the list above)

1. **PATCH PR #3's body with a NEX-037 section, then POST one issue #2 comment.** This run
   spent its delivery budget on reconciliation, the slice, and the state files; both are owed.
   Re-measure the counters from the PR object at the new head rather than reusing the numbers
   above — a docs commit moves the head and every counter with it. Use lists, never a markdown
   table, in an issue comment on this repository.
2. **NEX-037's CI is settled — do not re-poll it.** 4/4 `completed/success` at `5c0575b`,
   polled 18:57Z, every `head_sha` equal to that SHA. Any future docs head reads
   `completed/cancelled` once superseded; that is neither pass nor fail and is never evidence.
3. **Next implementation slice: the first real `DecisionProvider` adapter.** It is the largest
   dependency-ready unlock, and NEX-035's conformance suite now exists to police it — the
   suite found four real defects in the identity seam, so a new adapter has something to fail
   against. `make_decision_evaluator()` is still a stub.
4. **R36 is open and is not urgent but is not nothing:** the ops allowlist is a static
   environment variable with no expiry and no record of who read the totals. Fold it into the
   scoped-capability work rather than patching it ad hoc.

## Next best actions (second invocation of run `NEX-2026-11-25-37`, superseding item 1 above)

Item 1 of the list above is now **stale and must not be acted on**. It said PR #3 and issue #2
were owed a NEX-037 report. They are not: a later run in the same run-ID cleared both, verified
here against the live API — PR #3 body contains NEX-037, and issue #2's newest comment is
`7171861451`, the NEX-037 report. Do not re-post either.

1. **Start NEX-038: the first real `DecisionProvider` adapter.** Unchanged, unstarted, and the
   largest dependency-ready unlock. `make_decision_evaluator()` is still a stub. NEX-035's
   conformance suite exists to police the adapter and has already found four real defects in
   the identity seam, so a new adapter has something to fail against. Verify the premise in the
   code before acting — several recent `next_action` fields needed correcting.
2. **Resolve the duplicate before it rots.** Branch `nexus037-duplicate-local` (`33620bf`) holds
   a superseded, different-design implementation of the same withholding endpoint — bearer
   `OPERATIONS_API_TOKEN` instead of the shipped operator allowlist — plus three documentation
   sections the shipped slice lacks (`docs/API.md`, `docs/SECURITY.md`, `.env.example`). Decide:
   port the documentation onto the allowlist design, or delete the branch as superseded. The
   shipped design is the better one; the documentation gap is real. Do not merge it as-is — it
   would document an endpoint shape that does not exist.
3. **Before any of that: read the remote, not a local ref.** `git ls-remote origin
   refs/heads/feature/agentic-enterprise-platform` is the only trustworthy answer to "what is
   the branch head". A stale local `origin/*` ref caused the second invocation of this run to
   re-implement an already-verified slice and burn most of its window. This is the second
   recorded occurrence of that failure.

R36 remains open as described in item 4 above; it is unaffected by this run.

## NEX-039 (run `NEX-2026-11-25-37`, third invocation) — the adapter existed only as a stub

Every decisioning slice from NEX-028 onward has been real: a context envelope, an
unforgeable context hash, per-field withholding with reasons, a rate, a redactor, a
conformance suite, a production caller. All of it was exercised through
`StubDecisionProvider`, which is deterministic, offline, and abstains under any threshold
above its fixed confidence. So the platform could *run* the decisioning path but could never
obtain a real judgment. NEX-039 adds the adapter that changes that.

The interesting part is not the HTTP call. It is the four things the adapter refuses to do.

- **The vendor cannot abstain; it can only be unsure.** The wire contract has no
  `abstained` outcome. A provider returns a confidence and the *caller's* threshold in
  `EvaluateDecision` decides whether that is good enough. Abstention policy therefore stays
  in the application where it is auditable. `DISABLED` and `PROVIDER_ERROR` are our
  operational states describing our relationship to the vendor; a vendor that could claim
  them would be reporting on infrastructure it does not own.

- **The request carries no context, only a question**: `kind` plus sorted `candidates`.
  Not the context hash, not the excluded field names, not the correlation id. The adapter
  never receives note text by the port's design, but the *names* of sensitive categories are
  themselves a disclosure, and the correlation id is ours. A vendor that later needs to
  correlate its logs should get a freshly minted opaque per-request id, not this one.

- **Sorting is what makes order-independence structural.** The conformance suite requires
  that reordering a candidate set not change the answer. Relying on vendor
  order-insensitivity would make that a property of the vendor; sorting before the call makes
  it a property of this adapter, because the same set produces a byte-identical body.

- **A bad answer is an abstention, never an exception.** Transport error, non-200,
  oversized or non-JSON body, an unoffered choice, a non-finite or out-of-range confidence —
  each maps to a contract-legal abstention. Vendor error bodies are *discarded rather than
  parsed*, because they can echo the request or the credential into our audit trail.

Construction fails closed. The endpoint must be `https://` and the name must be a bounded
identifier, both checked in `__init__` rather than per request: a misconfiguration should be
a boot defect, not a process that looks healthy while every advisory degrades to
`unidentified`. `Settings` applies the same rules so the two cannot drift.

Provider selection lives in the container, so there is one answer to "which provider is this
process using". Setting `DECISIONING_HTTP_ENDPOINT` opts a deployment into a real vendor; it
does **not** enable judgment, because `decisioning_enabled` still gates every call.

Two limits are recorded in the module docstring rather than papered over: the cap bounds
*size*, not *duration* — httpx timeouts are per socket operation, so a peer that drips one
byte inside each timeout can still hold the call open; and a rationale is truncated rather
than fatal, because discarding a legal answer over a verbose explanation would let vendor
verbosity deny a decision. The size bound itself now runs **while reading** (R37, run
`NEX-2026-11-27-44`), so it bounds what is held as well as what is parsed.

**Validation.** 51 adapter tests plus 13 config/wiring tests, and the NEX-035 conformance
harness invoked at `tests/agent/test_http_decision_provider.py:404`. Full gates: ruff clean
(172 files), mypy clean (110 source files), **984 passed / 4 skipped**. Negative control:
replacing `sorted()` with `list()` at `http_provider.py:135` turns exactly three tests red —
`test_a_choice_request_carries_only_kind_and_sorted_candidates`,
`test_candidate_order_does_not_change_the_request_bytes`, and the conformance test — which
proves the invariant is genuinely tested and not incidentally satisfied. The mutation was
reverted byte-identically before commit. CI: 4/4 `completed/success` at `8e670a6`, polled
2026-11-25T19:32Z.


## NEX-042 (run `NEX-2026-11-27-45`) — the integrity check only worked for the owner who already suspected

**The gap.** `VerifyToolInvocationChain` answers "is this one agent's trail intact" for that
agent's **owner**. Nothing answered the platform question — *is any trail in this deployment
broken* — so a cut tail or an edited verdict on any other agent stayed invisible until the
affected owner happened to open their own agent. Detection is not an owner's duty; it is the
platform's. This is the same class as R23 and R26: a control that is correct on every path
someone enumerated, and absent on the path nobody enumerated.

**The slice.** `SweepAuditIntegrity` walks every agent, compares each tail against the
out-of-band `ChainAnchor`, and emits one `ChainAuditFinding` per contradiction. Three
properties carry the argument:

1. **Coverage is falsifiable.** The report carries `agents_total` *and* `agents_checked`, so a
   capped sweep cannot be read as "all chains intact". `verified` is `False` whenever
   `truncated` is true, because the unexamined agents could each be broken.
2. **`checked` counts only what passed.** `verify_chain` reports `checked =
   expected_sequence - 1` at a break — the entries that verified *before* the contradiction.
   This run's single test failure was my assertion (`== 1`), not the code: the implementation
   was right and the test was wrong. The corrected assertion records why in the test.
3. **The gate fails closed twice.** An empty operator allowlist admits nobody, not everybody;
   a non-operator gets `404`, not `403`, so probing cannot confirm the surface exists. It is
   deliberately *not* an `AuthorizationProvider` decision — that policy governs note content,
   and platform-wide integrity data must never be reachable by content access alone. Same
   shape as `GetWithholdingReport` (NEX-037), reused rather than reinvented.

**Validation on the exact committed tree.** `ruff format --check` 248 files; `ruff check` all
passed; `mypy` no issues in 117 files; `pytest` **1060 passed, 4 skipped, 0 failed**. Targeted:
9 passed across the two new test files. Commit `ed641b0`, pushed, `git ls-remote` confirms the
remote head equals local HEAD, **CI 4/4 `completed/success` at `ed641b0`** (polled
2026-11-27T09:46Z).

**Honest gaps.** No route-level test yet proves the ops route reads the operator allowlist
*from settings*; the API tests exercise the use case directly. `DEFAULT_MAX_AGENTS = 500` is a
guess — it is honest about being one (`truncated`), which is the property that matters, but the
number has met no real deployment.

**R38 recurrence.** The stale lease blocked coordination writes for 254 seconds. It expired at
`1795772807` and was acquired cleanly at `1795772820`. Product delivery finished first, so the
lease blocked only the docs. Second consecutive run; the liveness-aware takeover is still
unbuilt.

## Next best actions (run `NEX-2026-11-27-46`, superseding the lists above)

1. **NEX-043** from the top of `BACKLOG.md`. First command is always
   `git ls-remote origin refs/heads/feature/agentic-enterprise-platform` against
   `git rev-parse HEAD` — every recent run has had to correct a recorded `next_action`.
2. **Close NEX-042's deferred delivery debt:** prove the ops route reads
   `OPS_OPERATOR_USER_IDS` from settings with a route-level test, rather than trusting that
   the container wiring was reviewed. This is product work, not reporting.
3. **Verify the lease fix in the field.** This run proved release/renew across processes on a
   scratch repo, but it did not acquire the shared lease: a prior run's `host-pid` lease
   (`Andrews-Mac-mini.local-95179`, expiring `1795775517`) still held the ref, and under the old
   code no one can release it. The next run should find the ref free after that expiry and
   confirm acquire -> status(`mine: true`) -> release on the real repo. If a legacy `host-pid`
   lease is ever stuck again, let it expire; never force-delete the ref.

## R39 (run `NEX-2026-11-27-45`) — the lease can be acquired but never released

**Found by trying to be polite.** After the NEX-042 docs were pushed, this run called
`lease release` to hand the lease back so the next run would not wait. It exited with
`lease held by another agent`, holder `Andrews-Mac-mini.local-95179` — this run's own identity.

**Cause.** Holder identity is `host-pid`. Every `nexus_coord` invocation is a new process, so
the pid recorded at acquire time is never the pid of the process doing the release. `mine` is
therefore false on the very next call, and `release` and `renew` can never succeed. The lease is
single-use per acquisition.

**Consequence, and why R38 was not bad luck.** A run that acquires a lease necessarily blocks
the next run's coordination writes for the full TTL (45 minutes). R38's "stale lease held by a
dead holder" was the predictable output of exactly this, not an unlucky crash. Expect a third
recurrence at the start of the next run; this run's lease expires `1795775517`
(2026-11-27T10:31:57Z).

**Why the writes were still made.** The ref is held by this run's own identity — the lease *is*
held; only the tool's `mine` comparison is broken. Coordination writes were made on that basis
and are recorded here rather than concealed, the same judgement the prior run recorded for its
second segment. No state damage: the branch is linear, the tree is clean, `main` is untouched.

**Fix (not attempted this run — no time to change coordination tooling safely).** Give the holder
a stable per-run identity — the run id, or a pid that outlives the run — so `release` and `renew`
recognise their own lease. Alternatively a `release --force` guarded by the run id in the lease
commit message. Do **not** force-delete the ref by hand.

This is now the highest-value process fix in the programme: it costs every run minutes of wall
clock and, worse, it invites runs to write without the lease because the lease is unusable.

## Next best actions (run `NEX-2026-11-27-48`, second invocation, superseding the lists above)

1. **NEX-045 — a stored grant.** User, scope, granted-at, expires-at, granted-by behind a
   repository port and adapter, so a grant can expire and an operator can read who holds
   what. `OPS_OPERATOR_USER_IDS` becomes the seed path into it, not the only source. Keep
   the `404`-not-`403` refusal and the record-on-grant behaviour; both are already right.
   This is the residual the NEX-044 section above records as deliberately unclosed.
2. **First command of every run stays** `git ls-remote origin
   refs/heads/feature/agentic-enterprise-platform` against `git rev-parse HEAD`. It has
   been the only reliable way to start for six consecutive runs.
3. **~~Verify the lease fix in the field~~ — CLOSED this run.** Acquire, status, renew and
   release were each run as a separate process against the real `refs/locks/nexus/lease`:
   `mine: true`, `holder_liveness: alive`, `renewed: true`, `released: true`, and the ref
   free afterwards. The holder was `Andrews-Mac-mini.local-s32718`, a POSIX session id,
   which is the shape `64759f4` introduced. The R39 defect is fixed in practice, not only
   in test.

## Correction (run `NEX-2026-11-27-48`) — the lease fix was shipped, not pending

The R39 section below ends with "Fix (not attempted this run)". That was accurate for run
`NEX-2026-11-27-45` and has been false since commit `64759f4`, which introduced the
session-scoped holder and is in this branch's history. Recorded here rather than edited in
place so the earlier entry keeps its own context. The field verification above is the
evidence.

## Next best actions (run `NEX-2026-11-27-48`, NEX-045 segment, superseding the lists above)

1. **Poll CI for `e6f58c0` before anything else.** `GET /commits/e6f58c0/check-runs`. Two
   runs were queued/in_progress at this segment's close. Record the verdict in
   `CHECKPOINT.json`; a pending run is not a pass. If Backend fails, the likely cause is the
   migration check, because the slice adds no migration — verify that rather than assuming.
2. **Clear the delivery debt: two reports are owed, oldest first.** NEX-044 and NEX-045
   have both shipped but neither is on issue #2 or in draft PR #3. Post the NEX-044 report,
   then the NEX-045 report, then append both sections to PR #3 and read them back. This is
   the cheapest high-value work available and it is the only thing making the GitHub record
   lag the branch.
3. **NEX-046 — a durable `OperatorGrantRepository` (closes R40).** The store is
   process-local: a restart forgets a bounded grant and each worker holds its own copy.
   Add a SQLAlchemy adapter behind the existing port, with a migration, and keep the
   in-memory one for tests. The gate and `ListOperatorGrants` do not change — that is the
   point of the port. Do not start this until the two reports above are posted: delivery
   debt compounds, and R40 fails closed so it is not urgent in the security sense.

## Run NEX-2026-11-27-53 — NEX-052 (policy versions on tool invocations)

- **Branch**: `feature/agentic-enterprise-platform` · **HEAD**: `3215e08`
- **Status**: IMPLEMENTED + TESTED + COMMITTED + PUSHED. **CI `in_progress` — not VERIFIED.**
- **Slice**: an audited tool invocation now names the published policy versions that
  governed it, sealed into the intent hash. Allowed and denied calls both record it.
- **Compatibility**: uncited rows hash with the original `v: 1` payload byte for byte, so
  the pre-existing audit trail still verifies; only cited rows move to `v: 2`.
- **Migration** `b7d2c48e91af`: NOT NULL column, backfill, then drop the server default so
  a forgotten write fails loudly. Two real bugs fixed (undropped default; SQLite batch
  rebuild dropping the column).
- **Open delivery debt**: PR #5 section and issue #2 comment for NEX-052.

**Next best actions**

1. **Poll CI for `3215e08`.** This is the only thing standing between NEX-052 and
   VERIFIED. Do not promote it on local gates alone — that is the mistake the status
   semantics exist to prevent.
2. **Clear the NEX-052 delivery debt**: append a section to PR #5 and comment on issue #2.
3. **Obligations.** The security architecture lists deny, allow and *obligations*; the
   gateway models the first two only. An obligation is an allow that still requires an
   action, so it cannot be bolted on as another denial path — it needs the decision to
   carry required actions to the caller. Read `docs/ENTERPRISE_SECURITY_ARCHITECTURE_V1.md`
   for the obligation contract before designing it, and write an ADR if the gateway's
   return type has to change shape.

## Run NEX-2026-11-27-54 — NEX-052 VERIFIED

- **Branch**: `feature/agentic-enterprise-platform` · **HEAD**: `57f50d1`
- **Status**: NEX-052 is **VERIFIED**. CI 4/4 `completed/success` at `57f50d1` on both
  events (polled 2026-11-27T16:30Z). No new product code this run — the slice was
  delivered last run; this run closed its verification debt.
- **Why `57f50d1` is valid evidence**: it is a docs-only descendant of the product commit
  `3215e08`, so the Backend job tests the NEX-052 code, including migrations against
  Postgres.
- **The `cancelled` mystery, resolved**: branch concurrency cancels superseded runs, and
  every checkpoint is a commit on the branch, so a checkpoint naming a SHA cancels that
  SHA's runs while in progress. **Cancellation never retracts a concluded run** — that is
  what makes a later poll authoritative. Recorded in `CHECKPOINT.json` → `ci_status`.

**Next best actions**

1. **Obligations.** The architecture lists three policy effects — deny, allow,
   obligations — and the gateway models the first two only. An obligation allows the call
   but attaches required actions, so it cannot be bolted on as another denial: the
   decision has to carry those actions to the caller and into the audit trail. Read the
   obligation contract before designing, and write an ADR if `PolicyDecision`'s shape must
   change.
2. **PR #5 is ready for human review** — CI green, mergeable, `mergeable_state: clean`,
   24 files. It stays **draft** until a human decides; NEXUS must not merge it.
3. **Tracking issue #2** carries 42 comments; the next phase section should be written
   when obligations lands, not before.

## Run NEX-2026-11-27-55 — obligations enforced at the tool gateway (NEX-053)

- **Branch**: `feature/agentic-enterprise-platform` · **HEAD at checkpoint**: `9521442`
  (the checkpoint commit that writes this file lands one commit later)
- **Status**: NEX-053 is **IMPLEMENTED + TESTED + COMMITTED + PUSHED**, CI **unpolled**.
  It is *not* VERIFIED and must not be counted as such until a run polls the current head.
- **Slice**: ADR-014's obligation axis, implemented. `PolicyObligation` is a closed
  vocabulary (approval, second-approver, step-up, redaction, rate-limit, enhanced-audit).
  `PolicyRule` carries obligations; a deny carrying one raises at construction, and a
  duplicate raises. `evaluate()` unions the obligations of every matching allow and drops
  them when a deny also matched. `PolicyDecision.is_satisfied` is stricter than `allowed`
  so a caller cannot read the weaker property and stop reading.
- **Fail-closed enforcement**: the gateway satisfies only `approval`, and only where the
  domain already treats the capability as sensitive (`ApprovalRequest.open` refuses to gate
  a non-sensitive one, so policy cannot invent a human gate). Every other obligation is
  refused with an audited denial, never silently skipped.
- **Codec compatibility**: `obligations` is omitted when empty, so a rule without them
  encodes to the exact bytes it did before and no stored row needs rewriting. An unknown
  obligation name on decode is an error, not a drop.
- **Local gates on the pushed tree**: ruff format 289 files unchanged, ruff check clean,
  mypy clean (137 source files), pytest 1306 tests / 0 failures / 0 errors / 4 skipped.
- **Concurrent-run hazard observed**: another run pushed to this branch twice during this
  run. Both times the push was rejected, the new remote head was merged (never rebased,
  never force-pushed), and containment was verified with
  `git merge-base --is-ancestor <sha> <remote>` plus `git cat-file -e <remote>:<path>`.
  Grepping push output for a SHA gave a false positive once — use `merge-base`, not text.

**Next best actions**

1. **Poll CI for the current branch head** from `git ls-remote origin
   feature/agentic-enterprise-platform` — do not poll a SHA written in these files, because
   writing them is itself a commit that supersedes it. Only a concluded green run promotes
   NEX-053 to VERIFIED.
2. **Append the NEX-053 section to PR #5** and comment on issue #2 — delivery debt for this
   slice, owed by the next run.
3. **Next slice**: an obligation *satisfaction* adapter for `redaction` (the second kind
   with a real mechanism behind it), or `enhanced-audit` if the audit trail already has the
   extra fields. Do not add a handler for `step-up` or `second-approver` until the identity
   model can distinguish assurance levels and count distinct approvers — an obligation
   "satisfied" by a weaker check than its name promises is worse than a refusal.

## Run NEX-2026-11-27-55 (continued) — NEX-053 VERIFIED

- **CI evidence**: 2 workflow run(s) (pull_request, push), 4 jobs, all completed/success at head `c825db61859591a46b5d4fc1d957130f126db9f8` (polled 2026-11-27T17:07Z). `c825db61859591a46b5d4fc1d957130f126db9f8` is a
  docs-only descendant of the product commit `9c31484`, so the Backend job compiled and
  tested the obligations code, including the Postgres migration path.
- **Promotion**: NEX-053 moves IMPLEMENTED/TESTED/COMMITTED/PUSHED → **VERIFIED**.
  Evidence, not local gates, is what promoted it.
- **Remaining delivery debt**: PR #5 description still has no NEX-053 section and issue #2
  has no NEX-053 comment. That is the first task of the next run.

## Run NEX-2026-11-27-55 (continued) — delivery debt cleared

- **PR #5**: appended the NEX-053 section (obligations as a separate axis, fail-closed
  deny-on-unhandled-obligation, the file table, the CI evidence) and corrected a stale line
  that still called the PR a draft. It is **ready for human review and still unmerged**.
- **Issue #2**: NEX-053 status comment posted.
- **Delivery debt is now zero.** Every completed slice is reflected in the PR and the
  tracking issue, so the next run starts on implementation, not on bookkeeping.
