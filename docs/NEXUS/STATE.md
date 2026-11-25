# NEXUS State

Snapshot of where the platform is. Git and repository evidence are the source of
truth; this file records intent and reconciliation.

## Current position

| Field | Value |
| --- | --- |
| Project phase | Phase 2 **complete** — agent identity & delegation all VERIFIED. Phase 5 (durable runtime): NEX-009 slice 1 VERIFIED (`0e74d4f`); NEX-009-HTTP VERIFIED (`e817d04`); NEX-011 audit trail TESTED |
| Branch | `feature/agentic-enterprise-platform` |
| Base commit (run start) | `a94d3f6` |
| Head commit (run end) | `f50a78d` — NEX-011 pushed this run |
| Last CI result | 4/4 green on `e817d04` (NEX-009-HTTP). On `f50a78d` (NEX-011): frontend 2/2 `completed/success`, backend 2 runs still `in_progress` — not concluded |
| Tracking issue | #2 (open) |
| Draft PR | #3 (draft, unmerged — human review required) |
| Authorized to merge to `main` | No — automated merging is prohibited |

## Active task

None in flight. **NEX-011 is TESTED, not VERIFIED** — the tool-invocation audit
trail is readable over HTTP and the full suite passes locally, but the backend CI
check runs were still `in_progress` on `f50a78d` when the run ended. Local green
runs are not CI evidence (R10); promoting on them would repeat the defect this
project has already recorded twice.

Next dependency-ready work is choosing the following Phase 5 slice — see Next
Best Actions and the Phase 5 section of `ROADMAP.md`.


## Last completed task

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

| Gate | Result (run `NEX-2026-11-25-11`, bare as CI runs them) |
| --- | --- |
| `ruff format --check` | clean — **189 files** (repo-wide, includes `docs/` Markdown fences) |
| `ruff check` | clean |
| `mypy` | clean (97 source files) |
| `pytest` (full) | **674 collected, 0 failures, 4 skipped** = 670 passed (up from 662 + 8 API) |
| Migration chain | unchanged this run — no new migration (read path over an existing table) |
| Frontend `typecheck`/`lint`/`test`/`build` | not run this slice — no frontend files changed (CI ran them green anyway) |

> **CI is the evidence.** The last commit with observed green CI is `e817d04`
> (4/4 check runs). This run's commit `f50a78d` is **TESTED only** — the frontend
> job was 2/2 `completed/success` but both backend runs were still `in_progress`
> when the run ended. Local gates are recorded above for reproducibility, not as
> proof (R10).

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

## Next best actions

1. **Observe CI on `f50a78d` (NEX-011), then promote it.** At the end of this run
   the frontend job was 2/2 `completed/success` and both backend runs were still
   `in_progress`. Run
   `gh api repos/anotherhale/bluestaq-notes/commits/f50a78d/check-runs`; promote
   NEX-011 to VERIFIED in `BACKLOG.md`, `STATE.md` and `CHECKPOINT.json` only if
   all four are `completed/success`. If a job failed, read the log and fix it —
   do not weaken assertions.
2. **Refresh the PR #3 body.** Measured on `f50a78d`: **24,087 insertions / 98
   files**. The body still claims 21,310 / 91 / 639 and names `0fcc078` as
   CI-verified. It has understated this branch on every run — always recompute
   with `git diff --shortstat main...HEAD` plus a fresh pytest collect, never copy
   the last number forward.
3. **Next dependency-ready slice (P2, Phase 5):** the durable runtime is reachable
   over HTTP and its audit trail is now readable, so the remaining Phase-5 work is
   what *consumes* those two surfaces. Re-read the Phase 5 section of `ROADMAP.md`
   before choosing; do not invent a slice from the last run's summary alone.

**Promote nothing on local gates alone.** NEX-009 slice 1, NEX-009-HTTP and
NEX-011 all reached TESTED on local runs and only two have gone on to VERIFIED,
each when CI went green on the commit. A local green suite is not evidence;
R9/R10 exist because that conflation happened before.

