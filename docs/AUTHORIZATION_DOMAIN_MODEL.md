# Authorization Domain Model

> Status: Proposed canonical domain model for review.
> This document defines domain concepts and invariants, not SQLAlchemy tables or a claim of implemented functionality.
> Related: [ADR-011](ADR/ADR-011-unified-authorization-model.md), [Authorization Architecture](AUTHORIZATION_ARCHITECTURE.md), [Authorization Policy Model](AUTHORIZATION_POLICY_MODEL.md), [Domain Model](DOMAIN_MODEL.md).

## 1. Modeling goals

The authorization domain must:
- Model human, agent, and service actors without conflating identity with authority.
- Represent reusable roles separately from assignments of those roles.
- Represent registered capabilities separately from grants and policy rules.
- Make tenant and organizational scope explicit.
- Support resource-specific grants without turning every permission into a role.
- Express contextual policy conditions, explicit deny, and obligations.
- Preserve attribution, revocation, expiry, and policy version information.
- Keep domain types independent of FastAPI, SQLAlchemy, OIDC libraries, and any external policy engine.
- Preserve current Notes owner/READ/WRITE behavior as a compatibility contract.

The model should remain a modular-monolith domain capability. It is not a proposal to create an authorization microservice.

## 2. Core vocabulary and boundaries

### Actor versus identity

An **identity** is evidence of an authenticated entity. An **actor** is the entity on whose behalf an operation is being performed. A request's actor context is established by trusted authentication and application-side resolution; it must not be assembled from arbitrary client fields.

Actor categories:
- Human actor: references the application's internal user.
- Agent actor: references an agent/service-principal identity and its lifecycle state.
- Service actor: references a non-human integration or workload identity.

A delegated request has an immediate actor (the agent/service) and a distinct delegator (the human or upstream actor). Do not collapse them into one user ID.

Conceptual value object:
```python
@dataclass(frozen=True)
class ActorRef:
    actor_id: ActorId
    actor_type: ActorType  # HUMAN | AGENT | SERVICE
```

The authenticated identity and authentication evidence belong in an immutable request security context, not in a role or permission record.

### Tenant and scope

Tenant is a mandatory security boundary for tenant-owned resources. A tenant identifier is resolved from trusted authentication/membership context and validated server-side.

A `ScopeRef` identifies the boundary at which authority applies:
- PLATFORM
- TENANT
- ORGANIZATIONAL_UNIT
- DEPARTMENT
- TEAM
- RESOURCE

Scope identity is typed; a bare string ID without a scope type is ambiguous. A tenant-scoped assignment must carry a tenant ID. Platform-scoped assignments are represented explicitly rather than by a magic tenant ID.

Organizational hierarchy and authorization scope are related but not equivalent. Membership or reporting relationships do not themselves grant content access.

## 3. Canonical domain types

The following are proposed domain concepts. Exact Python module/file names may be adjusted to fit the existing package conventions, but their semantic boundaries should remain.

### 3.1 Identifiers and enums

- `ActorId`, `TenantId`, `RoleId`, `RoleAssignmentId`
- `CapabilityId`, `PolicyId`, `PolicyVersion`
- `GrantId`, `DelegationId`, `ApprovalId`
- `ScopeId`, `DecisionId`, `ObligationId`
- `ActorType`: HUMAN, AGENT, SERVICE
- `ScopeType`: PLATFORM, TENANT, ORGANIZATIONAL_UNIT, DEPARTMENT, TEAM, RESOURCE
- `AssignmentStatus`: ACTIVE, REVOKED, EXPIRED (EXPIRED may be derived from time rather than persisted)
- `PolicyEffect`: ALLOW, DENY
- `DecisionEffect`: ALLOW, DENY, ALLOW_WITH_OBLIGATIONS
- `PrincipalType`: USER, TEAM, ORGANIZATIONAL_UNIT, AGENT, SERVICE
- `GrantSource`: ROLE_ASSIGNMENT, RESOURCE_GRANT, TEAM_POLICY, SERVICE_POLICY, DELEGATION, SYSTEM_POLICY

IDs should be strongly typed value objects consistent with the existing `UserId` and `NoteId` approach. Domain logic must not depend on database-generated integer IDs.

### 3.2 CapabilityDefinition

A registered capability is a named action in the system's authorization vocabulary.

Conceptual fields:
```python
@dataclass(frozen=True)
class CapabilityDefinition:
    id: CapabilityId
    name: str  # e.g. "team.members.remove"
    allowed_scope_types: frozenset[ScopeType]
    inheritable: bool
    delegable: bool
    allowed_actor_types: frozenset[ActorType]
    sensitive: bool
```

Invariants:
- Capability names are unique and canonical (lowercase dotted names).
- Requests for unknown capabilities are denied; clients cannot invent capabilities.
- Scope inheritance is explicit per capability, never globally assumed.
- A capability definition describes where/how it can be granted; it does not itself grant authority.
- Capability registry changes are controlled and auditable.

### 3.3 RoleDefinition

A role is a reusable, versioned bundle of registered capabilities and, where needed, constraints/metadata.

Conceptual fields:
```python
@dataclass(frozen=True)
class RoleDefinition:
    id: RoleId
    tenant_id: TenantId | None  # None only for protected platform/system roles
    name: str
    description: str
    capability_ids: frozenset[CapabilityId]
    is_system: bool
    version: int
```

Invariants:
- Role definitions do not identify who holds the role; that is a RoleAssignment.
- System/platform roles cannot be modified by tenant administrators.
- Tenant-custom roles cannot include unknown capabilities or capabilities outside the tenant's grantable authority.
- Editing a role must have defined versioning semantics. Existing assignments must not silently acquire unexpected privileges without an auditable, controlled role-version update.
- A role is not a resource ACL and does not automatically grant access to every resource in its scope.

### 3.4 RoleAssignment

A role assignment binds a principal to a role at a specific scope.

Conceptual fields:
```python
@dataclass(frozen=True)
class RoleAssignment:
    id: RoleAssignmentId
    tenant_id: TenantId | None
    principal: PrincipalRef
    role_id: RoleId
    scope: ScopeRef
    assigned_by: ActorRef
    assigned_at: datetime
    expires_at: datetime | None
    status: AssignmentStatus
```

Invariants:
- Principal, role, scope, and tenant relationships must be validated together.
- A tenant role assignment cannot cross tenant boundaries.
- Platform roles require platform-scoped assignment and privileged assignment authority.
- The assigning actor cannot grant beyond their own grantable scope or authority.
- Revoked and expired assignments contribute no capabilities.
- Assignment, revocation, expiry, and role reassignment are auditable.
- Role assignment does not itself imply resource access; resolved capabilities remain subject to resource policy and mandatory constraints.

### 3.5 PrincipalRef and ResourceGrant

`PrincipalRef` identifies the recipient of a grant: user, team, organizational unit, agent, or service. Only supported principal types may be used for a given resource type.

Conceptual grant:
```python
@dataclass(frozen=True)
class ResourceGrant:
    id: GrantId
    tenant_id: TenantId
    resource_type: str
    resource_id: str
    principal: PrincipalRef
    capability_ids: frozenset[CapabilityId]
    granted_by: ActorRef
    granted_at: datetime
    expires_at: datetime | None
    status: AssignmentStatus
```

Invariants:
- Every resource grant is tenant-bound.
- The target resource must belong to the same tenant.
- The resource type declares which principal types and capabilities it supports.
- Grantor authority is checked before grant creation/revocation; a grant cannot create authority the grantor is not permitted to delegate.
- Revocation takes effect on subsequent authorization evaluations; caches must not preserve revoked authority.
- Existing Notes owner/READ/WRITE rules remain authoritative until a deliberate migration changes them.

Do not force all resource permissions into generic capability grants if the existing domain has a useful, smaller permission vocabulary. A mapping/adapter can translate legacy `PermissionLevel.READ/WRITE` into authorization inputs while preserving the aggregate invariants.

### 3.6 PolicyRule and PolicySet

A policy rule is a versioned declarative constraint over a registered action and trusted request attributes.

Conceptual structure:
```python
@dataclass(frozen=True)
class PolicyRule:
    id: PolicyId
    version: PolicyVersion
    scope: ScopeRef
    effect: PolicyEffect
    capability_ids: frozenset[CapabilityId]
    condition: PolicyCondition
    obligations: tuple[Obligation, ...]
    enabled: bool
```

A `PolicyCondition` is a typed, validated expression tree (e.g. equality, membership, comparison, boolean composition) over an allowlisted attribute vocabulary. It is not arbitrary Python, SQL, user-supplied code, or an unrestricted expression evaluator.

A `PolicySet` is an immutable published snapshot of policy rules with a stable version/reference. An evaluation must use one coherent published version, not a mixture of drafts and active rules.

Invariants:
- Unknown attributes/operators and malformed conditions fail closed.
- Missing attributes do not satisfy positive conditions.
- Tenant policy cannot weaken platform-mandated tenant/trust constraints.
- Applicable explicit DENY rules override ordinary grants.
- Policy authoring, approval, publication, rollback, and version changes are audited.
- Policy priority must not allow an ordinary ALLOW to override a mandatory boundary or applicable DENY.

### 3.7 Delegation

A delegation conveys bounded authority from a delegator to a delegate actor. Autonomous agent authority is represented by service/agent policy without a human delegator.

Conceptual fields:
```python
@dataclass(frozen=True)
class Delegation:
    id: DelegationId
    tenant_id: TenantId
    delegator: ActorRef | None
    delegate: ActorRef
    capability_ids: frozenset[CapabilityId]
    scopes: frozenset[ScopeRef]
    task_id: str | None
    not_before: datetime
    expires_at: datetime
    created_by: ActorRef
    revoked_at: datetime | None
```

Invariants:
- Delegate and delegator are distinct identities when delegation is used.
- Delegation is tenant-bound, time-bounded, scope-bounded, and optionally task-bound.
- Only capabilities marked delegable may be conveyed.
- Delegation cannot exceed the delegator's effective authority, the delegate's own policy, tenant constraints, resource constraints, or global policy.
- Revocation and expiry are checked on each operation.
- Child delegation, if supported, must attenuate authority further; no authority amplification.
- Sensitive actions may require approval and separation of duties; the requesting actor cannot approve their own sensitive action.

### 3.8 AuthorizationContext

An immutable context is assembled by trusted application infrastructure for one decision.

Conceptual fields:
```python
@dataclass(frozen=True)
class AuthorizationContext:
    actor: ActorRef
    tenant_id: TenantId
    memberships: tuple[MembershipRef, ...]
    role_assignment_refs: tuple[RoleAssignmentId, ...]
    delegation_id: DelegationId | None
    authentication_assurance: str | None
    task_id: str | None
    correlation_id: str
    evaluated_at: datetime
```

The context should contain trusted references and facts, not an unbounded copy of raw identity-provider claims. Attribute resolution may retrieve additional trusted data through application ports. A request cannot choose its own actor, tenant, clearance, memberships, role assignments, or approval status.

### 3.9 AuthorizationRequest

A request describes the intended operation, target, and context.

Conceptual fields:
```python
@dataclass(frozen=True)
class AuthorizationRequest:
    context: AuthorizationContext
    capability_id: CapabilityId
    resource: ResourceRef | None
    environment: EnvironmentAttributes
```

Use typed resource references and typed environment attributes. Create operations may have no existing resource, but must still carry a validated tenant and any required target scope. Resource lookup and authorization must be coordinated safely to preserve non-disclosure and transaction/concurrency semantics.

### 3.10 AuthorizationDecision and obligations

The decision is an immutable result, not a Boolean.

Conceptual fields:
```python
@dataclass(frozen=True)
class AuthorizationDecision:
    id: DecisionId
    effect: DecisionEffect
    actor: ActorRef
    tenant_id: TenantId
    capability_id: CapabilityId
    resource: ResourceRef | None
    reason_code: str
    matched_policy_refs: tuple[PolicyVersionRef, ...]
    grant_sources: tuple[GrantSource, ...]
    obligations: tuple[Obligation, ...]
    evaluated_at: datetime
    correlation_id: str
```

An obligation is a typed requirement with validated parameters, for example MFA/step-up, approval, second approver, field redaction, read-only projection, rate limit, or heightened audit.

Invariants:
- Unknown obligations are not considered satisfied.
- The use case/PEP must enforce obligations before the protected effect occurs.
- A decision with unsatisfied obligations must not be treated as unconditional ALLOW.
- Reason codes and matched policy details are internal; external responses must not leak sensitive policy information.
- The decision and resulting action/outcome are auditable with actor/delegator attribution.

## 4. Grant resolution and decision semantics

The evaluator should conceptually perform these phases:

1. Validate actor status, authentication context, tenant membership, and trusted context.
2. Validate resource tenant and scope. Mandatory tenant/trust boundaries cannot be overridden.
3. Validate agent/service credential, audience, expiry, delegation, task, and revocation constraints.
4. Resolve a coherent published policy snapshot and trusted attributes.
5. Evaluate applicable mandatory constraints and explicit DENY rules. Any applicable deny terminates with DENY.
6. Resolve grants from applicable role assignments, resource grants, team/org policies, service policy, and delegation.
7. Validate capability registration, allowed actor type, scope compatibility, and capability-specific inheritance.
8. Evaluate ABAC conditions; missing or invalid required attributes do not satisfy conditions.
9. Derive required obligations. The PEP must satisfy them before execution.
10. Return ALLOW only when a valid grant remains and all mandatory conditions are met; otherwise default DENY.

Effective delegated authority is constrained by intersection:
`P_effective = P_agent ∩ P_delegator ∩ P_tenant ∩ P_resource ∩ P_policy`.

This is a semantic constraint, not necessarily a literal set-intersection implementation; contextual conditions and obligations must also be evaluated.

## 5. Domain services and application ports

Keep domain behavior pure. Suggested responsibilities:

### Domain services
- `CapabilityResolver`: derive candidate capabilities from active role assignments and grant sources.
- `ScopeMatcher`: determine exact/ancestor/contextual scope applicability according to capability rules.
- `PolicyEvaluator`: evaluate validated policy conditions and deny semantics against supplied attributes.
- `DelegationAuthority`: validate delegation bounds and attenuation.
- `AuthorizationDecisionFactory`: construct consistent immutable outcomes/reason codes.

These can be pure domain services or application services depending on whether they require repository access. Do not let them perform database/network I/O.

### Application ports
- `AuthorizationProvider`: primary decision port used by use cases; evolves ADR-004's current `decide(principal, action, note)` shape to a typed authorization request.
- `RoleRepository` / `RoleAssignmentRepository`
- `CapabilityRegistry`
- `ResourceGrantRepository`
- `PolicyRepository` (published policy snapshots)
- `AuthorizationAttributeProvider` (trusted actor/resource/environment attributes)
- `DelegationRepository`
- `ApprovalProvider` (if approval workflows are introduced)
- `AuthorizationAuditPort` or existing audit/outbox abstraction

Repositories return domain objects, not ORM rows. The evaluator receives a coherent snapshot of required facts; it must not perform hidden N+1 repository calls.

## 6. Proposed package boundaries

Adapt to the current package layout rather than creating a second identity system:

```
src/notes_service/domain/
  identity/                 # existing User, ExternalIdentity, Principal
  authorization/
    identifiers.py
    enums.py
    actor.py
    scope.py
    capability.py
    role.py
    role_assignment.py
    principal_ref.py
    resource_grant.py
    policy.py
    policy_condition.py
    delegation.py
    context.py
    request.py
    decision.py
    obligations.py
    services/
      capability_resolver.py
      scope_matcher.py
      policy_evaluator.py
      delegation_authority.py

src/notes_service/application/
  ports/
    authorization_provider.py
    role_repository.py
    role_assignment_repository.py
    resource_grant_repository.py
    policy_repository.py
    authorization_attribute_provider.py
    delegation_repository.py
```

Do not move existing `Principal` or duplicate `UserId`. Human actor references should bridge to existing identity domain types. Keep Notes-specific `PermissionLevel` and aggregate invariants in the Notes context until a migration is designed.

## 7. Persistence guidance (not a migration specification)

Likely persistence concepts, subject to later schema design:
- capability definitions (may initially be code-defined and version-controlled)
- role definitions and role-capability associations
- role assignments
- resource grants (existing note permission rows may remain the Notes-specific representation)
- policy sets, rules, versions, publication state
- delegations and revocation state
- approval records
- authorization/audit events

Tenant-scoped rows should carry tenant identity and use composite constraints where appropriate to prevent cross-tenant references. Do not make polymorphic IDs a substitute for referential integrity where concrete foreign keys are feasible. Policy snapshots need stable version identity. Revocation and expiry must be reflected in evaluation; cache invalidation is part of the design.

Do not implement all these tables in one migration before the domain and rollout slices are approved.

## 8. Compatibility with the existing Notes domain

The existing domain currently has:
- `Principal` with `user_id` as the authoritative human identity.
- `Note.owner_id` and `NotePermission` with READ/WRITE levels.
- Aggregate invariants for owner-only content update, sharing, revocation, and folder changes.
- An application-layer `AuthorizationProvider` port and `DefaultAuthorizationProvider` per ADR-004.

The target authorization model must not weaken these invariants. In particular:
- Owner remains full-control principal under current Notes behavior.
- READ collaborator can read only.
- WRITE collaborator can read/update only.
- Collaborators cannot delete, share, or manage permissions.
- Folder ownership remains separate from note sharing.
- Resource existence concealment (404 where appropriate) remains a presentation/security behavior.
- The policy engine does not replace aggregate invariants or optimistic concurrency.

ADR-004 remains the accepted description of current behavior. ADR-011 defines the proposed enterprise target. When implementation changes the port contract, update ADR-004 or supersede it explicitly and retain the existing behavior as regression tests.

## 9. Required invariants and adversarial tests

### Identity and tenant
- A request cannot choose another actor or tenant by changing request fields.
- Inactive/suspended actors cannot act.
- Tenant A role assignment/grant/policy cannot authorize Tenant B resources.
- A resource whose tenant does not match the trusted context is denied without leaking its existence.

### Role and capability
- Role definition does not grant authority until assigned.
- Revoked/expired role assignment grants nothing.
- Unknown capability is denied.
- Tenant cannot mutate protected system/platform roles.
- Assignment actor cannot grant authority outside their scope or grantable authority.
- Capability scope inheritance works only when explicitly enabled.

### Team and resource
- Team admin authority applies only to the assigned team.
- Team membership alone does not grant access to private notes.
- Team resource grant applies only to supported resource types and current members.
- Revocation/removal takes effect on the next authorized operation.
- Manager/reporting relationship alone does not grant resource access.

### Policy and obligations
- Applicable explicit DENY beats every ordinary grant source.
- Missing attributes fail conditions closed.
- Policy evaluation error fails closed.
- Tenant policy cannot override mandatory platform constraints.
- Unknown obligation is not silently ignored.
- Approval/MFA/redaction obligations are enforced by the PEP before the effect.

### Delegation and audit
- Agent cannot exceed its own service policy or delegator's effective authority.
- Expired, revoked, wrong-task, wrong-audience, or out-of-scope delegation is denied.
- Actor and delegator are separately attributable.
- Policy/role/grant/delegation changes and protected decisions are auditable.
- No secrets, tokens, raw prompts, or protected note content are written to security audit records by default.

## 10. Open design decisions before implementation

These require explicit review rather than accidental assumptions:

1. **Role version semantics:** Should assignments pin a role version, or should changes to a role definition affect all active assignments after controlled publication? Recommendation: version roles and make privilege-changing updates explicit, audited, and reviewable; choose pinning vs current-version semantics per rollout needs.
2. **Deny policy administration:** Which platform-level deny constraints are immutable to tenants, and which tenant-scoped denies may tenant admins author?
3. **Team grants:** Should team membership be evaluated dynamically at request time, or materialized into per-user grants? Recommendation: evaluate current membership dynamically; avoid fan-out grants.
4. **Resource permission migration:** Keep NotePermission as the Notes-specific ACL initially and adapt it into the unified evaluator; do not prematurely generalize every resource.
5. **Policy language:** Begin with typed, code-defined policy conditions and a constrained expression AST. Do not expose a policy authoring UI or arbitrary expression language until validation, simulation, publication, and audit are ready.
6. **Decision caching:** Start without authorization decision caching unless profiling requires it; if introduced, define invalidation for actor status, memberships, assignments, grants, policies, delegations, and resource changes.
7. **Approval lifecycle:** Define approval as a separate workflow/record with approver identity and separation-of-duties rules; do not treat a client-supplied `approved=true` flag as evidence.
8. **Platform support access:** Decide whether support access is time-bound, just-in-time, approval-gated, and content-redacted. Platform admin must not imply tenant private-content access.

## 11. Recommended implementation sequence

1. Define typed authorization request/context/decision and evolve the existing provider port with compatibility adapters.
2. Preserve and test existing Notes owner/READ/WRITE behavior through the provider.
3. Introduce registered capabilities and pure scope matching.
4. Add role definitions and scoped role assignments for administrative/team capabilities.
5. Add resource/team grants where a concrete use case requires them.
6. Add trusted ABAC attributes and typed policy conditions.
7. Add versioned policy sets and controlled policy administration.
8. Add delegation and agent/service actor enforcement.
9. Add obligations/approval workflows for sensitive actions.
10. Add tenant-scoped persistence constraints, audit/outbox reliability, and adversarial integration tests alongside each slice.

Do not implement this entire model as a single big-bang authorization rewrite. Each slice must preserve tenant isolation, default-deny behavior, and the current Notes regression contract.
