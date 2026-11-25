# NEXUS State

Snapshot of where the platform is. Git and repository evidence are the source of
truth; this file records intent and reconciliation.

## Current position

| Field | Value |
| --- | --- |
| Project phase | Phase 2 — Agent identity & delegation (tool gateway VERIFIED incl. HTTP surface; tenant-scoped capability answer VERIFIED; OIDC verifier now TESTED) |
| Branch | `feature/agentic-enterprise-platform` |
| Base commit (run start) | `e93a3e9` |
| Head commit (run end) | _set after commit_ |
| Tracking issue | #2 (open) |
| Draft PR | #3 (draft, unmerged — human review required) |
| Authorized to merge to `main` | No — automated merging is prohibited |

## Active task

None in flight. **NEX-010** is **TESTED** (run `NEX-2026-11-25-07`) — committed
and pushed, local gates green; it becomes **VERIFIED** when CI is green on the
commit carrying it. The next dependency-ready task is **NEX-009** (durable
agentic runtime); see Next best actions.


## Last completed task

**NEX-010 — Direct tests for the OIDC token verifier** (run `NEX-2026-11-25-07`).
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

NEX-002 moves **IMPLEMENTED → TESTED** on this evidence.

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

| Gate | Result (run `NEX-2026-11-25-07`, pre-commit) |
| --- | --- |
| `pytest` (full) | **639 passed, 4 skipped** (643 collected, 0 failures/errors) |
| `pytest tests/architecture` | passed (layering invariant intact) |
| `ruff format --check` | clean |
| `ruff check` | clean |
| `mypy` | clean (94 source files) |
| Frontend `typecheck`/`lint`/`test`/`build` | passed at `3f1d855` (127 tests, 9 files); no frontend files changed since |
| `alembic upgrade head` → `downgrade base` | passed on fresh SQLite (unchanged this run) |

> Local gates are **not** CI evidence. CI is green at `e1cb0c5` (4/4 check runs
> `completed/success`); this run's commit still needs its own CI result before
> NEX-010 and NEX-002 can be called **VERIFIED**. CI earned that trust earlier by
> catching a real defect on its first execution — see the R3 entry and NEX-008 in
> `BACKLOG.md`.

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


## Next best actions

1. **NEX-009 — Durable agentic runtime** (renumbered from a duplicate NEX-006 id):
   runs checkpoint progress and resume after interruption without repeating
   completed side effects. NEX-005, NEX-007, and now NEX-010 are done, so this is
   the highest-value dependency-ready task — but it is *large*. Scope the first
   slice to run-checkpoint persistence plus a resume test rather than attempting
   the whole task. (P2.)
2. **Confirm CI on the NEX-010 commit.** Once the run that carries
   `tests/authentication/test_oidc_relying_party.py` reports green check runs,
   move **NEX-010** and **NEX-002** from TESTED to VERIFIED. Do not promote them
   on local gates alone — that is exactly the conflation R9 was filed about.
3. **Keep PR #3 honest as slices accumulate.** The body records 20,576 insertions
   / 89 files / 614 tests as of `e1cb0c5`; this run adds a test module and a
   production fix. Re-check `git diff --stat main...HEAD` against the body
   whenever a slice lands, because the body has understated the branch before.

