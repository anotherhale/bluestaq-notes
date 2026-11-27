# Enterprise Authorization Architecture

> Status: Architecture proposal for review and implementation
> Scope: Bluestaq Notes enterprise expansion
> Related: [ADR-004](ADR/ADR-004-authorization-provider.md), [ADR-011](ADR/ADR-011-unified-authorization-model.md), [Security](SECURITY.md), [Domain Model](DOMAIN_MODEL.md)

## 1. Purpose

Authorization is a first-class, unified policy system. It must support simple owner/collaborator rules now while extending to enterprise teams, organizational administration, attributes, agents, service principals, and delegated authority without scattering security logic across routes or use cases.

Authorization models are complementary. Role-Based Access Control (RBAC), scoped RBAC, Attribute-Based Access Control (ABAC), resource permissions/ACLs, capability grants, administrative privileges, and delegation are evaluated as inputs to one decision.

## 2. Design principles

1. **Default deny:** No applicable grant means deny. Unknown actions, incomplete context, policy errors, and unsupported conditions do not grant access.
2. **Tenant isolation is mandatory:** Tenant boundaries are security constraints, not ordinary policy grants.
3. **Explicit deny overrides ordinary grants:** A matching deny policy cannot be overcome by a role or resource grant.
4. **No admin bypass:** Administrative capabilities are explicit and scoped. Admin status does not automatically grant access to private note content.
5. **Organization is not authorization:** Reporting relationships, department membership, and team membership alone do not grant access to private resources.
6. **Capabilities are the authorization vocabulary:** Roles and other grant sources provide capabilities, subject to scope and constraints.
7. **One policy model for every actor:** Humans, agents, and services use common policy semantics and enforcement.
8. **Least privilege and authority attenuation:** Delegation cannot increase the authority of the delegator, agent, tenant, resource, or policy.
9. **Enforce at application boundaries:** Routes do not contain business authorization logic; protected use cases invoke the authorization port.
10. **Auditable decisions:** Security-relevant decisions and outcomes must be attributable without logging secrets or protected content.

## 3. Authorization mechanisms

### 3.1 RBAC — Role-Based Access Control

Roles are reusable bundles of capabilities. Roles do not automatically confer unrestricted resource access.

Initial role examples:
- Employee
- Team Member
- Team Lead
- Team Admin
- Department Admin
- Tenant Admin
- Security Admin
- Agent Operator
- Auditor
- Platform Support
- Platform Admin

Role definitions are customizable by a tenant subject to protected system roles, role-assignment policy, and separation-of-duty controls.

### 3.2 Scoped RBAC

A role assignment includes a scope, such as platform, tenant, organizational unit, department, or team. A Team Admin assignment applies only to the assigned team; a Department Admin assignment applies only to the assigned department.

Scope inheritance is explicit and capability-specific. Do not automatically propagate every parent capability to child scopes. Each capability defines whether inheritance is allowed and how child resources are included.

### 3.3 ABAC — Attribute-Based Access Control

Policies may consider attributes of:
- **Subject/actor:** actor type, status, tenant, memberships, roles, clearance, authentication assurance, agent identity, delegation/task context.
- **Resource:** tenant, owner, classification/sensitivity, team/department, type, state.
- **Action:** requested operation and its risk category.
- **Environment:** time, request origin, approval state, authentication strength, task limits, and other trusted context.

Only trusted, validated attributes may be used for security decisions. Client-supplied values are not authoritative.

### 3.4 Resource permissions / ACLs

Explicit grants on a resource can be given to a user, team, organizational unit, agent, or service principal where supported by policy. Resource grants are independent of role assignments.

Current Notes permissions remain owner, READ, and WRITE semantics until the enterprise model is implemented. Owners have full note control. READ grants permit reading only. WRITE grants permit reading and updating, but not deleting or managing sharing. Sharing, revocation, and delete remain owner-only under the current model.

### 3.5 Capability-based authorization

Capabilities name actions (e.g. `notes.read`, `team.members.remove`, `tenant.policies.manage`). Capabilities may be granted through roles, scoped role assignments, resource grants, team policy, service policy, or delegation. They are evaluated with scope, constraints, and applicable policies; possession of a capability string alone is not sufficient.

### 3.6 Administrative privileges

Administrative roles are composed from granular administrative capabilities and scope. Do not create a universal magic `ADMIN` bypass.

Examples:
- Tenant administration: `tenant.users.manage`, `tenant.teams.manage`, `tenant.settings.manage`, etc., scoped to one tenant.
- Security administration: policy and security configuration capabilities, scoped to one tenant.
- Team administration: member/settings/eligible-role management scoped to a team.
- Platform administration: platform-level capabilities explicitly defined and protected.

Administrators do not automatically receive private note-content access. Administrative authority and data access are separate capabilities.

### 3.7 Delegated and agent authority

Autonomous agents and delegated agents are service actors with identity, status, policy, scope, and task context. A delegated agent is distinguishable from the human delegator; audit records identify both.

Effective authority is bounded by intersection, not union:

`P_{effective} = P_{agent} \cap P_{delegator} \cap P_{tenant} \cap P_{resource} \cap P_{policy}`

Delegations must be scoped to permitted actions/resources, time-bound, revocable, and task-bound where applicable. Reauthorize each tool/API operation; do not rely solely on token claims for resource permissions. High-risk operations (delete, sharing, external actions) may require approval, separation of duties, or step-up authentication.

## 4. Scope hierarchy

Logical scopes:
- Platform
- Tenant / Organization
- Organizational Unit
- Department
- Team
- Resource

Scope is part of role assignment and grant evaluation. Inheritance is not globally assumed; capabilities explicitly declare scope behavior. Tenant context is derived from trusted identity/membership and validated server-side, never trusted from a request field alone.

## 5. Decision precedence

The policy evaluator uses deterministic semantics:

1. Validate actor identity and status.
2. Establish trusted tenant context and membership.
3. Verify resource tenant matches the trusted tenant context; cross-tenant requests are denied.
4. Validate agent/service credentials, audience, expiry, task context, delegation, and scopes when applicable.
5. Apply mandatory security constraints (disabled actor, invalid trust context, classification constraints, prohibited action, etc.).
6. Evaluate applicable explicit deny policies. Any matching deny wins over ordinary grants.
7. Resolve applicable grants: role capabilities, scoped roles, team/org grants, administrative capabilities, resource ACLs, service policy, and delegation.
8. Apply ABAC conditions and constraints. Conditions must be satisfied for a grant to apply.
9. Resolve obligations (approval, MFA/step-up, redaction, limits, heightened audit, etc.). If required obligations are unsatisfied, do not execute the action.
10. Grant only when an applicable grant exists and every mandatory constraint/condition is satisfied. Otherwise deny (default deny).

Missing, malformed, or unavailable security context/policy information fails closed for security-sensitive actions.

## 6. Authorization decisions

Decisions are structured, not Boolean-only. A decision includes:
- effect: ALLOW, DENY, or ALLOW_WITH_OBLIGATIONS
- actor and tenant identifiers
- action and resource identifiers (when applicable)
- reason code
- matched policy identifiers/version references
- obligations
- evaluation timestamp
- correlation/task context as appropriate

Reason codes are internal for audit and diagnostics. Avoid revealing sensitive policy details to clients.

Possible obligations:
- REQUIRE_MFA / STEP_UP_AUTHENTICATION
- REQUIRE_APPROVAL
- REQUIRE_SECOND_APPROVER
- REDACT_FIELDS / LIMIT_FIELDS
- READ_ONLY
- REQUIRE_AUDIT
- RATE_LIMIT / RESOURCE_LIMIT

The Policy Decision Point (PDP) produces decisions. Application use cases act as Policy Enforcement Points (PEPs) and enforce both effects and obligations. Obligations cannot be silently ignored.

## 7. Logical components

These are logical modules inside the modular monolith initially, not separate services:
- **PDP:** evaluates policies and produces decisions.
- **PEP:** enforcement in application use cases.
- **PIP:** retrieves trusted subject, resource, and environment attributes.
- **PAP:** policy authoring, validation, versioning, and publication.
- **Capability resolver:** derives capabilities from roles and grant sources.
- **Scope resolver:** determines whether assignments apply to a target.
- **Policy repository:** stores policy definitions and versions.
- **Decision audit:** records decision metadata and outcomes.

Keep policy evaluation behind application ports. Avoid a giant SecurityService and avoid duplicated per-route checks.

## 8. Role catalog examples

Roles are initial templates and are not an exhaustive fixed list.

| Role | Scope | Example authority |
|---|---|---|
| Employee | Tenant | Self-service, own notes |
| Team Member | Team | Team resources according to policy |
| Team Lead | Team | Team operational privileges |
| Team Admin | Team | Team membership/settings and eligible role management |
| Department Admin | Department | Scoped department administration |
| Tenant Admin | Tenant | Tenant administration |
| Security Admin | Tenant | Security/policy administration |
| Agent Operator | Tenant/team | Agent lifecycle and configuration |
| Auditor | Tenant | Read-only audit access |
| Platform Support | Platform | Limited support capabilities |
| Platform Admin | Platform | Explicit platform administration |

## 9. Example capability vocabulary

Capabilities should be granular, versionable, and domain-oriented. Illustrative initial vocabulary:

- Notes: `notes.create`, `notes.read`, `notes.update`, `notes.delete`, `notes.share`, `notes.export`
- Teams: `team.members.read`, `team.members.invite`, `team.members.remove`, `team.settings.manage`, `team.roles.assign`, `team.resources.manage`
- Tenant: `tenant.users.view`, `tenant.users.manage`, `tenant.memberships.manage`, `tenant.teams.manage`, `tenant.org_structure.manage`, `tenant.roles.view`, `tenant.role_assignments.manage`, `tenant.policies.view`, `tenant.policies.manage`, `tenant.settings.manage`
- Agents: `agents.execute`, `agents.configure`, `agents.approve`, `agents.manage`
- Audit: `audit.read`, `audit.export`
- Platform: `platform.tenants.manage`, `platform.policies.manage`, `platform.security.manage`

This vocabulary is illustrative; implementation must validate an authoritative capability registry rather than accept arbitrary capability strings.

## 10. Examples

| Scenario | Expected decision | Rationale |
|---|---|---|
| Team Admin removes a member from their assigned team | Allow | Scoped team capability |
| Team Admin modifies a different team | Deny | Scope mismatch |
| Tenant Admin reads an employee's private note | Deny by default | Admin status does not grant content access |
| Manager reads direct-report private note | Deny by default | Reporting relationship alone is not access |
| Agent deletes a note while delegated only to summarize/read | Deny | Capability absent from effective authority |
| Role grants read but restricted-resource policy denies | Deny | Explicit deny takes precedence |
| User reads note shared with their team | Allow if current membership and grant policy validate | Team resource grant |
| Cross-tenant request | Deny | Mandatory tenant boundary |

## 11. Implementation guardrails

- Keep the current `AuthorizationProvider` port replaceable; evolve it through a new ADR rather than silently breaking ADR-004.
- The authorization context must be explicit and immutable per request/use case.
- Routes must not implement policy rules.
- Tenant-scoped repository contracts should require tenant context for enterprise resources; avoid lookups by resource ID alone.
- Resource-level decisions may require safe resource loading and evaluation inside transaction/concurrency boundaries; a universal preflight-only check is insufficient.
- Do not trust client-supplied tenant IDs, role names, team scopes, clearance, delegation, or agent attributes.
- Enforce authorization for every operation and every agent tool invocation.
- Avoid caching decisions without correct invalidation for membership, grant, policy, actor status, and resource changes.
- Sensitive denials should preserve existing non-disclosure semantics (e.g. 404 for unreadable resources where appropriate).
- Security-relevant mutations and audit records should be transactionally coordinated where feasible (e.g. transactional outbox).
- Never log note content, raw prompts, credentials, tokens, or secrets as authorization/audit data.

## 12. Verification requirements

At minimum, tests must cover:
- Default deny for unknown actions, missing grants, malformed context, and evaluation failures.
- Explicit deny wins over every ordinary grant source.
- Tenant isolation across read/update/delete/share/list/search and background/agent operations.
- Role capabilities and scope matching, including scope mismatch.
- Scope inheritance only where explicitly configured.
- ABAC positive and negative conditions.
- Resource permission precedence and revocation behavior.
- Administrative privilege boundaries and no admin data-access bypass.
- Manager/reporting relationship does not grant resource access by itself.
- Delegated-agent intersection/attenuation, expiry, revocation, task binding, and forbidden capabilities.
- Obligations are enforced (approval/MFA/redaction), not just returned.
- Audit attribution for human, agent, delegator, and service actors.
- Race/concurrency behavior where permission changes and resource actions overlap.

## 13. Rollout

The existing Notes authorization (owner/READ/WRITE) remains the baseline until enterprise authorization is implemented. This document defines the target architecture, not a claim that these mechanisms already exist in code. Begin with a tested policy core and scoped capability model, then add role assignments, teams, attributes, policy administration, and agent delegation incrementally.
