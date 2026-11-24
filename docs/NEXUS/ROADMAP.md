# NEXUS Roadmap

Dependency-aware program roadmap for the Bluestaq Notes Agentic Enterprise
Platform. Sequence matters more than parallelism: later phases assume the
identity and authorization foundations of earlier ones.

Source of truth for scope: `docs/ENTERPRISE_AGENTIC_EXPANSION_SPEC.md`,
`docs/ENTERPRISE_ARCHITECTURE_MASTER.md`,
`docs/ENTERPRISE_SECURITY_ARCHITECTURE_V1.md`.

## Phase 0 — Foundations (COMPLETE)

Notes CRUD, folders, sharing, stars, search, sanitised rich text, demo auth,
optimistic concurrency. Verified by the existing suite.

## Phase 1 — Organization & Identity foundation (IN PROGRESS)

**Objective:** organizations, units, teams, memberships, managers, and the
authorization model that every later phase depends on.

| Milestone | Status | Notes |
| --- | --- | --- |
| M1.1 Organization hierarchy domain + persistence | COMPLETE | Migration `189c359206d3` |
| M1.2 Organization/team/member use cases + policy | COMPLETE | Last-admin and escalation guards |
| M1.3 Organization REST API | COMPLETE | `routers/organizations.py` |
| M1.4 Organization security tests | COMPLETE | `tests/security/test_org_admin_scope.py` |
| M1.5 OIDC authentication | NOT STARTED | NEX-002 |
| M1.6 Identity linkage to memberships | NOT STARTED | Depends on M1.5 |

**Acceptance criteria (phase):** an operator can create an organization, add
units and teams, assign managers, suspend a member (removing all authority
atomically), and the last administrator can never be removed or demoted. OIDC
login produces a session whose subject resolves to an organization membership.

## Phase 2 — Agent platform core

**Objective:** agent principals that are first-class, governed actors.

| Milestone | Status | Depends on |
| --- | --- | --- |
| M2.1 Agent identity & registration | NOT STARTED | M1.5 |
| M2.2 Token exchange with delegation attenuation | NOT STARTED | M2.1 |
| M2.3 Delegated authority / consent records | NOT STARTED | M2.2 |
| M2.4 Revocation | NOT STARTED | M2.2 |
| M2.5 Tool gateway (validated, audited tool invocation) | NOT STARTED | M2.2 |
| M2.6 Agentic runtime (durable runs, checkpointing) | NOT STARTED | M2.5 |

**Acceptance criteria (phase):** an agent can act only within a token whose
authority is a subset of its delegator's, every tool call is validated and
audited, revocation takes effect immediately, and a run resumes after
interruption without duplicating side effects.

## Phase 3 — Governance, audit, and Jev integration

| Milestone | Status | Depends on |
| --- | --- | --- |
| M3.1 Governance policy model + versioning | NOT STARTED | M1.2 |
| M3.2 Audit integrity (append-only, tamper-evident) | NOT STARTED | M2.5 |
| M3.3 Separation of duties / approval workflows | NOT STARTED | M3.1 |
| M3.4 System One / Jev classifier integration | NOT STARTED | M2.5 |

## Phase 4 — Admin console (frontend)

| Milestone | Status | Depends on |
| --- | --- | --- |
| M4.1 Org, identity, team management UI | NOT STARTED | M1.6 |
| M4.2 Access & integrations UI | NOT STARTED | M2.3 |
| M4.3 Policy & audit UI | NOT STARTED | M3.2 |

## Cross-cutting non-negotiables (apply to every phase)

- Domain and application layers import no framework, ORM, or JWT library.
- Default-deny authorization; inaccessible resources return 404, known-but-
  insufficient permission returns 403.
- No caller identity or owner id is ever read from a request body.
- AI never grants itself permission, bypasses policy, or approves its own
  sensitive action; enforcement is deterministic application code.
- Every mutation is auditable; migrations are idempotent and reversible.

## Risks

- **Sequencing risk:** implementing agent runtime before token attenuation would
  create a governed-looking platform with unattenuated authority. Order is
  non-negotiable.
- **Scope risk:** the spec is large; phases are deliberately sliced so each lands
  tested and integrated rather than scaffolded.
