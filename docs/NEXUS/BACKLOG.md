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

---

## NEX-003 — Agent identity and token exchange

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P1 |
| Status | TODO |
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

---

## NEX-004 — Refresh PR #3 and tracking issue #2

| Field | Value |
| --- | --- |
| Phase | n/a (delivery hygiene) |
| Priority | P4 |
| Status | TODO |
| Dependencies | none |

**Objective:** make GitHub reflect verified reality.

**Acceptance criteria:** PR #3 description states what is implemented versus
planned, lists validation evidence, and carries an accurate checklist; issue #2
shows current phase and next priorities. PR remains a draft until validation is
complete and human review is requested.

---

## NEX-005 — Tool gateway

| Field | Value |
| --- | --- |
| Phase | 2 |
| Priority | P2 |
| Status | TODO |
| Dependencies | NEX-003 |

**Objective:** a single governed path for agent tool invocation with schema
validation, authorization checks, audit records, and idempotency keys.

**Acceptance criteria:** unknown tools denied; arguments validated against a
declared schema; every invocation audited with actor, capability, and outcome;
repeated idempotency key does not duplicate the side effect.

**Complexity:** large.

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
