# NEXUS State

Snapshot of where the platform is. Git and repository evidence are the source of
truth; this file records intent and reconciliation.

## Current position

| Field | Value |
| --- | --- |
| Project phase | Phase 2 **complete** — agent identity & delegation all VERIFIED (NEX-003 promoted this run). Phase 5 (durable runtime): NEX-009 slice 1 VERIFIED (`0e74d4f`); NEX-009-HTTP VERIFIED (`e817d04`); NEX-011 audit trail VERIFIED (`f50a78d`); NEX-012 audit chain VERIFIED (`3aade7c`, CI 4/4); NEX-013 approval validity **VERIFIED** (`12584a1`, CI 4/4 — after a correction); NEX-014 revocation VERIFIED (`b27b6b5`, CI 4/4); NEX-015 decided-approval visibility **VERIFIED** (`414a222`, CI 4/4); NEX-016 approval-gated steps park instead of failing the run **VERIFIED** (`b9b7207`, CI 4/4); NEX-017 delegator tenant-access re-check **VERIFIED** (`990557d`, CI 4/4) |
| Branch | `feature/agentic-enterprise-platform` |
| Base commit (run start) | `ad21e5b` — the clone started on `main` at `d6f92d0`; `git ls-remote` showed the authorized branch at `ad21e5b`, which was fetched and checked out before any gate was trusted. Verified with `git log -1` and against `origin/feature/agentic-enterprise-platform`. |
| Head commit (run end) | the commit carrying this line — `feat(agent): reject a terminal verdict with no outcome seal` (NEX-018), plus its docs commit. Re-read `git log -1`; do not assume. |
| Last CI result | **No CI result has been read for the NEX-018 commit.** Green on `4ff4f62`, 4/4 `completed/success`, every run reporting `head_sha` `4ff4f62`, polled 2026-11-25T09:05Z — that is the previous run's evidence and does not cover this run's code change. Locally this run **on the feature branch**: `ruff format --check` 197 files clean, `ruff check` clean, `mypy` 98 files clean, full `pytest` 722 passed / 4 skipped. |
| Tracking issue | #2 (open) |
| Draft PR | #3 (draft, unmerged — human review required). Head `ad21e5b` locally at run start; the NEX-018 commits are newer and not yet reflected in the body. Body last **refreshed 2026-11-25T08:46:30Z** at that head: **106 files, 28,409 insertions, 10 deletions, 89 commits**, 720 tests collected, 10 migrations, 11 routers. Counters measured from the PR object at `fa387a9`. |
| CI status | **NEX-018 commit: not yet polled.** `fa387a9`: 4/4 `completed/success`; `ca15eb1`: 4/4 `completed/success`; `990557d`: 4/4; `b9b7207`: 4/4. `e5f1bb4` (NEX-003) and `4c5a6ef` (NEX-006) have **no check runs of their own** — they are ancestors of the green heads above. Docs heads superseded by a later push read `completed/cancelled`, which is not the same as passing. |
| Authorized to merge to `main` | No — automated merging is prohibited |

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

Status: **IMPLEMENTED + TESTED locally. Not VERIFIED** — no CI result has been
read for the commit. Six domain tests in `tests/agent/test_audit_chain.py`,
including the honest unfinished-`started` case, so the fix cannot be "pass" by
refusing everything.

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

1. **PR #3 body is current at `fa387a9`** — 28,409 additions / 10 deletions /
   106 files / 89 commits, 720 tests, 10 migrations, 11 routers, all measured
   from the PR object at that head (refreshed 2026-11-25T08:46:30Z). It names
   `b9b7207`, `990557d`, `ca15eb1` and `fa387a9` as CI-verified commits. Keep it
   current: it had been stale for several runs and carried numbers wrong in two
   directions.
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


## Active task

| Field | Value |
| --- | --- |
| Task | none - NEX-015 closed; next slice not yet selected |
| Status | idle, budget remaining |
| Next action | Select the next dependency-ready Phase-5 slice from ROADMAP.md. PR #3 body is current as of `7ce6f8b` (27,517 / 10 / 104, 715 tests, seven VERIFIED slices). |

## Last completed task

**NEX-015 (VERIFIED, `414a222`, CI 4/4)** - a decision erased the approval record from the
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

Pinned by `tests/api/test_audit_trail_api.py` (8 tests). **Status: TESTED, not
VERIFIED** — backend CI had not concluded on the commit.

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
in `tests/agent/test_agent_runs.py` (14 total). **Status: TESTED, not VERIFIED** —
CI has not yet run on the commit.

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

| Gate | Result (run `NEX-2026-11-25-13`, bare as CI runs them) |
| --- | --- |
| `ruff format --check` | clean — **193 files** (repo-wide, includes `docs/` Markdown fences) |
| `ruff check` | clean |
| `mypy` | clean (98 source files) |
| `pytest` (full) | **684 collected, 0 failures, 4 skipped** = 680 passed (up from 677 + 3) |
| Migration chain | new head `d4c8e2a71f05`; verified upgrade → downgrade → upgrade round-trip and the backfill on a **non-empty** table |
| Frontend `typecheck`/`lint`/`test`/`build` | not run this slice — no frontend files changed |

> **CI is the evidence.** The last commit with observed green CI is `e2bf7bc`
> (run [45559723156](https://github.com/anotherhale/bluestaq-notes/actions/runs/45559723156),
> `completed/success`), which contains NEX-013. NEX-013 is therefore VERIFIED.
> Local gates are recorded above for reproducibility, not as proof (R10).

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

1. **Observe CI on `12584a1` before promoting NEX-013 again.**
   `gh api repos/anotherhale/bluestaq-notes/commits/12584a1/check-runs` — only a
   green 4/4 makes NEX-013 VERIFIED. It was marked VERIFIED once while its bound
   was inert; do not repeat that on local gates.
2. **Next dependency-ready slice (P2, Phase 5).** The durable runtime is
   reachable over HTTP, its audit trail is readable and tamper-evident, and
   approvals are now actually bounded and single-use. Re-read the Phase 5 section
   of `ROADMAP.md` before choosing; do not invent a slice from a run summary.
3. **Keep the PR #3 body measured, not remembered.** It has understated this
   branch on every recent run and now also predates the NEX-013 correction.
   Recompute with `git diff --shortstat main HEAD` (two-dot: the clone is grafted,
   `main...HEAD` has no merge base) and `pytest --collect-only -o addopts=""`.

**Check the adapter, not just the aggregate (R18).** NEX-013 was VERIFIED while
`ApprovalRequestRepository.add()` silently dropped the two columns the whole
invariant depended on, because the expiry test used `save()`. When a slice adds
a field that a security check reads, pin it through the write path production
actually uses.

**Promote nothing on local gates alone.** NEX-009 slice 1, NEX-009-HTTP, NEX-011
and NEX-012 each reached TESTED on local runs and then VERIFIED only when CI went
green on the commit (`0e74d4f`, `e817d04`, `f50a78d`, `3aade7c`). A local green
suite is not evidence; R9/R10 exist because that conflation happened before.

