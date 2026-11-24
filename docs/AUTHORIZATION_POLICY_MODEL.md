# Authorization Policy Model

> Target design. This describes proposed enterprise authorization behavior, not currently implemented controls.

See [Authorization Architecture](AUTHORIZATION_ARCHITECTURE.md) for the system overview and [ADR-011](ADR/ADR-011-unified-authorization-model.md) for the decision record.

## 1. Policy concepts

| Concept | Definition |
|---|---|
| Actor | Authenticated human, agent, or service performing the request |
| Capability | Named action the actor may potentially perform |
| Role | Reusable set of capabilities |
| Role assignment | Role assigned to a principal at a specific scope |
| Grant | Authority provided by a role, ACL, team policy, service policy, or delegation |
| Scope | Boundary where a role or grant applies |
| Attribute | Trusted fact about actor, resource, action, or environment |
| Policy | Rule that constrains or permits a request |
| Obligation | Required control that must be fulfilled before action execution |
| Decision | Structured authorization result |
| Delegation | Time/task/resource-limited authority conveyed to another actor |

## 2. Capability naming

Use lowercase dotted names with stable domain/action semantics.

Pattern:
`<domain>.<resource-or-area>.<action>` or a concise domain-action pattern for established vocabulary.

Examples:
- `notes.read`
- `notes.update`
- `notes.share`
- `team.members.remove`
- `tenant.policies.manage`
- `agents.execute`
- `audit.export`
- `platform.tenants.manage`

Capabilities must be registered. Arbitrary client-provided capability strings are not valid authorization requests.

## 3. Scope types

Initial scope types:
- `platform`
- `tenant`
- `organizational_unit`
- `department`
- `team`
- `resource`

Every scoped role assignment or grant has a scope type and identifier (platform scope may use a canonical singleton identifier or explicit representation).

A capability definition declares:
- Which scope types may grant it.
- Whether scope inheritance is permitted.
- Whether it is administrative or sensitive.
- Whether it requires obligations or additional controls.
- Whether it may be delegated.
- Whether the capability is applicable to human, agent, or service actors.

Inheritance is capability-specific and explicit. Never infer that all capabilities on a parent scope apply to every descendant.

## 4. Role assignment semantics

A role assignment is a principal-to-role relationship scoped to an organization boundary and optionally time-limited.

Conceptual fields:
```
RoleAssignment
- assignment_id
- tenant_id (nullable only for platform-scoped assignments)
- principal_id
- principal_type (human | agent | service)
- role_id
- scope_type
- scope_id
- assigned_by
- created_at
- expires_at (optional)
- status
```

Validation:
- Assigning actor must possess a capability that permits assignment at that scope.
- The actor cannot grant authority outside their own grantable scope or above their own authority.
- Protected system roles and platform roles cannot be tenant-modified.
- Role assignment changes must be audited.
- Expired or revoked assignments must not contribute capabilities.

## 5. Resource grant semantics

A resource grant is explicit access to a resource, independent of role assignment.

Conceptual fields:
```
ResourceGrant
- grant_id
- tenant_id
- resource_type
- resource_id
- principal_id
- principal_type (human | team | org_unit | agent | service)
- permission_or_capability
- granted_by
- created_at
- expires_at (optional)
- status
```

Only resource types that explicitly support group/team/org grants may accept those principal types. A grant must be tenant-bound and validated. Resource owners may manage grants only where domain policy permits; owner-only operations in the current Notes domain remain owner-only until intentionally superseded.

## 6. Policy rule structure

The implementation may use code policies initially and a policy repository/authoring UI later. Regardless of representation, policies should be validated and versioned.

Conceptual rule:
```
PolicyRule
- policy_id
- version
- tenant_scope (or platform scope)
- effect (allow | deny)
- actions (registered capabilities)
- subject_conditions
- resource_conditions
- environment_conditions
- obligations
- priority/metadata (only if required for deterministic ordering)
- enabled
- created_by
- published_at
```

Rules should be declarative and validated, not arbitrary executable Python or user-supplied code.

## 7. Decision semantics

The decision engine follows these principles:

1. Mandatory trust, tenant, actor-status, and delegation constraints are checked first; violations deny.
2. Applicable explicit deny rules override ordinary grants.
3. Grants are collected from applicable role assignments, scoped role grants, team/org policies, resource grants, service policy, and delegated authority.
4. ABAC conditions restrict the applicability of grants. Missing required attributes do not satisfy conditions.
5. Obligations are returned when a policy requires additional controls. The application enforcement point must fulfill them before the operation proceeds.
6. If no valid grant remains, deny.
7. If the policy evaluation system errors or context is incomplete, fail closed for security-sensitive actions.

Do not treat deny/allow rules as a universal Boolean expression where a grant can override a mandatory security boundary.

## 8. Scope matching

A grant applies only if the requested resource/action is within the grant's scope and the capability allows that scope type.

Scope match can be:
- Exact: resource/team/department matches assignment scope.
- Ancestor: resource lies beneath scope, only when capability explicitly permits inheritance.
- Contextual: attributes satisfy policy, e.g., actor is current member of the target team.

Scope membership must be current and server-resolved. Cached membership must have an explicit freshness/invalidation strategy.

## 9. Attributes and condition evaluation

ABAC conditions may use trusted attributes such as:
- Actor status and type
- Tenant membership/status
- Team/department memberships
- Actor clearance and authentication assurance
- Resource tenant, classification, owner, sensitivity, type
- Delegation constraints and task context
- Request approval status
- Environment attributes from trusted infrastructure

Client values such as tenant ID, clearance, role, scope, or approval state must never be trusted without authoritative validation.

A missing attribute used in a condition evaluates as unsatisfied; it must not be treated as a wildcard or default allow.

## 10. Obligations

Initial obligation vocabulary may include:
- `require_mfa`
- `require_approval`
- `require_second_approver`
- `redact_fields`
- `limit_fields`
- `read_only`
- `require_audit`
- `rate_limit`
- `resource_limit`

Obligations are typed and validated. The use case or orchestration layer must have an explicit handler for each obligation; unknown obligations are not satisfied by default.

## 11. Delegation

Delegation records specify:
- Delegator (optional for autonomous agents)
- Delegate actor
- Allowed capabilities
- Scope/resources
- Tenant boundary
- Task/context identifier
- Not-before/expiry
- Revocation state
- Approval requirements, if any

Effective authority is constrained by the intersection of agent/service policy, delegator authority, tenant, resource, and global policy. Each request is re-authorized using current policy and membership/grant state. Delegation may not include capabilities prohibited from delegation.

## 12. Decision payload

Conceptual contract:
```
AuthorizationDecision
- effect (allow | deny | allow_with_obligations)
- actor_id
- actor_type
- delegated_by (optional)
- tenant_id
- action
- resource_type/resource_id (optional)
- reason_code
- matched_policy_ids and policy_versions
- obligations
- evaluated_at
- correlation_id
```

Reason codes are stable internal identifiers. External API responses must not expose sensitive policy details that could assist probing or circumvention.

## 13. Policy administration and lifecycle

Policy administration should support:
1. Draft creation and edits.
2. Schema/type validation.
3. Static analysis for unsafe rules and contradictory or unreachable conditions where feasible.
4. Test cases/simulation before publishing.
5. Versioned publication.
6. Rollback to a previous known-good version.
7. Change approval for sensitive policies.
8. Audit trail of author, approver, publisher, version, and change.
9. Evaluation against the published version; no partial or unversioned policy state.
10. Explicit policy cache invalidation.

Tenant policy administration cannot modify platform mandatory constraints. Tenant policies cannot cross tenant boundaries. Sensitive policy changes may require dual control/separation of duties.

## 14. Minimum authorization test matrix

| Dimension | Positive case | Negative case |
|---|---|---|
| Actor status | Active actor | Disabled/suspended actor |
| Tenant | Correct tenant | Cross-tenant target or forged tenant |
| Role | Role capability present | Missing capability |
| Scope | Assignment covers target | Scope mismatch |
| Inheritance | Capability permits inheritance | Inheritance disabled |
| Team | Current member/team grant | Stale or removed membership |
| Resource ACL | Valid grant | Missing/revoked/expired grant |
| ABAC | All trusted conditions met | Condition fails or attribute missing |
| Deny | No matching deny | Explicit deny overrides allow |
| Admin | Scoped admin action | Admin attempts out-of-scope action or private-data bypass |
| Agent | Within delegated authority | Exceeds authority, expired/revoked delegation, wrong task/audience |
| Obligations | Controls fulfilled | Approval/MFA/obligation missing |
| Error behavior | Policy evaluated successfully | Evaluator or context failure fails closed |
| Audit | Attribution recorded | Event missing or actor/delegator collapsed |

## 15. Compatibility and migration

The current Notes authorization model remains the initial policy:
- Owner: full control.
- READ collaborator: read only.
- WRITE collaborator: read and update.
- Collaborators cannot delete, share, or manage permissions.
- Unreadable resources may return 404 to preserve resource-existence confidentiality.

Enterprise authorization should be introduced behind the authorization port. Migration must preserve existing permission behavior unless a reviewed change explicitly alters it. Existing tests become regression tests for the policy engine.
