# NEXUS State

Snapshot of where the platform is. Git and repository evidence are the source of
truth; this file records intent and reconciliation.

## Current position

| Field | Value |
| --- | --- |
| Project phase | Phase 1 — Organization & Identity foundation (in progress) |
| Branch | `feature/agentic-enterprise-platform` |
| Base commit (run start) | `e76e9c0` |
| Tracking issue | #2 (open) |
| Draft PR | #3 (draft, unmerged — human review required) |
| Authorized to merge to `main` | No — automated merging is prohibited |

## Active task

**NEX-006 — Tenant access enforcement.** Status: **TESTED** (all local gates
green), pending commit in this run. See `EXECUTION_LOG.md`.

## Last completed task

**NEX-002 — OIDC authentication** (authorization-code + PKCE, state/nonce,
JWKS-validated ID tokens, session cookies). **IMPLEMENTED and on the branch**;
the login-use-case and identity-provider suites cover it.

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
| `pytest` (full) | **409 passed, 4 skipped** |
| `pytest tests/architecture` | passed (layering invariant intact) |
| `ruff format` | clean |
| `ruff check` | clean |
| `mypy` | clean (82 source files) |
| Frontend `typecheck`/`lint`/`test`/`build` | not run this run — no frontend files touched |

> Local gates are **not** CI evidence. CI for this commit set has not been
> observed (see R3).

## Blockers

None currently. The following are known open items, not blockers:

- OIDC, agent identity/token exchange, delegated authority, tool gateway, Jev
  integration, and the admin console UI are **not yet implemented** — they are
  sequenced in `ROADMAP.md`, not regressions.

## Risks

- **R1 (medium):** PR #3 remains a draft while the branch carries real
  implementation. It must be brought up to date and described accurately before
  any human review, or reviewers will under-estimate what exists.
- **R2 (low):** The frontend has no admin console yet; the expansion spec's
  frontend testing requirements cannot be satisfied until that work lands.
- **R3 (low):** CI on the feature branch has not been observed for this commit
  set at the time of writing; local gates are green but that is not CI evidence.

## Next best actions

1. **NEX-003 — Agent identity & token exchange**: agent principals, delegated
   token attenuation (delegation never exceeds the delegator's authority),
   revocation. (P1/P2, depends on NEX-002 — now unblocked. An agent token
   resolves through the same bearer provider, so it inherits
   revocation-on-next-request automatically.)
2. **NEX-004 — Update PR #3** description and checklist to reflect the verified
   organization + OIDC + tenant-access work, and refresh tracking issue #2.
   (P4 hygiene, safe and immediate; also mitigates R1.)
3. **NEX-007 — Tenant-scoped capability checks**: extend the tenant seam to
   answer *which* tenants the caller may act in (`organization_ids` is already
   computed but unused by callers), so tenant-scoped use cases stop re-deriving
   membership themselves. (P1, builds directly on this slice.)
