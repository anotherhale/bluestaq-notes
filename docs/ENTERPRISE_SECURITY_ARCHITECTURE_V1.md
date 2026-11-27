# Enterprise Security Architecture v1

**Status:** Proposed target architecture / accepted architecture decisions  
**Branch:** `feature/agentic-enterprise-platform`  
**Scope:** Bluestaq Notes enterprise and agentic platform expansion  
**Decision basis:** Architecture decisions accepted in design discussions (Decisions 1–53).  
**Implementation:** Delegated to OpenHands in sequenced work packages; this document is the architectural contract, not evidence of implementation.

## 1. Executive Summary

Bluestaq Notes will evolve from a notes collaboration application into an enterprise multi-tenant security and governance platform that authorizes humans, AI agents, and services; safely manages privilege changes and delegated authority; and preserves durable, independently verifiable security evidence.

The platform remains a **modular monolith** initially. Logical boundaries are enforced through domain modules, application ports, and infrastructure adapters. Services are not split prematurely.

Core security principles:

1. Default deny.
2. Tenant isolation is a mandatory invariant.
3. Authentication and authorization are distinct.
4. Identity and actor are distinct.
5. Authorization semantics are unified and enforced in application use cases.
6. Delegated authority is attenuated and cannot exceed the delegator's authority.
7. Published security policy and role versions are immutable.
8. Revocation takes precedence over stale grants, caches, and snapshots.
9. Security mutation and audit intent commit atomically.
10. Security history is append-only and independently verifiable.
11. Administrative authority does not automatically grant access to private content.
12. Availability and degradation never silently weaken security.

## 2. Logical Architecture

The following are logical boundaries, not mandatory separate deployable services:

- **Identity & Authentication:** OIDC, account lifecycle, sessions, authentication assurance, credential revocation.
- **Trusted Actor Context:** actor, tenant, scope, delegation, task, auth context, risk.
- **Authorization:** PDP, PEP, PIP, PAP, capability registry, roles, assignments, resource grants, policies, delegation, obligations.
- **Application Enforcement:** use cases, resource authorization, obligations, transaction-aware mutation and concurrency controls.
- **Security Governance:** role versions, policy release, approvals, role migrations, break-glass.
- **Audit & Integrity:** outbox, append-only ledger, hash chain, Merkle trees, signing, checkpoints, anchoring, verification, retention, export.
- **Infrastructure & Trust:** database, KMS/HSM, immutable storage, replication, recovery and independent verification.

Inbound UI, REST, MCP/tool, OIDC and administrative adapters must call application use cases. Agent/tool adapters never access repositories or databases directly.

## 3. Authentication and Identity

### Humans
- OIDC Authorization Code flow with PKCE S256.
- Validate issuer, audience, signature/JWKS and rotation, expiry, `nbf`, `state`, and `nonce` as applicable.
- Secure session lifecycle, logout, claim mapping, account linking and provisioning.
- External identity key is `(issuer, subject)`, not email.
- The application owns internal user identity, tenant membership, and authorization.

### Agents and services
Agent and service identities are distinct from human identities and use separate credential lifecycle and scope management. Use short-lived, revocable, task-bound or narrowly scoped credentials where applicable. Never share human credentials.

### Session security
- Refresh rotation and reuse detection.
- Revocation and account-disable propagation.
- Idle and absolute session limits.
- Step-up authentication and reauthentication for sensitive administration, privilege escalation, break-glass, investigations, exports, and critical cross-tenant actions.
- A valid session is not an authorization bypass.

## 4. Actor Context

Authentication establishes identity; actor context describes the entity performing an action.

Actor types: Human, Agent, Service.

Trusted context is derived server-side and may include:
- actor ID and type
- tenant ID
- identity reference and status
- authentication context / assurance
- session or credential identity
- delegated-by identity and delegation ID
- agent/service task ID
- scope
- risk context
- correlation ID

Client-provided tenant, actor, delegator, or scope claims cannot widen trusted authority. Preserve the acting agent/service and the delegating human as separate identities.

## 5. Unified Authorization Model

Authorization combines complementary mechanisms in one decision:
- RBAC
- scoped RBAC
- ABAC
- resource ACLs/grants
- capability-based authorization
- scoped administrative privileges
- delegation
- obligations

### Semantics
- Default deny.
- Mandatory platform security constraints cannot be overridden.
- Applicable explicit deny overrides ordinary grants.
- Grants may originate from roles, scoped roles, ACLs/resource grants, policy, or delegation.
- ABAC conditions constrain grants; missing required attributes never satisfy a condition.
- Unknown actions, capabilities, conditions, obligations, or evaluation failures fail closed.
- Obligations (step-up, approval, second approver, redaction, rate/volume limits, enhanced audit) must be satisfied before the operation proceeds.
- No cross-tenant grants.
- Tenant or platform admin status does not automatically grant private-resource access.
- Manager/report and team membership alone do not grant private-resource access.
- Scope inheritance is capability-specific and explicit.
- Role assigners cannot grant authority beyond their own authority and scope.
- Human, agent, and service actors use common authorization semantics.

### Scope
Supported conceptual scopes include Platform, Tenant, Organizational Unit, Department, Team, and Resource. Inheritance is explicit and capability-specific; parent scope does not automatically confer all child capabilities.

### Decision evidence
Structured `AuthorizationDecision` includes decision ID, effect, actor/delegator, tenant, action, resource, scope, reason code, matched policy versions, grant sources, obligations, attribute provenance/version, risk, task/correlation IDs, timestamp, and evaluation failure details as appropriate. Reason codes and policy detail are not leaked to unauthorized audiences.

## 6. Authorization Component Boundaries

Logical PDP, PEP, PIP, and PAP remain modules/ports in the modular monolith:
- **PDP:** evaluates policy and produces structured decisions.
- **PEP:** application/use-case enforcement point.
- **PIP:** trusted attribute acquisition and provenance.
- **PAP:** policy authoring, validation, versioning, approval and publication.

Enforcement occurs in application use cases, with route checks as defense in depth. Protected mutations load authoritative resource/security state, evaluate, satisfy obligations, and use transaction/concurrency controls to prevent TOCTOU.

### Authorization caching and resilience
- Risk-tiered, version-aware caching.
- Low-risk stable reads may use short bounded caches.
- Membership, ACL, policy, role, actor, and delegation versions are tracked.
- Sensitive actions revalidate authoritative state.
- Revocation invalidates promptly; sensitive authorization cannot rely solely on asynchronous cache invalidation.
- Missing authority dependencies or inconsistent versions fail closed for protected actions.

## 7. Capability Registry

Capabilities are centrally governed, versioned, and immutable after publication. Definitions include ID, description, resource type, scope model, sensitivity, delegability, inheritance semantics, obligations, version and lifecycle status.

Capabilities must be registered and validated before use. Unknown capabilities deny. Deprecated definitions cannot be used for new grants; revocation overrides assignments and pinned role versions. Namespace and compatibility rules are validated in CI.

## 8. Policy Model and Lifecycle

Policies use a typed, declarative AST—never arbitrary Python, JavaScript, SQL, or user-executable code. Supported operators are constrained (boolean, equality/inequality, set membership, numeric comparison, approved existence/relationship/time/classification predicates). Type combinations, complexity, and attribute provenance are validated. Missing attributes do not grant; implicit coercion is prohibited.

Policy lifecycle:
Draft → Validate/Test → Impact Preview → Approve → Publish → Monitor.

Published policy releases are immutable. Rollback is a new release that restores prior configuration, not mutation/deletion of published history. Broad-impact or lockout-prone changes require risk-based approval and preview. Mandatory platform constraints cannot be weakened by tenant configuration.

Policy conflict semantics must be explicitly encoded and tested: mandatory security constraints first; applicable explicit deny overrides ordinary grants; grants and conditions evaluated within their scope; obligations applied to allowed decisions; errors and unknown semantics deny.

## 9. Role Versioning and Governance

A Role has stable identity and immutable published RoleVersion snapshots. Assignments pin a version. Publishing a new version does not automatically migrate assignments; migration is explicit. Revocation overrides pinned versions. Role lifecycle uses draft, validate/preview, approval and publish.

Role assignment scope and grant authority are constrained by the assigner's own scope and authority.

## 10. Role Migration

Migration is a governed, resumable workflow:
1. Select target role version and cohort criteria.
2. Preview effective privilege changes and impact (new access, removed access, redundant grants, scope expansions, constraint changes).
3. Freeze the cohort snapshot at approval; snapshot is immutable.
4. Approve exact plan revision and snapshot.
5. Execute assignment-level transactional updates with expected source version and assignment revision.
6. Record outcomes and durable checkpoints.
7. Pause, inspect, amend, re-review, re-approve, and resume when conflicts or policy/approval invalidation occurs.

Cohort targeting may include org units, teams, explicit assignments, or combinations. Avoid arbitrary percentage rollout and arbitrary user rules in the initial model.

### Execution semantics
Snapshot defines intent; current state defines eligibility. At execution revalidate assignment active, source version/revision, target version valid/not revoked, executor authority, approvals, mandatory policies, and tenant/scope. Conditional update must match expected source and revision. Zero affected rows is a conflict; never blindly retry.

### Batches and failures
- Durable checkpoints and idempotent item execution.
- Assignment-level atomicity where possible; no pretend global atomicity.
- Outcomes: SUCCEEDED, SKIPPED, FAILED, CONFLICT, RETRYABLE, REQUIRES_REVIEW.
- Preserve completed work; no automatic rollback of successful assignments.
- Retry transient failures with bounded backoff.
- Security failures pause immediately.
- Operational pause thresholds, batch sizes, and retry limits are configuration/implementation decisions.
- Conflict resolution is a localized amendment. Original snapshot, approvals, expected source, attempts, and conflict history remain immutable.

## 11. Risk and Approval Governance

Platform risk classification establishes minimum requirements; tenant/org policy may tighten but never weaken platform requirements. Factors include effective capability delta, sensitive capability, scope expansion, admin/security privileges, affected population, agent/service delegation, policy/constraint change, restriction removal, resource sensitivity, and reversibility.

Illustrative risk levels:
- **Low:** equivalent/no effective privilege change; no approval and self-approval permitted.
- **Moderate:** limited capability within scope; at least one policy-defined approver.
- **High:** meaningful/sensitive increase; independent approver; initiator cannot approve.
- **Critical:** tenant-wide/admin/security/highly sensitive; two independent approvers; initiator excluded; separate executor.

Separation-of-duties requirements are hard platform minimums for high/critical. Approvals bind to exact plan revision and frozen cohort; material changes invalidate them. Approver authority is rechecked at execution. Proposed maximum approval validity: Moderate 7 days, High 72 hours, Critical 24 hours; tenant policy may shorten. Expired, revoked, and invalidated are distinct statuses and all block execution.

Mid-rollout approval invalidation pauses future work, preserves completed outcomes/checkpoints/history, recalculates remaining impact, and requires fresh authorization/approval before resume.

## 12. Agent and Service Delegation

Delegation is revocable and attenuated by delegator, agent/service, tenant, capability, resource/scope, purpose/task, validity, constraints, and permitted depth. Effective authority is the intersection of agent authority, delegator authority, tenant, resource, and policy. Delegation cannot elevate privilege.

Revalidate at protected action boundaries. Revoke when delegation/task expires, delegator loses authority, agent/service is suspended, tenant membership/status changes, capability is revoked, or credential compromise occurs. Preserve actor vs delegator attribution. Offline protected agent actions deny by default.

## 13. Audit Architecture and Event Requirements

Use a dedicated append-only, tamper-evident security event ledger, logically separate from operational authorization state, initially within the modular monolith.

### Event envelope
Event ID/type, schema version, timestamp, tenant/partition, actor and delegator, action, resource reference/classification, outcome/reason, policy/version references, correlation/task/migration IDs, metadata, previous hash, event hash, and other validated fields. Never include secrets, raw tokens, private note content, raw prompts, or model output unless a separately governed evidence process explicitly permits it.

### Risk tiers
- **Tier 1 mandatory durable:** all authorization denials; privilege grant/revoke; admin/security actions; state-changing agent/service actions; sensitive classified access; approval lifecycle; role migrations/outcomes/conflicts; policy/capability registry changes; tenant isolation failures; break-glass.
- **Tier 2 risk-based durable:** bulk operations, elevated access, unusual patterns, exports and suspicious activity.
- **Tier 3 telemetry/sampling:** routine low-risk reads. Sampling never applies to Tier 1; risk-triggered events can be promoted.

### Transactional outbox
For required security mutations, operational mutation and durable audit intent commit in the same database transaction. If outbox intent cannot be persisted, the mutation does not commit. External ledger delivery is asynchronous once durable intent exists.

Outbox is durable, idempotent, at-least-once, stable event IDs, bounded retries/backoff, DLQ/quarantine, partition ordering. Outbox backlog is monitored and threshold breaches can pause high-risk mutations. Outbox prevents loss of intent; it is not independently tamper-proof.

## 14. Ledger Integrity and Checkpointing

Integrity layers:
1. Canonical event representation and event hash.
2. Partitioned hash chain.
3. Merkle trees for inclusion proofs.
4. Signed checkpoints.
5. Independent immutable anchors.

Partition ordering is per partition, not globally. Partition identity, canonicalization/schema version, hash algorithms, sequence boundaries, timestamps, signing key identifiers, previous checkpoint, and signature are part of checkpoint evidence.

### Verification
- Continuous lightweight schema/hash/sequence/idempotency validation.
- Scheduled deep segment verification.
- On-demand verification before archive restore, investigations, evidence export, migration, and suspected tampering.
- Distinguish inability to verify from confirmed tampering.
- Integrity anomalies create incident signals, preserve evidence, and restrict sensitive operations.

### Checkpointing
Adaptive volume/risk/time checkpoints. Proposed normal maximum checkpoint age: 15 minutes. Critical events (break-glass, tenant isolation violations, critical privilege escalation, platform security changes, signing/checkpoint concerns) trigger immediate checkpoint work. Asynchronous anchoring is not reported as complete until the anchor provider confirms.

### Signing
Production signing keys are KMS/HSM-backed and non-exportable. Application uses a signer abstraction; it never holds production private keys. Key versions are retained for historical verification. Rotation is scheduled, manual, or emergency. Retired keys remain trusted for historical verification unless revoked; compromise status may make signatures for a specified time/scope suspect. Rotation alone does not restore trust.

### Independent anchors
Initial strategy: separate security account/admin boundary, immutable/WORM storage, independent controls and credentials. Application cannot rewrite/delete anchored checkpoints. Anchor providers are abstracted for future independent services, object stores, or customer-controlled infrastructure. No blockchain requirement.

## 15. Ledger Partitioning and Cross-Tenant Correlation

Use a platform-security ledger and tenant-local logical ledgers, with physical partitions based on tenant/volume/category/time as scale warrants. Tenant events remain isolated. Cross-tenant workflows use shared correlation/causation IDs and platform workflow trail; each tenant independently records local effects. The platform trail contains only necessary orchestration metadata and must not expose tenant content.

Correlation IDs are not authorization credentials. Cross-tenant workflow investigation requires explicit platform authority. Missing tenant effects must be detectable.

## 16. Audit Visibility, Audit-of-Audit, Retention, Privacy

### Visibility
- Tenant admins/auditors: routine events within their authorized scope.
- Sensitive events: restricted security officers/investigators.
- Platform security: authorized cross-tenant security context.
- Platform operators: operational metadata only by default, no standing tenant content access.
- Agents/services: no audit access by default.

Restricted audit access requires explicit scope, purpose/case, time bounds, step-up as required, and durable access audit. Redaction is performed server-side. Query/view/export/generate/download are distinct actions.

### Audit-of-audit
Routine query metadata may use telemetry; sensitive event views, investigations, denials, bulk queries, exports, and suspicious query patterns require durable audit. Avoid recursive full-audit loops.

### Retention
Hybrid platform minimums and tenant extensions by event category, with legal holds and controlled disposition. Illustrative defaults (not legal mandates): authentication/security and denials 1 year; privilege changes 2 years; approvals/migrations 3 years; investigations policy-defined; routine telemetry 30–90 days; legal hold until released. Immutable while retained; corrections appended; deletion audited. Segment-based cryptographic erasure, backup/replica/export lifecycle, and disposition evidence require explicit design.

## 17. Audit Export Controls

Export is a distinct capability. Controls include authorization, scope/query limits, server-side redaction, volume limits, justification, risk-based approval, encryption, short-lived downloads, artifact expiry, and durable lifecycle audit. Export artifacts are snapshots. Viewing, query, generation, download, and disposition are separate audited actions. Sensitive/high-risk export requires step-up and possibly independent approval.

## 18. Resilience, Degradation, and Offline Authorization

Authorization infrastructure should use stateless PDP replicas, redundant trusted attribute providers, HA policy/role repositories, signed/versioned snapshots, bounded timeouts, circuit breakers, and correctness-aware health.

Failure behavior:
- Public content: available according to application rules.
- Low-risk cached reads: only explicitly eligible under bounded, signed, valid snapshots.
- Private/sensitive reads: valid current authorization required; high sensitivity revalidates authoritative state.
- Protected mutations, admin/security operations, privilege changes, and protected delegated actions: fail closed.
- Required attributes unavailable: deny.
- Inconsistent policy/version state: deny sensitive actions.
- Policy repository unavailable: use only an eligible, valid snapshot for explicitly permitted low-risk operation.
- Cache failure: re-evaluate or deny.
- All PDPs unavailable: protected actions deny.
- Circuit breakers do not grant permission.
- Audit query may use a restricted degraded mode only when safe; exports fail closed if required controls are unavailable.

Offline authorization is restricted to explicitly eligible low-risk operations using signed, versioned, tenant-scoped, time-bounded snapshots. Private content, sensitive operations, mutations, admin and protected agent/service actions are denied offline by default.

## 19. Mutation Consistency and Revocation

Sensitive mutations follow a transaction-aware pattern:
1. Load authoritative resource/security state and trusted context.
2. Evaluate authorization.
3. Satisfy obligations.
4. Revalidate current authority and resource state where critical.
5. Apply conditional mutation/optimistic concurrency.
6. Commit mutation and required audit intent atomically.
7. Publish external audit work asynchronously.

Protect against TOCTOU races. Where external systems prevent atomic enforcement, document the consistency model and residual risk.

Revocation is committed with durable audit intent, version advancement, and event-driven cache invalidation. Pub/sub is an optimization; authoritative checks protect sensitive boundaries. Revocation is prioritized over stale cache and offline snapshots.

## 20. Security Observability and SLOs

Monitor auth latency/errors, denial patterns, policy/cache versions, revocation propagation, outbox backlog/age, ledger lag, checkpoint age/signing, integrity verification, exports, delegation, approvals, break-glass, and cross-tenant anomalies.

Initial targets for validation, not committed SLAs:
- Authorization latency p95 ≤ 50 ms.
- Authorization availability 99.95%.
- Revocation propagation p99 ≤ 5 seconds.
- Outbox lag p99 ≤ 30 seconds.
- Normal checkpoint age ≤ 15 minutes.
- Zero committed security event loss.

Targets must be validated with load tests, instrumentation, and deployment topology.

## 21. Incident Containment and Recovery

Contain at the narrowest authority boundary: credential → agent/service identity → delegation/task → capability → resource/scope → tenant → platform.

Incident flow: detect → contain → preserve → investigate → recover. Preserve evidence and chain of custody, assess impact, revoke authority, verify trust boundaries, restore only independently verified state, reissue credentials as needed, reevaluate roles/delegations/policies, and durably audit recovery. Reinstatement is not automatic.

## 22. Disaster Recovery

Proposed targets are architectural planning values, not final SLAs:
| Component | Proposed RPO | Proposed RTO |
|---|---:|---:|
| Operational authorization state | ≤ 1 minute | ≤ 1 hour |
| Durable outbox | 0 committed loss | ≤ 1 hour |
| Security ledger | ≤ 1 minute | ≤ 1 hour |
| Anchored checkpoints | No loss of successfully anchored checkpoints | ≤ 4 hours |
| Audit query service | ≤ 15 minutes | ≤ 4 hours |

Recovery verifies chains/checkpoints, reconciles operational state, ensures outbox records are not silently lost, detects missing sequence ranges, preserves tenant isolation, and validates trust before enabling high-risk operations. Recovery events are audited.

## 23. Security Testing Requirements

Required testing layers: domain unit, property-based, integration, adversarial, concurrency, chaos, integrity, and recovery tests.

Mandatory security properties:
- No cross-tenant access.
- Default deny.
- Explicit deny precedence.
- Revocation and stale-cache safety.
- Delegation attenuation.
- No human identity forgery by agents.
- Missing required attribute does not grant.
- Unknown capability/condition/obligation denies.
- Evaluation failures deny.
- Approval invalidation blocks execution.
- Migration concurrency conflict is detected.
- Required audit intent is never lost on committed mutation.
- Duplicate outbox processing is idempotent.
- Integrity tampering is detectable.
- Export isolation/redaction is enforced.
- Admin has no private-content bypass.
- Recovery validates trust before re-enabling protected operations.

## 24. Legacy Authorization Migration

Preserve current Notes authorization behavior initially:
- `Principal.user_id` remains authoritative human identity.
- Note owner retains full control.
- READ collaborator is read-only.
- WRITE collaborator can read/update.
- Collaborators cannot delete, share, or manage permissions.
- Folder ownership remains distinct from note permission.
- Unreadable resources return 404 where appropriate to conceal existence.
- Existing `AuthorizationProvider` and `DefaultAuthorizationProvider` contracts remain during migration.
- Avoid duplicating `Principal`, `UserId`, or prematurely replacing `NotePermission`.

Migration is incremental: map existing permissions conservatively (no privilege increase), run shadow evaluation, compare decisions, review discrepancies, and progressively enforce by capability/cohort. Unexpected access differences are treated as security regressions. Preserve rollback path.

## 25. Proposed Package Boundaries

Keep a modular monolith. Logical modules may include:
- `identity`
- `authorization/domain` (actor, scope, capability, role, role assignment, resource grant, policy, delegation, context, request, decision, obligations)
- `authorization/application` (ports, use cases)
- `authorization/infrastructure`
- `security_governance` (role versioning, migrations, approvals, policy release, break-glass)
- `audit/domain` (events, integrity, checkpoints, retention)
- `audit/application` (outbox, ingestion, verification, export)
- `audit/infrastructure` (ledger storage, signer, anchor, archive)
- `notes` (existing Notes domain/application/infrastructure)

Domains must not import framework or infrastructure types. Application layers depend on domain and define ports. Infrastructure implements ports. Bootstrap/composition root wires adapters. Security semantics cannot be redefined by transport adapters.

## 26. Implementation Roadmap and Gates

1. **Baseline/contracts:** document existing behavior, threat model, ADRs, ports, test invariants.
2. **Authorization foundation:** trusted actor context, tenant scope, capabilities, decisions, default-deny enforcement.
3. **Roles/resources:** RBAC, scoped roles, resource grants, dynamic membership, explicit deny.
4. **Policy/delegation:** typed AST, ABAC, obligations, agent/service delegation, revocation.
5. **Governance:** immutable role versions, risk approvals, SoD, migration snapshots, conflict workflows.
6. **Audit foundation:** event schemas, outbox, append-only ledger, partitioning, Tier 1 durable audit.
7. **Integrity:** hash chain, Merkle roots, signer abstraction, checkpoints, independent anchors.
8. **Audit operations:** visibility, retention, investigations, exports, legal holds.
9. **Resilience/operations:** HA, revocation, degradation, recovery, observability, incident containment.
10. **Migration/rollout:** shadow comparison, discrepancy review, controlled enforcement, rollback.

Each phase gate requires domain review, security invariant tests, integration tests, threat-model review, documentation, migration/compatibility review where applicable, and appropriate operational readiness. Code completion alone is not a gate.

## 27. Threat Model Focus

Threat actors and trust failures to assess:
- Malicious tenant user/admin.
- Compromised human session.
- Malicious or compromised agent/service.
- Delegation misuse or privilege escalation.
- Application compromise.
- Database/storage operator tampering.
- Audit publisher or outbox compromise.
- Signing key/KMS compromise.
- Independent anchor provider failure or compromise.
- Supply-chain or dependency compromise.
- Cross-tenant correlation/query abuse.
- Insider support/break-glass misuse.
- Stale policy, attribute, membership, or authorization cache.
- Recovery from compromised or incomplete backups.

## 28. Deliberately Open Implementation Decisions

Do not prematurely lock:
- Audit storage engine.
- KMS/HSM provider.
- Canonical serialization/hash/signature algorithms and byte representation.
- Checkpoint and proof formats.
- Exact retention/legal requirements.
- Deployment-specific RPO/RTO commitments.
- Physical ledger sharding and isolation.
- Policy AST grammar and complexity limits.
- Migration batch sizes, thresholds, retries and backoff.
- Anchor provider and anchor receipt verification semantics.
- Key compromise trust windows and remediation.
- Crypto-erasure and backup/export disposition mechanics.
- Compliance mappings.

Resolve through ADRs, threat-model evidence, performance tests, product requirements, and deployment constraints. Retention and SLO figures in this document are illustrative architecture proposals, not legal requirements or production commitments.

## 29. Architecture Position

The foundational decision is not a particular authorization mechanism or cryptographic primitive. It is that **authorization, governance, delegation, enforcement, and audit are parts of one security system with shared invariants and separate responsibilities**.

The system must be consistent for humans, agents, and services; secure under failure; auditable; tenant-isolated; and evolvable without silent privilege changes.

This document is the architecture contract for subsequent implementation and review. It does not claim that any implementation, test, deployment, or operational control has been completed.
