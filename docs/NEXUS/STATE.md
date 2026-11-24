# NEXUS State

Snapshot of where the platform is. Git and repository evidence are the source of
truth; this file records intent and reconciliation.

## Current position

| Field | Value |
| --- | --- |
| Project phase | Phase 2 — Agent identity & delegation (tool gateway landed) |
| Branch | `feature/agentic-enterprise-platform` |
| Base commit (run start) | `e5f1bb4` |
| Tracking issue | #2 (open) |
| Draft PR | #3 (draft, unmerged — human review required) |
| Authorized to merge to `main` | No — automated merging is prohibited |

## Active task

**NEX-005 — Tool gateway.** Status: **TESTED** (all local gates green,
migration applied to a fresh SQLite database), committed as `bf70f40` and
pushed. HTTP/router exposure was deliberately deferred. See `EXECUTION_LOG.md`.


## Last completed task

**NEX-006 — Tenant access enforcement** (`4c5a6ef`). Authentication proves
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

| Gate | Result (run `NEX-2026-11-24-01`) |
| --- | --- |
| `pytest` (full) | **585 passed, 4 skipped** |
| `pytest tests/architecture` | passed (layering invariant intact) |
| `ruff format --check` | clean (175 files) |
| `ruff check` | clean |
| `mypy` | clean (92 source files) |
| Frontend `typecheck`/`lint`/`test`/`build` | not run this run — no frontend files touched |

> Local gates are **not** CI evidence. CI for this commit set has not been
> observed (see R3).

## Blockers

None currently. The following are known open items, not blockers:

- Tool gateway, Jev integration, and the admin console UI are **not yet
  implemented** — they are sequenced in `ROADMAP.md`, not regressions.

## Risks

- **R1 (medium):** PR #3 remains a draft while the branch carries real
  implementation. It must be brought up to date and described accurately before
  any human review, or reviewers will under-estimate what exists.
- **R2 (low):** The frontend has no admin console yet; the expansion spec's
  frontend testing requirements cannot be satisfied until that work lands.
- **R3 (low):** CI on the feature branch has not been observed for this commit
  set at the time of writing; local gates are green but that is not CI evidence.
- **R4 (low):** `hypothesis` is not a project dependency, so the attenuation
  invariant is proved by exhaustive enumeration rather than property-based
  sampling. This is stronger for the current delegable universe but would not
  scale if the capability set grew substantially; revisit if capabilities
  multiply.
- **R5 (medium):** The tool gateway has no HTTP/router exposure. It is reachable
  only from application code, so no API contract test covers it.
- **R6 (medium):** The tool gateway has no dedicated test file. It is covered
  only indirectly, by the full suite passing and by the migration test. Idempotent
  replay and deny-path auditing are unproven by a focused assertion.


## Next best actions

1. **NEX-005-followup — Tool gateway tests + HTTP exposure**: add focused tests
   for idempotent replay, deny-path auditing, and digest stability (R6), then
   expose the gateway through the API layer. (P2, the gateway is the seam every
   agent action will flow through; it currently has no direct test and no route.)
2. **NEX-004 — Update PR #3** description and checklist to reflect the verified
   organization + OIDC + tenant-access + agent-identity + tool-gateway work, and
   refresh tracking issue #2. (P4 hygiene, safe and immediate; also mitigates R1.)
3. **NEX-007 — Tenant-scoped capability checks**: extend the tenant seam to
   answer *which* tenants the caller may act in (`organization_ids` is already
   computed but unused by callers), so tenant-scoped use cases stop re-deriving
   membership themselves. (P1, builds on NEX-006.)

