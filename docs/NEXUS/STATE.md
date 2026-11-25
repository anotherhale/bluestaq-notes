# NEXUS State

Snapshot of where the platform is. Git and repository evidence are the source of
truth; this file records intent and reconciliation.

## Current position

| Field | Value |
| --- | --- |
| Project phase | Phase 2 **complete** — agent identity & delegation all VERIFIED. Phase 5 (durable runtime) **slice 1 VERIFIED**: NEX-009 durable runs, CI green on `0e74d4f` (both events) |
| Branch | `feature/agentic-enterprise-platform` |
| Base commit (run start) | `759f92a` |
| Head commit (run end) | `0e74d4f` — durable agent runs, pushed and CI-verified |
| Last CI result | **4/4 green** on `0e74d4f` — push and pull_request events both `completed/success` |
| Tracking issue | #2 (open) |
| Draft PR | #3 (draft, unmerged — human review required) |
| Authorized to merge to `main` | No — automated merging is prohibited |

## Active task

None in flight. **NEX-009 slice 1 is VERIFIED.** Durable agent runs are committed
at `0e74d4f`, pushed, and **all four check runs** — backend and frontend, on both
the push and pull_request events — report `completed/success`. CI ran the full
suite on the commit that added the tests, which is the standard this repository
has held since NEX-010. It is not a local green run.

Slice 2 is **not started**: the run use cases are not yet exposed over HTTP.


## Last completed task

**NEX-009 slice 1 — Durable agentic runtime** (run `NEX-2026-11-25-09`, `0e74d4f`).
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

| Gate | Result (run `NEX-2026-11-25-09`, bare as CI runs them) |
| --- | --- |
| `ruff format --check` | clean — **186 files** (repo-wide, includes `docs/` Markdown fences) |
| `ruff check` | clean |
| `mypy` | clean (96 source files) |
| `pytest` (full) | **651 passed, 4 skipped** locally; **CI 4/4 green on `0e74d4f`** |
| Migration chain | `alembic upgrade head` → `downgrade base` clean, including new `8a4c2e1f9b73_agent_runs` |
| Frontend `typecheck`/`lint`/`test`/`build` | green on `0e74d4f` (no frontend files changed) |

> **CI is the evidence.** On `0e74d4f` all four check runs — backend and frontend,
> on both the push and pull_request events — report `completed/success`. This run
> first observed only the push result and recorded that honestly; the
> pull_request job finished green before the run closed, and this file was
> corrected on that evidence rather than left at the weaker claim. Local gates
> are recorded above for reproducibility, not as proof (R10).

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
- **R10 (low, process — new this run):** a green local gate run is only evidence
  if it invokes the *same command CI invokes*. The pre-push run for `2dd7a0c` was
  scoped to `src tests`, so it skipped `docs/`, where ruff then failed the build
  on a Markdown-embedded Python fence. Run the gates bare. Recorded in
  `AGENTS.md` so the next run does not relearn it.

## Next best actions

1. **NEX-009 slice 2 — expose durable runs over HTTP.** The use cases exist and
   are tested but nothing in `presentation/` calls them, so no client can plan,
   advance, or resolve a run. Mirror NEX-005 → NEX-005-HTTP: thin routers over
   `PlanAgentRun` / `AdvanceAgentRun` / `ResolveInterruptedStep` / `AbortAgentRun`
   / `GetAgentRun` / `ListAgentRuns`, with the agent token — not the demo-identity
   header — as the authority, exactly as `test_tool_gateway_api.py` pins for the
   gateway. Refusals must be 401/403/404/409/422, never 500. (P2.)
2. **Refresh the PR #3 body again.** It records 21,310 insertions / 91 files / 643
   tests; the branch is now **22,819 / 95 / 655** after NEX-009 slice 1. The body
   has understated this branch three times now — always recompute with
   `git diff --shortstat main...HEAD` plus a fresh collect, never copy the last
   number forward.
3. **Promote nothing on local gates alone.** NEX-009 slice 1 reached VERIFIED only
   when *both* CI events went green on `0e74d4f`. This run had already written
   "push event only" and then corrected it on the pull_request result — the right
   order, and worth repeating. A local green suite is not evidence; R9/R10 exist
   because that conflation happened before.

