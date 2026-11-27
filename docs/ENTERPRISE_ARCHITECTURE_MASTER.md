# Enterprise Agentic Architecture Master

**Status:** Consolidated target architecture / accepted design baseline  
**Repository:** `anotherhale/bluestaq-notes`  
**Branch:** `feature/agentic-enterprise-platform`  
**Scope:** Enterprise collaboration, agent runtime, AI decisioning, context/knowledge, governance, workflow, UX, and operations  
**Implementation status:** Design only unless verified in code. This document does not assert implementation completion.

This master consolidates the expansion specification, current architecture, security architecture v1, and accepted design pillars. It supersedes overlapping proposal-level descriptions as the primary architecture reference. Existing ADRs and detailed domain/security documents remain authoritative for their focused topics; disagreements must be resolved explicitly rather than silently.

## 1. Product Vision

Evolve Bluestaq Notes from a secure notes collaboration product into an enterprise knowledge and agentic-work platform where people, teams, organizations, AI agents, and services work together under governed authority.

Primary capabilities:
- Multi-tenant organizations, organizational units, teams, employee/member directory, reporting relationships, and administration.
- Secure enterprise workspace for notes, knowledge, collaboration, AI-assisted work, agent runs, human tasks, and approvals.
- Governed agents that plan, execute bounded tasks, use approved tools, and produce traceable results.
- Human and agent identity, scoped permissions, delegation, policy, approvals, audit, and revocation.
- Context and knowledge retrieval with authorization filtering, classification, trust, provenance, and budgets.
- Intelligence provider abstraction for LLM generation and structured decisions (including Jev/System One).
- Enterprise Control Center for identity, organization, agent, capability, governance, audit, and operations.

## 2. Architecture Principles

1. Modular monolith first; extraction is evidence-driven.
2. Domain-Driven Design with bounded contexts and explicit contracts.
3. Hexagonal architecture: domain and application are framework-independent.
4. UI, REST, MCP, agent, and integration adapters invoke the same application use cases.
5. Authentication establishes identity; application authorization decides access per action/resource.
6. Agents and external content are untrusted.
7. Least privilege, default deny, tenant isolation, and revocation are invariants.
8. AI judgment is advisory; deterministic policy and application code enforce security and correctness.
9. Approval authorizes a specific action; it does not execute it or bypass revalidation.
10. Audit intent and protected mutation are transactionally coordinated.
11. Published governance artifacts are immutable and versioned.
12. Context is assembled for purpose, authorized before model delivery, classified, provenance-aware, and budgeted.
13. Failure must not silently weaken security.
14. Architecture status must be labeled honestly: target, accepted design, implemented, tested, and deployed are distinct.

## 3. Logical Bounded Contexts

These are logical modules within the monolith, not immediate microservices.

| Context | Responsibilities |
|---|---|
| Identity & Authentication | OIDC, account lifecycle, sessions, external identity links, authentication assurance |
| Organization | Tenants, hierarchy, units, teams, membership, reporting, lifecycle/reorganization |
| Notes & Knowledge | Notes, folders, sharing, knowledge resources, ownership and resource access |
| Authorization & Access | Policy decision/enforcement, roles, scoped assignments, ACLs, capabilities, attributes, obligations, delegation |
| Agent Platform | Definitions, versions, runs, tasks, plans, steps, checkpoints, leases, budgets, orchestration |
| AI Decisioning | Provider routing, generation, structured decisions, evaluation, abstention, model governance |
| Tool & Capability Gateway | Registry, schemas, invocation, authorization, obligations, budgets, validation, execution |
| Context & Knowledge Platform | Context requests/packages, retrieval, memory, ingestion, provenance, classification, snapshots |
| Workflow & Human Collaboration | Approval, human tasks, review, clarification, handoff, escalation, resume |
| Audit & Integrity | Audit events, outbox, ledger, integrity chain, checkpoints, verification, exports |
| Integrations | IdP, external knowledge sources, messaging, MCP adapters and provider adapters |
| Platform Operations | Health, runtime controls, budgets, incident response, recovery, operational telemetry |

## 4. Organization & Identity Model

### 4.1 Organization hierarchy
- `Organization` is the tenant/root boundary.
- `OrganizationUnit` represents configurable hierarchical nodes such as division, department, branch, program, or business unit.
- `Team` belongs to an organization unit. Team nesting is optional and must not create a second competing organizational hierarchy.
- Hierarchy uses one canonical model (adjacency representation with cycle prevention; closure/indexing can be added as an optimization).
- Organizational structure, team membership, and reporting relationships are separate concepts.

### 4.2 Identity/person model
- Internal `UserId` is the durable application identity.
- `ExternalIdentity` is keyed by `(issuer, subject)`; email is not a durable key.
- `Employee`/person profile is organization-scoped and may link to a user; avoid conflating a human profile with authentication identity.
- Support employees, contractors, guests, and service identities without assuming every person is an employee.
- A person can participate in multiple organizations and teams where policy permits.
- `OrganizationMembership`, `UnitMembership`, `TeamMembership`, and scoped `ManagerRelationship` are explicit and lifecycle-aware.
- Membership/reporting links are validated, tenant-scoped, and transactional; reporting cycles are prohibited.

### 4.3 Lifecycle
Membership, identity, team, unit, and employment states support active, suspended, ended, and deprovisioned semantics as appropriate. Suspension/deprovisioning must revoke access promptly and invalidate relevant sessions, delegation, authorization caches, and agent access. Reorganization changes are transactional and auditable; history is retained.

### 4.4 Baseline roles
Organization: `ORG_OWNER`, `ORG_ADMIN`, `ORG_SECURITY_ADMIN`, `ORG_AUDITOR`, `ORG_MEMBER`.  
Unit: `UNIT_MANAGER`, `UNIT_MEMBER`.  
Team: `TEAM_LEAD`, `TEAM_MEMBER`.

Roles are capability bundles, scoped assignments are explicit, and role inheritance is not blanket. Administrative metadata authority does not imply private note access. Ownership transfer and emergency authority are protected workflows.

### 4.5 Identity integration
Production human login uses OIDC Authorization Code + PKCE. Validate issuer, audience, signature/JWKS rotation, expiry, `nbf`, state, nonce, and required claims. Support secure session lifecycle, logout/revocation, claim mapping, account linking, provisioning, and step-up authentication. SCIM and IdP group synchronization are future adapters behind ports.

Development/demo authentication remains isolated and cannot be enabled in production. Authentication failure never downgrades to a weaker identity provider.

## 5. Actor & Authorization Architecture

### 5.1 Actor types
- Human
- Agent
- Service

Actor identity is separate from delegated-by identity. Server-derived context includes actor/type, tenant, status, credential/session, authentication assurance, delegation, task/run, scope, risk, and correlation identifiers. Client-supplied actor, tenant, or delegator claims cannot widen authority.

### 5.2 Unified authorization
A single structured decision combines:
- RBAC and scoped RBAC
- ABAC over trusted subject/resource/action/environment attributes
- resource ACLs/grants
- capability registry and capability grants
- scoped administrative authority
- delegation
- required obligations

Semantics:
- Default deny.
- Mandatory trust, tenant, actor status, and delegation boundaries cannot be overridden.
- Applicable explicit deny overrides ordinary grants.
- Missing trusted attributes do not satisfy conditions.
- Unknown actions, capabilities, conditions, obligations, or policy evaluation errors fail closed.
- Scope inheritance is capability-specific and explicit.
- No cross-tenant grant.
- Admin status, manager/report relationship, and team membership do not automatically expose private content.
- Grantors cannot grant authority beyond their own scope/authority.
- Obligations (step-up, approval, SoD, redaction, limits, audit) must be fulfilled before execution.

### 5.3 Logical components
- **PDP:** policy evaluation and structured decisions.
- **PEP:** application use-case enforcement.
- **PIP:** trusted attribute and provenance acquisition.
- **PAP:** policy authoring, validation, versioning, approval, publication.
- **Capability resolver:** registry and grant-source resolution.
- **Scope resolver:** tenant/org/unit/team/resource boundaries.

These are ports/modules in the modular monolith initially. Routes may add defense in depth but are not the authoritative policy layer.

### 5.4 Decision contract
`AuthorizationDecision` records effect, actor/delegator, tenant, action, resource, scope, reason code, matched policy/version, grant source, obligations, attribute provenance, risk, task/correlation, and timestamp. Sensitive decision details are not exposed to policy probing.

### 5.5 Versioning/revocation
Roles and policies are immutable published versions. Assignments pin explicit versions or use a controlled migration cohort. Revocation supersedes stale grants, caches, snapshots, and delegations. Protected actions revalidate authoritative state; caches are optimizations only.

## 6. Agent Runtime & Orchestration

### 6.1 Canonical entities
`AgentDefinition`, `AgentVersion`, `AgentRun`, `AgentTask`, `AgentPlan`, `PlanRevision`, `ExecutionStep`, `ToolInvocation`, `Checkpoint`, `ApprovalGate`, `AgentRunResult`.

A run is not a task; a task is not a step; a tool invocation is not a plan.

### 6.2 Lifecycle
Typical states: CREATED → AUTHENTICATING → CONTEXT_BUILDING → PLANNING → POLICY_CHECK → EXECUTING → EVALUATING, with bounded execution/evaluation loops and terminal COMPLETED, REJECTED, FAILED, or CANCELLED. WAITING_APPROVAL is a durable paused state. Exact transition rules are defined in the runtime state machine.

### 6.3 Orchestration
- One coordinator per run; bounded task workers/subagents.
- Agent coordination is task/artifact based, not unrestricted agent-to-agent chatter.
- Plan snapshots are immutable; amendments create revisions.
- Approvals bind to the exact plan revision and task context.
- Persist run/task/step/tool history and checkpoints.
- Leases/claims prevent concurrent duplicate execution.
- Idempotency keys protect retry/replay.
- Retry only safe/idempotent actions; irreversible effects require explicit safeguards.
- Cancellation, timeout, budget exhaustion, and failure are durable and recoverable.

### 6.4 Budget and limits
Enforce limits for run duration, tokens/cost, tool calls, steps, parallelism, delegation depth, and per-tool rate/volume. Budget policy is deterministic and auditable.

### 6.5 Runtime planes
Keep control-plane functions (definitions, policy, budgets, approvals, scheduling, lifecycle) logically separate from execution-plane functions (workers, tool calls, context consumption), while remaining in the monolith until extraction is justified.

## 7. Tool & Capability Gateway

Initial tools may include: `search_notes`, `get_note`, `list_shared_notes`, `list_teams`, `get_team_context`, `create_note`, `update_note`, `append_to_note`, and future `create_action_items`, `share_note`, `revoke_share`, `delete_note`.

Every invocation follows this pipeline:

1. Validate tool name/version and typed input schema.
2. Resolve trusted actor, tenant, delegation, run/task, and context.
3. Resolve immutable capability definition.
4. Evaluate authorization and resource access.
5. Evaluate policy obligations and approval gates.
6. Enforce budget, rate, timeout, and concurrency.
7. Invoke an existing application use case (never direct DB/repository access).
8. Validate, minimize, and classify result.
9. Record outcome/audit and correlate with run/task/tool IDs.
10. Return bounded output to the agent.

No generic SQL, repository access, shell, arbitrary HTTP, unrestricted CRUD, or bypass tools. MCP is an adapter over the same gateway and contracts—not an independent implementation.

## 8. Intelligence & Decisioning

- LLMs generate, reason, plan, draft, summarize, and synthesize.
- A provider-neutral `DecisionProvider` (Jev/System One is one adapter) returns structured judgments such as routing, risk, relevance, ambiguity, confidence, quality, and candidate selection.
- Deterministic application code enforces authorization, policy, validation, approvals, invariants, and execution.
- Confidence is a signal, not permission. Abstention is first-class.
- Model/provider routing is policy-based and classification-aware.
- Typed, versioned decision contracts and governed prompts.
- Evaluation is separate from generation; evaluate grounding, schema validity, safety, calibration, quality, and regression.
- Provider fallback is risk-sensitive and explicit.
- Cost/latency/token budgets, traces, and model capability restrictions are observable.

Required test categories include routing, schemas, policy enforcement, classification, confidence/abstention, grounding, injection, provider failure/fallback, evaluator disagreement, compatibility, latency/cost budgets, and regression.

## 9. Context & Knowledge Platform

### 9.1 Principle
Context is not a memory dump. Build an authorized, purpose-specific, classification-aware, provenance-preserving context package for each task.

### 9.2 Core contracts
`ContextRequest`, `ContextPackage`, `ContextItem`, `ContextSource`, `ContextReference`, `ContextPolicy`, `ContextBudget`, `ContextAssembly`, `ContextSnapshot`, `ContextClassification`, `ContextProvenance`, `MemoryRecord`, `MemoryCandidate`, `KnowledgeDocument`, `RetrievalResult`, `ContextRelevanceScore`, `TrustAssessment`.

### 9.3 Authorization-first retrieval
Filter authorization, tenant, resource sharing, membership, classification, and revocation **before model delivery**. Do not retrieve broadly and rely on the LLM to filter. Retrieval permission is not action permission; protected mutations reauthorize.

### 9.4 Classification and trust
Platform classification labels: PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED, REGULATED (these are platform labels, not formal government classification markings). Classification metadata includes tenant, owner, policy, retention, sharing, source, and timestamps. Agents cannot downgrade classification.

Trust is separate from authorization. Source trust tiers: platform trusted, enterprise authoritative, user-owned, derived/retrieved, untrusted external. Content is data, not authority; prompt injection defense is layered and not prompt-only.

### 9.5 Memory lifecycle
Layers: working, episodic, semantic, organizational. Memory lifecycle: candidate → validating → active → stale/corrected/expired/deleted/rejected. Model output does not automatically become memory. Explicit request or governed workflow, provenance, confidence, sensitive-data checks, contradiction handling, user correction precedence, scope, and retention are required. Memory is not shared automatically.

### 9.6 Retrieval and ingestion
Hybrid retrieval: lexical, semantic, metadata, and structured retrieval; candidate fusion, deduplication, authorization/classification filters, reranking, freshness, and trust. Graph DB is not a v1 requirement.

Ingestion: source registration/validation → authorization → extraction/normalization → classification/sensitive checks → chunking → metadata/provenance → indexing/embedding → quality/status. Preserve original source access rules.

### 9.7 Provenance and snapshots
Generated outputs distinguish source facts, summaries, and inference and include source references when supported. Context snapshots store a manifest (policy/assembly version, source/version IDs, classification, retrieval parameters, relevance, inclusion/exclusion, budgets, timestamp, integrity hash) without retaining unnecessary sensitive payload.

### 9.8 Budgets, cache, deletion, failure
Required context is preserved; optional context is ranked; duplicates collapse; truncation is reported; critical security/identity data is not truncated. Cache scope includes tenant, actor, authority, purpose, resource, policy/source versions, classification, and membership; invalidate on access/policy/delegation/agent/context changes. Cache never replaces authorization.

Deletion/correction propagates to indexes, chunks, embeddings, summaries, memory, and cache according to retention and audit requirements. Authz/classification failures fail closed. Required source failure blocks or escalates; optional source failure degrades with warning; provenance gaps mark unverified; unauthorized candidate metadata is not disclosed.

## 10. Workflow & Human Collaboration

Reusable workflow definitions/instances, steps, approval policies, approver resolution, human tasks, notifications, decision handlers, and resume controllers.

### 10.1 Approval lifecycle
CREATED → VALIDATING → PENDING → APPROVED / DENIED / CHANGES_REQUESTED / EXPIRED / CANCELLED; revision/revalidation states include REVISED and execution states EXECUTION_PENDING, EXECUTED, EXECUTION_FAILED.

**Approval is not execution.** At execution, revalidate actor authority, policy, resource state, preconditions, scope, and revocation. Approval binds exact action/capability version, tenant, requester/agent/delegator, resource, scope, validated parameters/hash, plan revision, expected state/version, risk/policy, approver, time/expiry, and execution status. Any materially changed action, parameters, target, plan, or state invalidates/requires revalidation. Replay is prevented.

### 10.2 Risk and approvers
Risk policy is deterministic and factors action, tenant/org, classification, actor/agent, delegation, impact, sensitivity, state, and policy version. Low-risk reads generally do not require approval; reversible low-risk mutations may be allowed by policy; sensitive/external/destructive/governance actions require stronger review, quorum, or SoD.

Approvers are resolved from trusted ownership, manager/team/unit/org, policy groups, security, or delegated approvers. Revalidate approver status, tenant, authority, SoD, and quorum at decision time. Agent self-approval is prohibited. No response never means auto-approval.

### 10.3 Human tasks and collaboration
HumanTask types: review, approval, correction, clarification, decision, investigation, assignment, escalation. Durable states include CREATED, ASSIGNED, IN_PROGRESS, COMPLETED, BLOCKED, REASSIGNED, EXPIRED, CANCELLED.

Patterns: approval, clarification, handoff, review. Durable deadlines, reminders, escalation, reassignment, expiry, and resume.

### 10.4 Notifications
Notifications are asynchronous through an outbox and channels (in-app, email, push, enterprise messaging, webhook). Events include task assignment/due/overdue, approval requested/decision/change, escalation, expiry, resume/completion. Delivery retries/dedup/status are independent; notification failure does not change workflow state. Payloads are minimized.

## 11. Audit, Integrity & Governance

### 11.1 Audit
Audit is append-only and correlated across tenant, actor, agent, run, task, step, tool, approval, resource, policy, and outcome. Tiered audit levels are risk-based; avoid raw prompt, private note content, secrets, and sensitive payloads by default.

Protected mutation and audit intent commit atomically (transaction/outbox). External publishing is asynchronous.

### 11.2 Integrity
Target audit integrity mechanisms: canonical serialization, event hash chains, Merkle proofs, signed checkpoints, independent anchors, verification tools, and tamper detection. Key management uses KMS/HSM-capable abstractions; algorithms, canonicalization, anchor cadence, and storage remain implementation decisions.

### 11.3 Governance
Policies, role versions, capabilities, agent definitions, prompts, model restrictions, workflow definitions, and classification rules are versioned and immutable once published. Governance flow: draft → validate → simulate → review → approve → activate → monitor → revise/rollback. Exceptions are scoped, time-bound, approved, and audited.

### 11.4 Audit access and export
Access to audit metadata, investigations, and exports is governed separately from resource content. Exports require authorization, purpose, risk controls, minimized payload, retention, and audit.

## 12. Enterprise UX

Two primary experience surfaces share identity, organization context, authorization, design system, navigation, notifications, audit, and use cases:

### Enterprise Workspace
- Notes and knowledge
- AI workspace
- Agent tasks/runs
- Collaboration and team context
- Human tasks, approvals, notifications
- Generated artifacts and result review

### Enterprise Control Center
Capability-based navigation and server-enforced access:
1. Organization Center — org overview, hierarchy, units, teams, members, reporting, invitations, provisioning, status, history, reorg.
2. Access Center — identity/IdP, memberships, roles, scopes, permissions, effective access (“what can this identity do and why?”), delegation, agent identities, reviews, revocation, history.
3. Agent Center — agent catalog/versions/config/grants/budgets/models/evaluation, lifecycle/revoke; run explorer for request, plan/revisions, tasks, steps, tools, approvals, errors, budget, artifacts, evaluation, result.
4. Capability Center — registered tools/capabilities, schema, risk, side effects, policy, retries/idempotency, timeouts, usage, grant/revoke/scope/version/deprecation.
5. Governance Center — policy/role/capability/agent/workflow/classification versions, risk, approvals, SoD, migrations, exceptions, controls, history.
6. Audit Center — investigation timeline, correlation, integrity verification, export controls.
7. Platform Operations Center — runtime/workers/queues/models/tools/context/workflows/approvals/notifications, health, budgets, suspend/stop/revoke/disable, safe retry/recovery/reassign/escalate.

Personas: employee, team member/lead, manager, org admin, security admin, agent operator, auditor, platform admin. UX principles: capability-based visibility; progressive disclosure; transparency without private chain-of-thought; risk-aware interactions; accessible consistency; no silent admin mutations. UI gating is not authorization.

## 13. Persistence, Consistency & Resilience

- Repositories and Unit of Work remain the persistence ports.
- Optimistic concurrency on mutable aggregates and state transitions.
- Durable run/workflow state and event history.
- Idempotency keys and deduplication for side effects.
- Transactional outbox for audit intent, notifications, and integration events.
- Leases/claims for worker concurrency and recovery.
- Revocation and sensitive authorization are synchronous/authoritative.
- Low-risk reads may use bounded version-aware snapshots/cache.
- Failure modes and degradation are explicit; security dependencies fail closed.
- DR/backup/restore and incident containment are planned and tested.
- Avoid promising distributed guarantees before storage/infra design validates them.

## 14. Security & Threat Coverage

Threat scenarios to test: cross-tenant access; identity spoofing; session/token replay; auth downgrade; privilege escalation; delegation abuse; stale/revoked authority; tool misuse; prompt injection; context leakage; resource enumeration; approval spoofing/self-approval/SoD bypass; approval parameter or plan substitution; duplicate side effects; concurrency races; notification manipulation; policy version drift; cache invalidation failure; audit tampering; export abuse; flooding/budget exhaustion; secrets and sensitive content leakage; provider compromise/failure; deprovisioning delay.

Required invariants:
- No agent or service acts beyond its effective authority.
- No tool bypasses application use cases or the gateway.
- Every protected action rechecks authorization and obligations.
- Approval cannot override authorization and must bind to exact execution.
- Revocation wins over stale state.
- Tenant boundaries are enforced in retrieval and execution.
- No prompt/content is treated as trusted instruction by default.
- Audit is not silently omitted for protected mutation.
- Security failure is never converted to permissive behavior.

## 15. Observability

Correlate the lifecycle: tenant → actor → agent → run → plan revision → task → step → tool → policy → approval → human decision → execution → outcome.

Metrics include run/task success and failure, queue/lease, latency, tool usage, approval turnaround/pending/overdue, human task completion/reassignment, notification delivery, policy deny/obligation, audit integrity, context source/exclusion/budget/truncation, model/provider cost/latency, evaluation/grounding/abstention, and revocation propagation. Telemetry must not leak note contents or sensitive prompt data.

## 16. Implementation Roadmap

Implementation is staged by dependency and risk. OpenHands should implement vertical slices with tests, migration, UI/API, documentation, and evidence—not broad scaffolding with unconnected pages.

### Phase 0 — Architecture consolidation (this PR)
- Consolidate accepted design pillars.
- Reconcile existing architecture/expansion/security docs.
- Formalize organization and identity decisions.
- Identify gaps and conflicts.
- Establish sequence and acceptance criteria.
- Update PR description and implementation handoff.

### Phase 1 — Foundation & organization/identity
- Shared actor/tenant context and identity lifecycle foundations.
- Org, unit, team, membership, reporting model and validations.
- Production OIDC/session/provisioning integration seams.
- Admin workspace shell and Organization Center.
- Migration, isolation, lifecycle and architecture tests.

### Phase 2 — Unified authorization & governance
- Capability/policy contracts and registry.
- RBAC/scoped RBAC/ABAC/ACL/delegation/obligations.
- Effective-access view and scoped admin capabilities.
- Policy/role versioning, approvals, revocation, migration.
- Security regression and cross-tenant tests.

### Phase 3 — Agent runtime & tool gateway
- Agent definition/version/catalog and run/task/plan/step contracts.
- Durable lifecycle, checkpoints, leases, idempotency, budgets, cancellation.
- Tool registry and gateway with existing application use cases.
- Run explorer and operational controls.
- Agent/delegation/abuse tests.

### Phase 4 — AI decisioning & context
- Provider-neutral generation/decision/evaluation ports.
- Jev/System One adapter.
- Context builder, authorized retrieval, classification, trust, provenance, snapshots.
- Hybrid knowledge retrieval and ingestion.
- Memory lifecycle and context budgets.
- Injection, grounding, privacy, provider fallback tests.

### Phase 5 — Human workflow & collaboration
- Workflow definitions/instances and durable state.
- Human tasks, approval gates, approver resolver, SoD, revalidation.
- Notification outbox, deadlines, escalation, resume.
- End-to-end agent pause/approve/execute/reject flow.

### Phase 6 — Enterprise Control Center
- Access, agent, capability, governance, audit, platform operations centers.
- Effective-access and run investigations.
- Risk-aware admin interactions, audit exports, observability.

### Phase 7 — Integrity, production hardening & showcase
- Append-only audit ledger and integrity proofs/checkpoints.
- KMS/HSM/key lifecycle decisions.
- Resilience, incident, recovery, deployment, performance, security review.
- Showcase demo covering organization, secure context, agent plan, tool policy, human approval, execution, audit trail.

### Delivery rule
Each phase is split into small reviewable vertical slices. Each slice must include:
- Domain/application contracts and invariants.
- API/UI adapters as applicable.
- Migration strategy and compatibility.
- Unit, integration, architecture, security, and regression tests.
- Observability and failure behavior.
- Documentation updates.
- CI evidence and reviewable PR.

## 17. Architecture Gaps & Explicit Decisions

The following are intentionally open implementation details; do not make hidden assumptions:
- Storage schema and hierarchy indexing choice; adjacency is baseline, closure/index optimization evidence-driven.
- OIDC/BFF/session cookie topology, IdP, step-up, CAC/PKI/federal IdP requirements.
- SCIM/group sync and provisioning policy.
- Policy AST grammar, evaluation engine, condition language, canonicalization, version migration.
- Capability naming, schema versioning, registry storage and publication workflow.
- Delegation token format, TTL, revocation strategy, actor propagation.
- Agent persistence/event store, scheduler/worker technology, queue, lease model.
- LLM/decision-provider routing, approved models, deployment, data classification restrictions.
- Context source connectors, retrieval/reranking/index choices, vector/lexical store.
- Approval risk matrix, quorum, SoD policy, approver resolution rules.
- Audit canonicalization, hashing/signing, KMS/HSM, anchor cadence, retention, independent anchor.
- Data classification mapping, retention, deletion, crypto-erasure, legal hold.
- RPO/RTO, backup, DR, incident runbooks.
- Compliance mapping and required evidence.
- Tenant isolation model beyond logical enforcement (e.g. shared DB vs stronger separation).

## 18. Consolidation/Gaps Checklist

- [x] Preserve modular monolith and Hexagonal architecture.
- [x] Consolidate core domain, security, agent, tool, intelligence, context, workflow, UX, and audit pillars.
- [x] Formalize organization/identity baseline and lifecycle.
- [x] Clarify approval is not execution and revalidation is required.
- [x] Clarify retrieval authz happens before model delivery.
- [x] Establish phased roadmap and implementation slice acceptance criteria.
- [ ] Reconcile detailed docs/ADRs and update their cross-links.
- [ ] Convert roadmap phases into issue/task breakdown and implementation handoff.
- [ ] OpenHands implementation has not started as a result of this architecture consolidation; code implementation remains separately tracked.

## 19. Related Documents

- `ENTERPRISE_AGENTIC_EXPANSION_SPEC.md` — original expansion scope and implementation contract.
- `ENTERPRISE_SECURITY_ARCHITECTURE_V1.md` — security architecture synthesis.
- `ARCHITECTURE.md` — existing code architecture and package layout.
- `DOMAIN_MODEL.md` — existing domain model.
- `SECURITY.md` — implemented controls and security limitations.
- Authorization architecture/policy/domain model docs and ADRs — detailed authorization contracts.

## 20. OpenHands Implementation Handoff

OpenHands is expected to implement the roadmap as sequenced, independently reviewable vertical slices. Do not attempt all phases in one PR. Start with a repository reconciliation and Phase 1 plan, report exact existing-code impact, and propose the first slice before coding.

**Implementation guardrails**
- Read this master and related documents before coding.
- Inspect the current repository and tests; do not assume target architecture already exists.
- Identify conflicts with existing code and ADRs and report them.
- No unrelated refactors.
- Do not build premature microservices.
- Do not create role/admin bypasses.
- Preserve current Notes semantics and backward compatibility through explicit migration.
- Every new endpoint/use case is authorization-protected.
- Agents/tools must use application use cases.
- Approval cannot auto-execute and must bind to an exact action/plan revision.
- Tests must cover tenant isolation, revocation, authz, failure/degradation, and concurrency.
- Run relevant test suites, architecture tests, lint/type checks, and CI; include evidence.
- Work in small vertical slices and open PRs with clear scope, migration, tests, and risks.
- Keep documentation and architecture status synchronized.
- Do not claim completion without passing evidence.

**First implementation deliverable:** Repository gap report + prioritized Phase 1 backlog (small slices, dependencies, acceptance criteria, test plan, risks). After review, implement the first vertical slice.

---
End of master architecture.
