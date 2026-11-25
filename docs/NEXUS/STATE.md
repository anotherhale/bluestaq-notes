# NEXUS State

Snapshot of where the platform is. Git and repository evidence are the source of
truth; this file records intent and reconciliation.

## Current position

| Field | Value |
| --- | --- |
| Project phase | Phase 2 — Agent identity & delegation (tool gateway VERIFIED; HTTP surface pending) |
| Branch | `feature/agentic-enterprise-platform` |
| Base commit (run start) | `7a392dd` |
| Tracking issue | #2 (open) |
| Draft PR | #3 (draft, unmerged — human review required) |
| Authorized to merge to `main` | No — automated merging is prohibited |

## Active task

**NEX-005-HTTP — Expose the tool gateway over HTTP.** Status: **TODO**. NEX-005
itself is **VERIFIED** at `a3ef9d7` (`74d3d98` fixed the two defects); the
gateway has no router, so no API contract test covers it. This is the last
gateway gap and closes R5. See `EXECUTION_LOG.md`.


## Last completed task

**NEX-008 — Add continuous integration** (run `NEX-2026-11-25-03`). The
repository had **no CI at all**; `.github/workflows/ci.yml` now runs the backend
gates (`ruff format --check`, `ruff check`, `mypy`, `pytest`), the frontend gates
(`typecheck`, `lint`, `test`, `build`), and an apply-then-revert migration check
on every push and pull request. Every step was verified locally first. Reduces
R3 to a residual: a green GitHub run is still required to close it fully.

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

| Gate | Result (run `NEX-2026-11-25-03`, at `7a392dd`) |
| --- | --- |
| `pytest` (full) | **603 passed, 4 skipped** (607 collected) |
| `pytest tests/architecture` | passed (layering invariant intact) |
| `ruff format --check` | clean (179 files) |
| `ruff check` | clean |
| `mypy` | clean (93 source files) |
| Frontend `typecheck`/`lint`/`test`/`build` | all passed (127 tests, 9 files) |
| `alembic upgrade head` → `downgrade base` | passed on fresh SQLite |

> Local gates are **not** CI evidence. This run *added* CI (NEX-008) but has not
> yet observed it run: the workflow file was pushed on this branch and CI
> evidence must be re-checked on the next run. Until a green run is observed,
> treat CI as **configured, unobserved**.

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
- **R3 (closed 2026-11-25, run `NEX-2026-11-25-03`):** The repository had **no CI
  configuration at all** — no `.github/workflows` on any branch, and zero check
  runs on any commit. `.github/workflows/ci.yml` now runs the backend gates
  (`ruff format --check`, `ruff check`, `mypy`, `pytest`), the frontend gates
  (`typecheck`, `lint`, `test`, `build`), and an `alembic upgrade head` →
  `downgrade base` cycle, on every push and pull request. Every step was
  verified locally before being written into the workflow. **Residual:** the
  workflow has not been observed green on GitHub yet; the next run must confirm
  it — a green run is what closes the risk fully, not the file's presence.
- **R4 (low):** `hypothesis` is not a project dependency, so the attenuation
  invariant is proved by exhaustive enumeration rather than property-based
  sampling. This is stronger for the current delegable universe but would not
  scale if the capability set grew substantially; revisit if capabilities
  multiply.
- **R5 (medium):** The tool gateway has no HTTP/router exposure. It is reachable
  only from application code, so no API contract test covers it.
- **R6 (closed 2026-11-25):** the tool gateway now has a dedicated test file,
  `tests/agent/test_tool_gateway.py` (18 tests). Writing it found two real
  defects, both fixed in `74d3d98`: a retried *denial* hit the
  `(agent_id, idempotency_key)` unique constraint and raised `IntegrityError`
  instead of replaying the recorded refusal, and a revoked delegation surfaced
  the domain `InvariantViolation` (a 500 at the edge) instead of a refusal.
  Do not delete that file; both bugs return silently without it.


## Next best actions

1. **Verify CI on GitHub (finishes NEX-008)**: `.github/workflows/ci.yml` was
   pushed this run but has not been observed running. Check `gh run list
   --branch feature/agentic-enterprise-platform` and fix any failure. Do not
   record CI as passing until a green run exists. (P1, closes R3 fully.)
2. **NEX-005-HTTP — Expose the tool gateway through the API layer**: a router,
   an auth dependency that resolves the agent token for `TokenAudience.TOOL_GATEWAY`,
   and an API contract test for a denied invocation. (P2; closes R5, the last
   gateway gap.)
3. **NEX-007 — Tenant-scoped capability checks**: extend the tenant seam to
   answer *which* tenants the caller may act in (`organization_ids` is already
   computed but unused by callers), so tenant-scoped use cases stop re-deriving
   membership themselves. (P1, builds on NEX-006.)

