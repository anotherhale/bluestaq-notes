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
| M1.3a Organization roster excludes offboarded people | **VERIFIED** (NEX-097, `febb810`; CI 4/4 on the product commit itself) | Roster filters only the terminal `DEPROVISIONED` state so suspended members stay reactivatable |
| M1.4 Organization security tests | COMPLETE | `tests/security/test_org_admin_scope.py` |
| M1.5 OIDC authentication | NOT STARTED | NEX-002 |
| M1.6 Identity linkage to memberships | NOT STARTED | Depends on M1.5 |

**Acceptance criteria (phase):** an operator can create an organization, add
units and teams, assign managers, suspend a member (removing all authority
atomically), and the last administrator can never be removed or demoted. OIDC
login produces a session whose subject resolves to an organization membership.

## Phase 2 — Agent platform core

**Objective:** agent principals that are first-class, governed actors.

Status below is a phase-level summary; `BACKLOG.md` and `CHECKPOINT.json` are authoritative
for per-slice detail and for what is *verified* versus merely implemented.

| Milestone | Status | Depends on |
| --- | --- | --- |
| M2.1 Agent identity & registration | VERIFIED (NEX-003) | M1.5 |
| M2.2 Token exchange with delegation attenuation | VERIFIED (NEX-003, NEX-005-HTTP) | M2.1 |
| M2.3 Delegated authority / consent records | VERIFIED (NEX-006, NEX-007) | M2.2 |
| M2.4 Revocation | VERIFIED (NEX-014, NEX-017) | M2.2 |
| M2.5 Tool gateway (validated, audited tool invocation) | VERIFIED (NEX-005, NEX-005-followup, NEX-011, NEX-012, NEX-018) | M2.2 |
| M2.6 Agentic runtime (durable runs, checkpointing) | **VERIFIED** — durable runs, approval validity, decided-approval visibility, approval gating, run write concurrency, crash/abort audit sealing, approval revocation, the approval clock (NEX-026), provider-neutral decisioning (NEX-027) and context assembly (NEX-028) are all VERIFIED. NEX-026 at `b46e2e3`, NEX-027 at `fb8d5bd`, NEX-028 at `a3d1b32` — each 4/4 `completed/success` on observed CI. Decisioning and context assembly, the last two named items, are both done. | M2.5 |

**Acceptance criteria (phase):** an agent can act only within a token whose
authority is a subset of its delegator's, every tool call is validated and
audited, revocation takes effect immediately, and a run resumes after
interruption without duplicating side effects.

## Phase 3 — Governance, audit, and Jev integration

| Milestone | Status | Depends on |
| --- | --- | --- |
| M3.1 Governance policy model + versioning | **VERIFIED** — typed AST with immutable published versions (NEX-047 `379a98a`), durable `PolicyVersionRepository` (NEX-048), lifecycle + three-way SoD on publish (NEX-065, NEX-078 `6637c50`) | M1.2 |
| M3.2 Audit integrity (append-only, tamper-evident) | **VERIFIED** — per-agent hash-chained trails; deployment-wide operator sweep (NEX-042), now pageable via `offset`/`next_offset` so a capped sweep can be finished rather than excluding the newest trails forever (NEX-081 `3a05db2`). This table said NOT STARTED long after both slices had shipped. | M2.5 |
| M3.3 Separation of duties / approval workflows | **VERIFIED** — author/approver/publisher all pairwise distinct, enforced in domain, use cases and a DB CHECK (NEX-078); a real second-approver control (NEX-059); risk-based approval with a threshold floor (NEX-063, ADR-016); assurance held to two independent factor families (NEX-067) | M3.1 |
| M3.4 System One / Jev classifier integration | **VERIFIED, with no live-vendor default** — an advisory classifier reaches deterministic policy through the gateway; no default code path calls a live vendor (NEX-040) | M2.5 |

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
