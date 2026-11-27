# ADR-011: Unified Enterprise Authorization Model

## Status

Proposed

## Context

The current Notes application has a replaceable `AuthorizationProvider` and deny-by-default owner/collaborator rules documented in ADR-004. Enterprise expansion introduces organizational hierarchies, teams, administrators, agents, service principals, delegated actions, contextual attributes, and policy administration. A simple owner-versus-collaborator matrix will not scale to those requirements, but replacing it with scattered role checks or a universal administrator bypass would create inconsistent and unsafe behavior.

We need one coherent authorization model that supports multiple mechanisms while preserving the current Notes semantics and tenant isolation.

## Decision

Adopt a unified, policy-driven authorization architecture. Authorization mechanisms are complementary inputs to a single structured decision:

- Role-Based Access Control (RBAC): reusable capability bundles.
- Scoped RBAC: assignments bound to platform, tenant, org unit, department, or team scope.
- Attribute-Based Access Control (ABAC): trusted subject, resource, action, and environment attributes.
- Resource permissions / ACLs: explicit grants to supported principal types on specific resources.
- Capability-based authorization: registered, granular actions resolved from grant sources.
- Administrative privileges: granular capabilities assigned within explicit administrative scopes; no universal admin bypass.
- Delegated authority: human, agent, or service authority constrained by least privilege, scope, task, time, tenant, resource, and policy.

### Policy semantics

1. Mandatory trust, actor-status, tenant, and delegation boundaries deny when violated.
2. Applicable explicit deny policies override ordinary grants.
3. Grants may come from roles, scoped roles, team/org policy, resource ACLs, service policy, or delegation.
4. ABAC conditions and scope constraints limit the applicability of grants.
5. Required obligations (e.g. MFA, approval, redaction, limits, audit) must be enforced before execution.
6. No applicable grant results in deny (default deny).
7. Missing security context, unknown actions/conditions/obligations, or evaluation failure fail closed.

### Scope and inheritance

Scope hierarchy includes Platform, Tenant/Organization, Organizational Unit, Department, Team, and Resource. Inheritance is capability-specific and explicit; no blanket parent-to-child permission propagation. Tenant context is derived from trusted authentication and validated membership, not client input.

### Administrative boundaries

Administrative roles are composed of capabilities and scoped assignments. Administrative authority does not imply private resource content access. Team, department, and tenant administrators may not elevate their own scope or grant authority beyond their own grantable authority. Platform administration is distinct from tenant administration.

### Agents and services

Agents and service principals use the same policy semantics as human actors. Delegated effective authority is the intersection of agent/service policy, delegator authority (where present), tenant constraints, resource constraints, and policy. Delegation is scoped, time-bound, revocable, task-bound as appropriate, and re-evaluated on every operation. High-risk actions may require approval or separation of duties.

### Decision contract

Authorization returns a structured decision rather than only a Boolean: effect, actor, tenant, action, resource, reason code, matched policy/version references, obligations, timestamp, and correlation/task metadata as appropriate. Internal reason codes must not be exposed in a way that enables sensitive policy probing.

### Architecture

Keep PDP, PEP, PIP, PAP, capability resolution, scope resolution, policy storage, and decision audit as logical components within the modular monolith initially. The application layer owns enforcement through use cases; routes do not implement authorization policy. Keep policy behind the application port and avoid a god-object security service.

### Compatibility

The existing owner/READ/WRITE Notes rules remain the baseline and regression contract until enterprise authorization is intentionally implemented. This ADR describes the target architecture, not controls already implemented.

## Alternatives considered

- **RBAC-only:** Rejected because roles cannot express resource ownership, contextual attributes, time/task constraints, or delegation without role explosion.
- **ABAC-only:** Rejected as the only mechanism because roles are a practical, manageable way to bundle common capability sets and administration.
- **ACL-only:** Rejected because it does not adequately express organization-wide administrative or contextual rules.
- **Universal admin bypass:** Rejected because it creates excessive privilege and conflates administration with content access.
- **Separate authorization microservice immediately:** Rejected as premature. Keep logical boundaries and ports inside the modular monolith; extract later if operational or scaling needs justify it.
- **External policy engine now:** Deferred. Maintain a replaceable port and deterministic semantics; evaluate engines when policy complexity and operational requirements warrant.
- **Scattered route checks:** Rejected because enforcement becomes inconsistent and hard to test.

## Consequences

### Positive

- Supports enterprise organizations, teams, scoped administrators, human/agent/service actors, resource sharing, and contextual rules.
- Consistent policy semantics and enforcement points.
- Least privilege and tenant boundaries are explicit.
- Policies and decisions can be tested independently.
- External policy engine or policy administration capabilities can be added behind stable interfaces.
- Existing Notes behavior can be preserved during migration.

### Tradeoffs / risks

- Policy evaluation and trusted attribute resolution become more complex.
- Policy and grant changes require careful audit, cache invalidation, revocation, and concurrency handling.
- Scope inheritance must be explicit to prevent privilege leakage.
- Obligations add enforcement complexity; returning them without executing them is unsafe.
- Authorization must be designed alongside tenant-scoped repositories, data constraints, and audit persistence.

## Implementation guidance

See:
- [Enterprise Authorization Architecture](../AUTHORIZATION_ARCHITECTURE.md)
- [Authorization Policy Model](../AUTHORIZATION_POLICY_MODEL.md)

Implementation should begin with a tested capability/scope/decision core and preserve current Notes permissions. Add role assignments, team/org scopes, attribute policies, policy administration, and agent delegation incrementally with adversarial tenant-boundary tests.
