# NEXUS State

Snapshot of where the platform is. Git and repository evidence are the source of
truth; this file records intent and reconciliation.

## Current position

| Field | Value |
| --- | --- |
| Project phase | Phase 1 — Organization & Identity foundation (in progress) |
| Branch | `feature/agentic-enterprise-platform` |
| Base commit (run start) | `2b54ca5` |
| Tracking issue | #2 (open) |
| Draft PR | #3 (draft, unmerged — human review required) |
| Authorized to merge to `main` | No — automated merging is prohibited |

## Active task

**NEX-001 — Organization hierarchy vertical slice: close out the failing
quality-gate suite.**

Status: **VERIFIED** (all gates green locally), **COMMITTED** and **PUSHED**
tracked in `EXECUTION_LOG.md`.

## Last completed task

**NEX-001.** Diagnosed and fixed two defects that blocked the organization
vertical slice from passing the gate suite:

1. `OrganizationUseCase` was referenced in `bootstrap/container.py` without being
   imported — this broke *every* test that built the container (121 setup
   errors, all one root cause).
2. `SqlAlchemyTeamMembershipRepository.list_for_team` returned soft-deleted
   (`DEPROVISIONED`) rows, so `DELETE .../teams/{id}/members/{id}` answered 204
   while the member still appeared in the roster.

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

| Gate | Result |
| --- | --- |
| `pytest` (full) | **320 passed, 4 skipped** |
| `pytest tests/architecture` | 5 passed (layering invariant intact) |
| `ruff format --check` | clean (142 files) |
| `ruff check` | clean |
| `mypy` | clean (73 source files) |
| Frontend `typecheck`/`lint`/`test`/`build` | see EXECUTION_LOG for run result |

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

1. **NEX-002 — OIDC authentication**: add authorization-code + PKCE flow with
   state/nonce validation, secure cookie handling, and identity linkage to
   organization memberships. (P1, unblocks agent identity.)
2. **NEX-003 — Agent identity & token exchange**: agent principals, delegated
   token attenuation (delegation never exceeds the delegator's authority),
   revocation. (P1/P2, depends on NEX-002.)
3. **NEX-004 — Update PR #3** description and checklist to reflect the verified
   organization slice, and refresh the tracking issue #2 status. (P4 hygiene,
   safe and immediate.)
