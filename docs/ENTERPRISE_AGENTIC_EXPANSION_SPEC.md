# Enterprise & Agentic Platform Expansion — Implementation Specification

Branch: `feature/agentic-enterprise-platform`
Status: Architecture and implementation contract; code implementation follows in sequenced work packages.

This expansion turns Bluestaq Notes into a compelling enterprise-ready, agent-enabled collaboration platform while preserving its modular monolith, Hexagonal Architecture, DDD boundaries, and security-first design.

## Product goal

Deliver a demonstrable enterprise workspace with:
- Hierarchical organizations, departments/divisions, teams, and employee directory.
- Administration console for organization, identity, team, access, integrations, policy, and audit management.
- Production-grade OIDC sign-in and account lifecycle integration.
- Agent identities and delegated user authority using short-lived, narrowly-scoped access tokens.
- Tool/capability interface for agents that calls the existing application service layer—never direct repository/database access.
- System One / Jev integration behind a vendor-neutral decision-provider port.
- Agent workflows for note discovery, summarization, action-item extraction, and creating/updating notes—with human approval for sensitive operations.
- Enterprise auditability, observability, policy enforcement, rate limits, and configurable deployment.

## Non-negotiable architectural rules

1. Keep a modular monolith unless measurable scaling or isolation needs justify services.
2. Domain and application layers must not import FastAPI, SQLAlchemy, JWT/OIDC SDKs, model vendor SDKs, or HTTP types.
3. All UI, REST, MCP/tool, and agent adapters must call application use cases.
4. Agents are untrusted callers. Their outputs and tool arguments are untrusted input.
5. Authentication proves identity; authorization is performed per operation/resource in the application.
6. A token scope is not a resource permission. Effective access must include agent policy, delegated authority (when applicable), user/org membership, and existing resource authorization.
7. No agent can elevate privilege, self-approve, transfer ownership, or grant access beyond the authority of its delegator.
8. Organization and team scoping must be explicit and deny-by-default.
9. Admin operations must have explicit role/policy checks and auditable events.
10. No secrets, bearer tokens, note contents, or sensitive prompts in logs.

## Target architecture

Inbound adapters:
- React workspace
- REST API
- Agent Tool API (initially application-owned tool contracts; MCP adapter may expose the same tools)
- OIDC callback/login endpoints
- Admin API

Application:
- Use cases/commands/queries for organization, employee, team, membership, notes, sharing, agent registration, delegation, policy, audit query, and administration.
- Authorization/policy services.
- Agent orchestration facade (does not itself trust or execute model decisions).
- Decision service.
- Ports: repositories, UoW, identity provider, authorization provider, token issuer/verifier, audit sink, agent registry, decision provider, LLM provider, policy provider, clock, rate limit, approval workflow.

Infrastructure:
- SQLAlchemy models and repositories; migrations; PostgreSQL production path.
- OIDC relying-party adapter.
- Token issuance/verification adapter.
- Jev/System One adapter.
- LLM adapter(s) behind vendor-neutral port.
- Audit sink/structured telemetry.
- Shared rate-limit provider interface (process-local dev adapter; shared-store adapter for deployment).

Composition root wires concrete adapters.

## Organization and employee domain

Implement an explicit organization hierarchy. Do not model this solely as arbitrary team labels.

Suggested entities/value objects:
- Organization (tenant/root)
- OrganizationUnit (hierarchical node: division, department, branch, program, etc.; configurable type)
- Team (belongs to an OrganizationUnit; team may have a parent team if supported, but avoid multiple competing hierarchy models)
- Employee/User (identity-linked person)
- OrganizationMembership (user ↔ organization; status, start/end, external identity)
- UnitMembership (user ↔ org unit; role/assignment)
- TeamMembership (user ↔ team; role, status, dates)
- OrgRole and TeamRole (initially enum-backed, later permission policy backed)
- ManagerRelationship or manager_id scoped to an organization (avoid unconstrained global manager pointer)
- OrganizationPath / hierarchy closure or adjacency representation, with cycle prevention.

Requirements:
- A person can be a member of multiple organizations (future-friendly); organization membership is the tenant boundary.
- Employees can belong to multiple teams and/or units.
- Reporting relationships must be scoped and validated; prevent cycles.
- Unit/team changes must be transactional.
- Support active, suspended, and deprovisioned states.
- Deactivated employees must immediately lose access.
- Avoid using email as a durable identifier; external identity is issuer + subject.
- Organization IDs must be carried through authorization and resource access, not trusted from caller-provided body fields.
- Notes remain private by default. Enterprise notes may be scoped to personal, team, unit, or organization only through explicit ownership/access model and policies.
- Define ownership and inheritance carefully: organization membership must not automatically expose all private notes.

## Suggested role model (baseline RBAC)

Organization roles:
- ORG_OWNER: full organization administration and emergency ownership transfer policy (protected, audited).
- ORG_ADMIN: manage org profile, units, teams, employees/memberships, policies, identity configuration; cannot arbitrarily access private note content unless separately granted.
- ORG_SECURITY_ADMIN: manage identity/security settings, agent policies, audit, revocation; content access not implied.
- ORG_AUDITOR: read audit/security posture and administrative metadata, not note contents by default.
- ORG_MEMBER: standard employee capabilities.

Unit roles:
- UNIT_MANAGER
- UNIT_MEMBER

Team roles:
- TEAM_LEAD
- TEAM_MEMBER

Role inheritance must be explicit and least-privilege: org admins can manage metadata and memberships but should not automatically read user-private note content. Team/unit content access is via grants, team-note ownership, or explicitly defined policy. Implement permission evaluation with deny-by-default and tests for cross-tenant isolation.

## Administration interface

Build a dedicated, role-protected admin area in the React application with navigation and server-side APIs. It must not be a client-side-only gate.

Admin modules:
1. Dashboard: active employees, org units, teams, identity provider status, registered agents, recent security/audit activity, failed sign-ins, token revocations, policy alerts.
2. Organization editor: organization metadata, hierarchy visualization, create/edit/archive units, validation and cycle prevention.
3. Employee directory: searchable/filterable directory, profile, status, external identity links, org/unit/team memberships, manager/report relationships; invite/provision and suspend/deactivate.
4. Team management: create teams, assign leads, manage members, move teams/units, team access and collaboration policy.
5. Role/access management: role assignments, scoped roles, permissions, effective-access view, privilege-change audit.
6. Identity settings: OIDC issuer/client metadata, allowed domains, mapping preview, required claims, provisioning mode; secrets shown once or referenced from secret store, never displayed again.
7. Agent management: register agent identities, status, capabilities, allowed tools, owner/delegator, token TTL ceilings, rate/usage budget, revoke agent, revoke active sessions/tokens, approvals.
8. Policy studio: configure allowed tools by agent/role/team, high-risk action approvals, data classification restrictions, maximum delegation, model/provider allowlist, budget and rate limits. Start with validated config and versioned policies, not an arbitrary expression editor.
9. Audit viewer: filter by actor, organization, agent, delegated user, action, resource, result, date, correlation/task ID; export CSV with admin permission.
10. Integrations: OIDC, model providers, Jev/System One decision provider health/config, MCP integration status, webhook secrets, test-connection controls.

Admin UX requirements:
- Consistent modern enterprise UI, responsive and keyboard accessible.
- Confirm dialogs for destructive/privileged changes.
- Clear empty/error/loading states.
- Pagination/filtering server-side for directory/audit.
- All mutations produce audit events.
- Admin UI calls only Admin application use cases; no direct DB access.
- Use optimistic concurrency for editing org/team/policy objects.
- Admin privilege must be distinct from content-access permission.

## OIDC implementation

Use standard OpenID Connect Authorization Code flow, with PKCE (S256) for browser clients. Do not implement cryptography manually.

Requirements:
- Discovery from issuer metadata; validate issuer and HTTPS in production.
- Validate signature via JWKS with key rotation/cache refresh.
- Validate iss, aud, exp, nbf, iat (reasonable clock skew), nonce, state, PKCE, and allowed signing algorithms.
- Authorization Code + PKCE; secure state/nonce correlation; CSRF protection.
- Prefer backend/BFF session using Secure, HttpOnly, SameSite cookies over storing access/refresh tokens in localStorage.
- If API bearer tokens are used, validate independently at API boundary.
- Logout/session revocation strategy; support short session idle/absolute lifetimes configurable.
- Support configurable issuer/client, redirect URIs, scopes, audience, claim mapping.
- External identity key is (issuer, subject), not email.
- Provisioning modes: JIT disabled by default for production; allow explicit controlled JIT or admin-approved provisioning.
- Claim mapping for display name, email, groups (never blindly translate IdP groups into privileged app roles).
- Account linking requires authenticated proof/administrator approval and collision-safe workflow.
- Demo identity mechanism remains dev-only and must never be a production fallback.
- Tests: malformed/expired/invalid signature, wrong issuer/audience, nonce/state mismatch, missing subject, disabled user, unknown external identity, key rotation, auth failure does not fall back to demo, secure cookie attributes.

## Agentic integration

### Agent identity and registry
AgentIdentity fields: id, display name, runtime/client identity, status, registered owner, created/rotated/revoked timestamps, allowed tools/capabilities, allowed models/provider policy, max token TTL, rate/budget limits, allowed organization scopes, risk tier.
Agents are first-class service principals, not employees. Never use an employee account as the agent's identity.

### Authentication and token issuance
Implement OAuth 2.0-style service identity and delegated authorization; prefer an Authorization Server / IdP-supported token exchange where possible. Do not mint a custom long-lived API key and call it OAuth.

Token requirements:
- Short-lived access tokens (default 5 minutes, configurable within policy ceiling).
- Narrow aud (Notes API or tool gateway).
- Least-privilege scopes/capabilities.
- Agent subject identity distinct from delegated human subject.
- Actor/delegation chain represented using a standard profile/claims where possible (document chosen profile; do not invent trust semantics).
- Include jti, issuer, audience, issued-at, expiry, task/correlation ID as appropriate.
- Delegated tokens must be attenuated: no scope/resource/action greater than agent policy or user's current authority.
- Do not allow token refresh without reauthorization; refresh/exchange re-evaluates current permissions and agent status.
- Token revocation/introspection strategy and rotation.
- Signing keys from managed secret/key store; key rotation, algorithm allowlist.
- Token never logged, persisted in browser, or returned in tool traces.
- Resource authorization rechecks current membership/permissions, so revocation takes effect promptly even if token remains valid.
- Use separate token audiences for agent gateway vs Notes API if boundary warrants it.

Support two modes:
A. Autonomous service agent: restricted service principal, no human delegation; allowed by explicit service-agent policy and organization scope.
B. Delegated agent: explicit user consent/task authorization, constrained by user permissions, agent permissions, organizational policy, and selected resources.

### Agent tool surface
Expose typed, versioned tools/capabilities via a tool adapter. Initial tools:
- search_notes
- get_note
- create_note
- update_note
- append_to_note
- list_shared_notes
- list_teams / get_team_context (metadata-only and permission-filtered)
- create_action_items (future separate domain)
Potential sensitive tools:
- share_note / revoke_share
- delete_note
- organization-wide searches
Require explicit approval/policy for sensitive actions. Do not expose raw generic CRUD or arbitrary SQL.

Tool calls must:
- Validate strict input schemas, reject extra properties, bound result size.
- Resolve actor and delegation from verified token, not model arguments.
- Apply policy, authorization, tenant/resource scope.
- Rate-limit and enforce per-task/tool-call budget.
- Add idempotency for mutation calls.
- Return minimal data, redact sensitive fields, and never disclose inaccessible resource existence.
- Record audit trail for successful and denied actions.
- Handle prompt injection in note contents as untrusted data; note text never becomes instructions to agent.
- Bound recursion/loops, timeouts, output tokens, and retries.
- Avoid executing arbitrary code, URLs, shell, or generated queries by default.

### Agent workflows
First showcase:
1. Ask: "Summarize project notes I have access to, identify recurring risks, and produce action items."
2. Agent classifies intent/risk.
3. Search uses permission-filtered Notes API.
4. Retrieval returns only authorized content.
5. Generative model summarizes with citations/links to source notes.
6. Jev checks structured quality/intent/relevance decisions.
7. Agent proposes action items; user reviews.
8. On approval, create a new note or update a designated note through the service layer.
9. Audit captures every tool invocation and approval.

Second showcase: "Prepare a team handoff"—retrieve team-approved accessible notes, summarize status/blockers/decisions, draft handoff note, ask user to approve write.

## System One / Jev decision integration

Create a vendor-neutral DecisionProvider port with typed request/result models. Add a Jev/System One adapter in infrastructure. Treat Jev as a structured judgment service, not a chat generator.

Potential evaluations (validate capabilities against Jev API docs during implementation):
- Intent/routing choice.
- Classification of requested operation.
- Risk/ambiguity score.
- Relevance scoring for retrieved note candidates.
- Completeness/quality check.
- Approval-needed recommendation (advisory only).
- Selection among known tool/workflow candidates.

Use confidence thresholds and abstention/fallback. Keep outputs structured and bounded. Decision results are advisory and never grant authorization. Application policy and authorization provider are authoritative. Store model/provider/version, input context hash (avoid retaining raw sensitive content by default), output, latency, confidence, and decision trace in telemetry/audit where safe. Provide stub/fake provider for tests and offline demo. Add feature flag and safe fallback behavior. Confirm Jev SDK/API specifics, supported judgment types, privacy/data retention, rate limits, and credential requirements before wiring vendor calls.

## Service-layer integration design

Add use cases/services:
- RegisterAgent / UpdateAgentPolicy / RevokeAgent
- IssueAgentToken / ExchangeDelegatedToken / RevokeToken
- ExecuteAgentTool (or separate use cases per capability)
- EvaluateDecision
- CreateApproval / ApproveAction / RejectAction
- GetAgentAudit / QueryAudit
- Organization CRUD/hierarchy use cases
- Employee provision/update/suspend/deprovision
- Team CRUD/membership/lead assignment
- Admin policy and identity-provider management

Use existing use cases (CreateNote, UpdateNote, SearchNotes, etc.) from agent tools. No duplicate note business logic.

## Data model and migrations

Introduce normalized tables/entities for:
- organizations
- organization_units (parent_id, type, name, slug, status; unique scoped constraints)
- teams (unit_id, optional parent_team_id, name, status)
- organization_memberships
- unit_memberships
- team_memberships
- manager_relationships (scope + validation; or manager fields in scoped membership if justified)
- agent_identities
- agent_tool_grants / capability policy
- delegated_authorizations / consent grants (scope, resource restrictions, expiry, revocation)
- token_sessions / revocation records (store identifiers/hashes, not bearer tokens)
- agent_tasks / task runs (status, limits, requester, agent, timestamps)
- agent_tool_invocations
- approval_requests
- audit_events (append-only)
- decision_evaluations (optional separate telemetry record with data minimization)
- policy_versions

Use UUID identifiers, UTC timestamps, FKs, indexes, uniqueness, status fields, soft-delete where history is needed, optimistic concurrency versions, and append-only audit semantics. Organization-aware resource queries must include tenant/org scope. Write migrations with safe downgrade where feasible.

## Audit and observability

Structured audit events for:
- sign-in and sign-out; OIDC failure/success; provisioning/linking
- employee/org/team membership changes
- admin role changes
- agent registration, configuration, revoke
- token issuance, exchange, revocation (never token values)
- every agent tool call and policy/auth decision
- approvals and execution results
- note sharing/revocation/deletion, including actor/agent attribution

Provide request_id, trace_id, task_id, actor_id, agent_id, delegated_user_id, org_id, operation, resource_id, result, reason_code, timestamp. Do not log note content, secrets, tokens, raw prompts, or unnecessary model inputs. Add dashboards/metrics for sign-in failures, tool latency, token exchanges, denials, approval queue, agent budget consumption, provider errors.

## Demonstration and seeded data

Seed an impressive, deterministic demo organization (e.g., "Bluestaq Demo Organization") with a hierarchy:
- Executive leadership
- Engineering
  - Platform team
  - AI & Data team
  - Application team
- Operations
  - Mission Operations team
  - Security team
- Product
  - Product Management team

Seed realistic demo employees, manager/report relationships, teams, team leads, scoped roles, and shared notes. Use clearly fictional names and avoid actual Bluestaq employee data. Seed demo agent identities:
- Notes Assistant (delegated, read/search/draft)
- Team Briefing Agent (team-scoped metadata/read + draft, approval before write)
- Organization Insights Agent (aggregate metadata only; no private note content)
Demo identity/OIDC flags must keep seed/demo identities out of production.

## Frontend roadmap

Add:
- organization switcher (if multi-org membership)
- employee directory with org chart/hierarchy views
- team detail pages (members, lead, shared context)
- admin console with dashboard navigation and role-aware routes
- agent center: agent list/status, capabilities, recent runs, approvals, token/session status (metadata only)
- AI assistant panel with task input, plan/progress, tool-call transparency, citations/source notes, approval cards, cancel/timeout, error handling
- admin policy and identity settings
- audit log explorer
- accessible components, server pagination, filtering, and responsive layout
- admin visibility should not imply content access

## Testing requirements

Add tests across layers:
- Domain: hierarchy constraints, cycles, membership status, scoped role rules.
- Application: org/team membership use cases, admin permission gates, agent registration/revocation, delegated token attenuation, approvals, decision fallback, task limits.
- Security: tenant isolation, agent cannot elevate, delegation intersection, revoked membership, token expired/revoked/wrong audience, forbidden tool, resource-level access, prompt-injection payload treated as data.
- API: OIDC flow callback/state/nonce/PKCE, cookie security, token exchange, tool input validation, idempotency, rate limits, audit events.
- Persistence: migration, FK, unique memberships, cascade/soft-delete behavior, concurrent changes.
- Frontend: admin route guards backed by API, org chart/directory, memberships, agent center, approval flows, accessibility, empty/error/loading.
- Integration: end-to-end demo workflow including user delegation, agent search, summary, approval, note creation, revoked access denied.

No tests should mock away the authorization decision being asserted. Include integration tests against real application services and repository/UoW.

## Build sequence / work packages

### WP0 — Baseline and architecture guard
- Run and record existing backend/frontend tests, typecheck, lint, build, smoke test.
- Map current modules, settings, migrations, auth, frontend routing/state and tests.
- Add expansion ADRs and preserve backward compatibility.

### WP1 — Organization foundation
- Organization, hierarchy, employees, memberships, teams, scoped roles.
- Domain validation, application use cases, repos, UoW, migration, admin APIs.
- Seed demo organization, employees, teams.
- API tests and isolation.

### WP2 — Admin backend and frontend
- Admin dashboard API, employee directory, hierarchy/team management, roles, audit viewer shell.
- Admin UX and server-side protection.
- Privileged-change auditing.

### WP3 — Production OIDC
- Authorization Code + PKCE, callback/session, secure cookie, JWKS cache/rotation, provisioning policies.
- Local/mock IdP integration tests.
- Keep demo auth dev-only.

### WP4 — Agent identity and token exchange
- Agent registry, autonomous/delegated modes, consent/authorization grants, short-lived tokens, scopes, revocation.
- Admin Agent Center.
- Security tests for authority attenuation.

### WP5 — Tool gateway and service-layer capabilities
- Versioned tool schemas, tool adapter, use-case invocation, rate/budget limits, audit.
- Initial notes/team metadata tools.
- MCP adapter only if it cleanly reuses contracts.

### WP6 — Jev/System One integration and orchestration
- DecisionProvider port, Jev adapter after API contract verification, fake/stub provider, feature flag.
- Agent workflow service, provider abstraction, fallbacks, evaluations.
- First workflows: project summary/action items and team handoff.

### WP7 — Approval workflows and polished agent UI
- Approval-required actions, cancel/retry/timeouts, transparent tool traces, source citations.
- Admin approval queue.

### WP8 — Hardening and release
- Threat model, tenant isolation review, dependency/security scan, migration test, Docker compose demo, docs, operational runbook, final test matrix.
- Build showcase script and seed data.
- Update README and architecture docs; publish feature branch PR for review, do not merge without owner approval.

## Acceptance criteria (release gate)

- Existing core notes behavior remains green.
- Organization hierarchy cannot be corrupted or cycled; employee/team relationships are tenant-scoped.
- Cross-tenant isolation verified for users, teams, notes, admins, and agents.
- Admin operations require server-side authorization and are audited.
- OIDC security checks and PKCE flow verified in automated tests; no live provider secrets in repo.
- Agent is a service principal, never impersonates a user.
- Delegated authority cannot exceed user, agent, policy, and scope intersection.
- Tokens short-lived and audience-limited; revoked agents/delegations lose access immediately or within documented enforcement bound.
- Agent tool calls invoke application use cases; no direct DB access.
- Sensitive/destructive actions require explicit approval by a separate authorized human, with no self-approval.
- Jev outputs are advisory, structured, observable, and safely fall back; never authorize.
- Audit events are append-only and exclude sensitive content/token values.
- Admin UI supports hierarchy, employee/team administration, identity, agents, policy, approvals, and audit.
- Demo can be run via documented single-command setup and deterministic seed data.
- Tests, lint, typecheck, frontend build, migration check, smoke test all run and results are recorded honestly.

## Demo narrative ("wow" path)

1. Sign in through a real OIDC provider (or clearly labeled local demo mode).
2. Show the fictional enterprise org chart and drill into a unit/team.
3. Open an employee profile and show team memberships/reporting line.
4. Switch to Admin Console and show scoped admin permissions.
5. Register or inspect Notes Assistant service identity and its restricted capabilities.
6. Start a delegated task: "Summarize platform team decisions, open risks, and action items from notes I can access."
7. Show plan and tool transparency; demonstrate Jev structured routing/quality judgment (use mock provider if real Jev credentials are unavailable and clearly label it).
8. Show short-lived delegated token metadata only (never reveal token value), scopes, audience, expiry, task binding.
9. Agent retrieves authorized notes only and returns a cited synthesis.
10. Agent drafts a new handoff note; human approval gate.
11. Approve; use case writes note. Audit explorer shows requester, agent, task, tool, approval, and resource.
12. Revoke the agent/delegation and demonstrate immediate denial.
13. Attempt a cross-team/cross-org request and demonstrate denied access without leaking resource existence.

## Interview positioning

Describe this as an architectural evolution and proof of engineering range, not as production-certified security. Be explicit about what is implemented, tested, simulated, and not yet connected to a live provider. Use a live Jev provider only after the integration contract, credentials, and policy are verified.
