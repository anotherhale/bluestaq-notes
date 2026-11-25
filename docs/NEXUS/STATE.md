# NEXUS State

Snapshot of where the platform is. Git and repository evidence are the source of
truth; this file records intent and reconciliation.

## Current position

| Field | Value |
| --- | --- |
| Project phase | Phase 2 **complete** — agent identity & delegation all VERIFIED. Phase 5 (durable runtime): **NEX-001…NEX-028 all VERIFIED.** NEX-028 — context assembly — is VERIFIED at `a3d1b32` on observed CI and **completes M2.6 decisioning** (NEX-027 was its first half). |
| Branch | `feature/agentic-enterprise-platform` |
| Base commit (run start) | `8052ded` — the docs-only checkpoint head left by run `NEX-2026-11-25-30`. |
| Head commit (run end) | `a3d1b32` — the NEX-028 code + tests commit. Its CI is polled on that exact SHA: 4/4 `completed/success`, polled 2026-11-25T14:42Z. |
| Last CI result | **`a3d1b32` (NEX-028): 4/4 `completed/success`**, every check run reporting `head_sha` `a3d1b3214fa990aab9abe2f93cdef642bde8c882`, polled 2026-11-25T14:42Z — both the push run (`45636602210`) and the pull_request run (`45636618245`). Prior: `fb8d5bd` (NEX-027), 4/4, polled 14:18Z. |
| Tracking issue | #2 (open) |
| Draft PR | #3 (draft, unmerged — human review required). Body last refreshed at head `fd223f4` (2026-11-25T13:21Z); the branch is now **five commits ahead** (`8742faa`, `f2f8e61`, `fb8d5bd`, `8052ded`, `a3d1b32`) and the body does not mention NEX-027 or NEX-028. Re-measure from the PR object before quoting any counter. |
| CI status | **`a3d1b32` (NEX-028): 4/4 `completed/success`, polled 2026-11-25T14:42Z** — Backend (ruff, mypy, pytest, migrations) and Frontend (typecheck, lint, test, build), push and pull_request events, all four reporting `head_sha` `a3d1b32`. An earlier poll at 14:40Z saw one duplicate check entry `in_progress`; the later read shows all four `completed`/`success`. The docs commit for this run will be superseded by the concurrency group and read `completed/cancelled` — neither pass nor fail, and not evidence. |
| Authorized to merge to `main` | No — automated merging is prohibited |

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

**R30 (new, low).** The stub is the only adapter, so the port's shape has been exercised
against one implementation that always answers cheaply. A real provider will be the first to
test whether the 400-character rationale bound and the 50-candidate cap fit a genuine vendor
response; if either is too tight, widening it is a contract change, not a tweak.

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
| Task | NEX-024 — aborting a run must seal the audit link the crash left open |
| Status | **VERIFIED** — 4/4 `completed/success` at `eaaae23`, polled 2026-11-25T12:16Z. All four `head_sha` values equal the polled commit, so this is a run at the slice's own SHA, not descendant-CI. |
| Next action | Read `ROADMAP.md` Phase 5 and select the next dependency-ready slice. |

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
| `pytest` (full) | **824 passed, 4 skipped** (was 794; the delta is exactly the 30 new NEX-028 tests) |
| Migration chain | no migration added; head unchanged. No schema change in this slice |
| Frontend `typecheck`/`lint`/`test`/`build` | not run locally this slice — no frontend files changed (CI ran them anyway and they passed) |

> **CI is the evidence.** Local gates above are for reproducibility, not proof (R9/R10).
> NEX-028 is **VERIFIED**: `a3d1b32`'s four check runs are 4/4 `completed/success`,
> every `head_sha` equal to `a3d1b32`, polled 2026-11-25T14:42Z.

## Blockers

None currently. The following are known open items, not blockers:

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
3. **Refresh PR #3.** Its body was last written at `fd223f4`; the branch is now five commits
   ahead (`8742faa`, `f2f8e61`, `fb8d5bd`, `8052ded`, `a3d1b32`) and the body mentions neither
   NEX-027 nor NEX-028. Re-measure additions/files/commits from the pull-request object at the
   current head (never a local diff) and verify byte-equality after the PATCH. This is the
   highest-value remaining action: it is the only artifact a human reviewer reads.
4. **Wire `ContextEnvelope` into a real caller** (R31). It is currently exercised only by tests.
   The first production caller — tool-gateway classification or the approval-recommendation
   path — is what proves the 200-char value cap and 32-field cap fit real content. Re-read the
   Phase 6 section of `ROADMAP.md` before choosing; do not invent a slice from a run summary.
5. **Confirm whether revoking a delegation or agent invalidates approvals already
   granted under it.** `RevokeDelegation` revokes the delegation and every token
   derived from it, immediately and ownership-checked, but this run did not establish
   whether a *granted* `ApprovalRequest` survives that revocation. If it does, an
   approval could outlive the authority that justified it. Read the approval and
   revocation paths together before deciding; do not assume either answer.

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

