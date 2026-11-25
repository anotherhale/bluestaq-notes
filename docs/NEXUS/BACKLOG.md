# NEXUS Backlog

Prioritized, dependency-ready implementation backlog. Priority tiers follow the
NEXUS directive: P0 critical, P1 foundational, P2 agent platform, P3 enterprise
capabilities, P4 enhancements.

Statuses are evidence-based: `TODO`, `STARTED`, `IMPLEMENTED`, `TESTED`,
`VERIFIED`, `COMMITTED`, `PUSHED`, `BLOCKED`.

---

## NEX-001 — Organization hierarchy vertical slice

| Field | Value |
| --- | --- |
| Phase | 1 |
| Priority | P1 |
| Status | **VERIFIED / COMMITTED** |
| Dependencies | none |

**Objective:** organizations, units, teams, memberships, and managers with
enforced authorization.

**Architecture refs:** `ENTERPRISE_AGENTIC_EXPANSION_SPEC.md` (org hierarchy),
`AUTHORIZATION_DOMAIN_MODEL.md`, ADR-008 (404 vs 403).

**Acceptance criteria**

- An organization can be created, read, updated.
- Units form a tree with cycle prevention.
- Teams belong to a unit and carry members.
- A member can be suspended, which removes organization *and* team authority.
- The last administrator cannot be removed or demoted.
- A non-administrator cannot escalate its own role.
- Soft-deleted memberships never appear in rosters.

**Required tests:** domain unit tests, application use-case tests, security
tests for escalation and last-admin guards, API tests, migration test.

**Security considerations:** default-deny; 404 for inaccessible, 403 for
insufficient permission; no identity from request body.

**Complexity:** medium. **Outcome:** all criteria verified — 320 passed, 4
skipped.

---

## NEX-002 — OIDC authentication

| Field | Value |
| --- | --- |
| Phase | 1 |
| Priority | P1 |
| Status | TODO |
| Dependencies | NEX-001 |

**Objective:** replace demo-only authentication with a real OIDC authorization-
code flow (PKCE) that binds a verified external subject to an organization
membership.

**Scope:** authorization request construction with `state` + `nonce` + PKCE
`code_verifier`; callback validation (signature, issuer, audience, expiry,
nonce); session establishment with `HttpOnly`/`Secure`/`SameSite` cookie;
subject→membership resolution; explicit failure paths that deny access.

**Acceptance criteria**

- A tampered or replayed `state`/`nonce` is rejected.
- An ID token with wrong issuer/audience/expiry is rejected.
- A valid login yields a session whose subject maps to exactly one active
  membership; no membership denies access.
- Demo auth remains available only in development and cannot activate in
  production.

**Required tests:** state/nonce replay rejection, token validation failures,
session cookie flags, subject→membership mapping, production demo-auth refusal.

**Security considerations:** no token in URL after callback; no secrets logged;
fail closed on any validation error.

**Complexity:** large. **Blocks:** NEX-003, all agent identity work.

**Outcome (NEX-2026-11-24-01):** IMPLEMENTED — authorization-code + PKCE with
state/nonce, JWKS-validated ID tokens, session cookies, and subject→user linkage
via `external_identities` (never auto-provisions). Covered by
`tests/authentication/{test_login_use_cases,test_identity_provider,test_login_router,test_session_domain}.py`.

---

## NEX-006 — Tenant access enforcement

| Field | Value |
| --- | --- |
| Phase | 1 |
| Priority | P1 |
| Status | **TESTED** (local gates green) |
| Dependencies | NEX-002 |

**Objective:** close the offboarding gap. Authentication proves identity; it must
not, on its own, keep granting access after the caller's membership is suspended
or deprovisioned. Revocation must take effect on the next request, not at session
expiry.

**Scope:** a pure domain rule over the caller's memberships and their
organizations; one shared application seam; enforcement on all three
identity-establishing paths (OIDC callback, per-request session resolution,
bearer tokens).

**Acceptance criteria**

- An active membership in an operational organization grants access.
- A suspended or deprovisioned membership denies access.
- A membership in an archived/suspended organization denies access, so
  suspending a tenant revokes access for everyone in it at once.
- A user with several tenants keeps access while *any* one still grants it.
- A user with no memberships is `UNAFFILIATED` and allowed through, so a new
  identity can create its first organization — granting nothing in any existing
  tenant.
- A revoked membership is rejected on the bearer path, not only on the session
  path.
- The refusal message is uniform (no account-existence oracle).

**Required tests:** domain rule branch coverage; login use-case tenant cases;
bearer path with a real signed RS256 JWT against a real unit of work.

**Security considerations:** deterministic application-layer enforcement, never
model output. Default-deny. No caching, by design — that is what makes revocation
immediate.

**Complexity:** medium. **Outcome:** `pytest` 409 passed / 4 skipped; ruff and
mypy clean. Detail in `EXECUTION_LOG.md`.

---

## NEX-003 — Agent identity and token exchange

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P1 |
| Status | **TESTED** (local gates green); pending commit in this run |
| Dependencies | NEX-002 |

**Objective:** make agents first-class actors whose authority is derived by
attenuation from a human or service delegator.

**Acceptance criteria**

- An agent principal is registered with an owner and a capability set.
- Token exchange issues a token whose scopes are a strict subset of the
  delegator's effective authority; a request exceeding it is denied.
- Delegation chains cannot amplify authority at any hop.
- Revocation invalidates derived tokens immediately.
- An agent cannot approve its own sensitive action.

**Required tests:** attenuation property (child ⊆ parent) as a property-style
test over chains; over-broad request denial; revocation immediacy; self-approval
denial.

**Security considerations:** this is the platform's central security boundary.
Enforcement must be deterministic application code, never model output.

**Complexity:** large.

**Outcome (NEX-2026-11-24-01):** IMPLEMENTED and TESTED.

- **Domain** — `domain/agent/{entities,value_objects,delegation,token}.py`.
  `DelegatedAuthorization` is the source of authority; `AgentToken` is a derived
  credential stored as a SHA-256 hash only (no raw secret column). Attenuation is
  a pure function that refuses to grow authority at any hop.
- **Application** — `use_cases/agents.py`: register/list/get, grant/revoke
  delegation, issue token, `ResolveAgentToken`, approval request open/decide/list.
  `ResolveAgentToken.run()` re-checks token expiry, delegation revocation, and
  agent suspension on every call, so revocation takes effect on the next request.
- **Persistence** — migration `c3f1a9d47e02` (`agent_identity_delegations_tokens`),
  verified to apply on a fresh database and to round-trip through downgrade.
  `ON DELETE CASCADE` from both `agents` and `agent_delegations` confirmed
  empirically.
- **API** — `presentation/api/routers/agents.py`; `POST /tokens/resolve` requires
  an authenticated human principal (unauthenticated introspection answers 401),
  so an agent cannot introspect its own token.
- **Tests** — `tests/agent/{test_agent_identity,test_delegation}.py` and
  `tests/api/test_agents_api.py`. Attenuation is covered by **exhaustive
  enumeration** of every capability subset and every parent/child pair rather
  than by a `hypothesis` property test: `hypothesis` is not a project dependency
  and this run did not add one. Enumeration over the delegable universe is
  strictly stronger than sampling for this invariant.

**Defect found and fixed during validation (this run):** `POST /tokens/resolve`
returned `AgentResponse`, which includes the agent's full capability *ceiling*
plus `max_token_ttl_seconds`, `version`, and timestamps — strictly more authority
than the token under inspection carried, disclosed to any token holder. The
schema's own docstring states `AgentResponse` is "only ever returned to the
owner". Introspection now returns the narrower `ResolvedAgentIdentity`
(`id`, `display_name`, `runtime_identity`, `status`) and the token's own
capabilities. Pinned by two regression tests:
`test_introspection_is_refused_without_an_authenticated_caller` and
`test_introspection_never_discloses_the_agents_capability_ceiling`.

---

## NEX-004 — Refresh PR #3 and tracking issue #2

| Field | Value |
| --- | --- |
| Phase | n/a (delivery hygiene) |
| Priority | P4 |
| Status | VERIFIED |
| Dependencies | none |

**Objective:** make GitHub reflect verified reality.

**Acceptance criteria:** PR #3 description states what is implemented versus
planned, lists validation evidence, and carries an accurate checklist; issue #2
shows current phase and next priorities. PR remains a draft until validation is
complete and human review is requested.

**Evidence (2026-11-25):** PR #3 description rewritten with a phase status
table, the two-defect write-up, a validation table, and the open risks; progress
comment 7149894984 posted to issue #2. PR #3 stays a **draft** and unmerged —
automated merging is prohibited.

---

## NEX-005 — Tool gateway

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P2 |
| Status | VERIFIED |
| Dependencies | NEX-003 |

**Objective:** a single governed path for agent tool invocation with schema
validation, authorization checks, audit records, and idempotency keys.

**Acceptance criteria:** unknown tools denied; arguments validated against a
declared schema; every invocation audited with actor, capability, and outcome;
repeated idempotency key does not duplicate the side effect.

**Complexity:** large.

---

## NEX-005-followup — Tool gateway focused tests

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P2 |
| Status | VERIFIED |
| Dependencies | NEX-005 |

**Objective:** prove the gateway's idempotency and audit invariants directly
rather than inferring them from a green full suite.

**Acceptance criteria:** replay with the same idempotency key returns the
recorded outcome without re-executing; a denied invocation is audited as denied
and cannot be replayed into an execution; a revoked delegation refuses rather
than erroring; `arguments_digest` is stable and raw arguments are never stored.

**Outcome:** `tests/agent/test_tool_gateway.py`, 18 tests, all passing. Found
two real defects (retried denial raising `IntegrityError`; revoked delegation
surfacing `InvariantViolation` as a 500). Both fixed in `74d3d98`.

**Complexity:** small.

---

## NEX-005-HTTP — Expose the tool gateway over HTTP

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P2 |
| Status | TODO |
| Dependencies | NEX-005 |

**Objective:** make the gateway reachable through the API layer, so agents can
invoke tools over HTTP under the same governed path.

**Acceptance criteria:** a router exposes tool invocation; the auth dependency
resolves an agent token for `TokenAudience.TOOL_GATEWAY` and rejects tokens
minted for the Notes API; a denied invocation returns a refusal status, not a
500; an API contract test covers the deny path and a successful invocation.

**Security:** the caller identity must come from the token, never the body.
Token audience separation is load-bearing — a Notes API token must not invoke
tools.

**Complexity:** medium. Closes risk R5.

---

## NEX-006 — Durable agentic runtime

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P2 |
| Status | TODO |
| Dependencies | NEX-005 |

**Objective:** durable runs that checkpoint progress and resume after
interruption without repeating completed side effects.

**Acceptance criteria:** a run interrupted mid-step resumes from its last
checkpoint; completed steps are not re-executed; run state is queryable.

**Complexity:** large.
