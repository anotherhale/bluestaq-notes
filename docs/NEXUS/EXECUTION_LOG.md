# NEXUS Execution Log

Append-only engineering history. Newest run last. Every entry distinguishes
implementation from verification; a status is only claimed at the level the
evidence supports.

---

## Run NEX-2026-11-24-01

| Field | Value |
| --- | --- |
| Run ID | `NEX-2026-11-24-01` |
| Timestamp | 2026-11-24T04:00Z |
| Branch | `feature/agentic-enterprise-platform` |
| Starting commit | `e76e9c0` |
| Ending commit | see checkpoint |
| Phase | 1 — Organization & Identity foundation |

### Tasks completed

**NEX-006 — Tenant access enforcement (membership revocation takes effect
immediately).**

Resumed interrupted work. The prior run had written the domain rule, the shared
application seam, and the login-use-case tests but left the bearer-path test
failing: it instantiated `AuthenticationContext`, which is a `Protocol` and
cannot be constructed. A stale `serialization` import also failed `ruff`.

Completed this run:

1. Replaced the `Protocol` instantiation with a concrete `_BearerContext`
   carrying exactly the attributes `OidcIdentityProvider` reads, and dropped the
   unused import.
2. Fixed a real type defect surfaced by `mypy`: `resolve_tenant_access` /
   `ensure_tenant_access` passed a bare `str` to
   `OrganizationMembershipRepository.list_for_user`, whose port expects
   `UserId`. The helpers now accept `str | UserId` and normalise via
   `UserId.parse`, so all three call sites are typed correctly instead of the
   error being silenced.
3. Reconciled `STATE.md`, which had drifted (claimed 320 tests at base
   `2b54ca5`; reality was 409 tests at `e76e9c0`).

### Files changed

| File | Change |
| --- | --- |
| `src/notes_service/domain/organization/tenant_access.py` | new — pure domain rule |
| `src/notes_service/application/use_cases/tenant_access.py` | new — shared seam; typed `str \| UserId` |
| `src/notes_service/application/use_cases/authentication.py` | `CompleteLogin` + `ResolveSession` enforce access |
| `src/notes_service/infrastructure/identity/oidc_provider.py` | bearer path enforces access |
| `tests/domain/test_tenant_access.py` | new — 8 domain-rule tests |
| `tests/authentication/test_login_use_cases.py` | +tenant-access cases (28 total in file) |
| `tests/authentication/test_tenant_access_bearer.py` | new — 4 real-JWT bearer tests |
| `docs/NEXUS/*` | state reconciliation |

### Tests executed

| Gate | Result |
| --- | --- |
| `pytest` (full) | **409 passed, 4 skipped** |
| `pytest tests/architecture` | passed — layering invariant intact |
| `ruff format` | clean |
| `ruff check` | clean |
| `mypy` | clean, 82 source files |

### Security findings

- Positive: the three identity-establishing paths (OIDC callback, per-request
  session resolution, bearer token) now converge on one deterministic
  application-layer check. Enforcement is application code, never model output.
- Positive: the refusal message is uniform, so a caller cannot distinguish
  "no such account" from "deprovisioned" — no existence oracle.
- Deliberate: `UNAFFILIATED` (zero memberships) is allowed so a new identity can
  create its first organization; it grants no authority inside any existing
  tenant. Pinned by tests on both the domain and bearer paths.
- Noted, not a regression: access is evaluated per request with no caching by
  design (that is what makes revocation immediate). This costs one membership
  query per authenticated request; acceptable until profiling says otherwise.

### Decisions

- Keep the tenant rule in `domain/` as a pure function over memberships +
  organizations, so it is testable without a database and cannot drift from the
  application seam.
- One shared seam (`ensure_tenant_access`) rather than three copies. Three
  copies is how the paths drift apart and how a revocation silently stops
  applying to one of them.

### Risks

- **R1:** PR #3 is still a draft and its description predates OIDC and this
  slice; a reviewer would under-estimate what exists. (NEX-004.)
- **R3:** CI on this branch has not been observed for this commit set; local
  gates are green, which is not CI evidence.

### Next action

NEX-003 — agent identity and token exchange (delegated-token attenuation), which
this slice unblocks: an agent token will resolve to a principal through the same
bearer provider, so it inherits revocation-on-next-request for free.
