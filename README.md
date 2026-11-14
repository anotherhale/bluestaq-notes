# Bluestaq Notes Service

A secure, multi-user REST backend for a note-taking service shared across small
teams. Notes are **private by default**; an owner may share a note with other
users at `read` or `write` level, and only the owner may delete or manage
sharing.

This is a small, deliberately well-engineered service. It favours clear
architecture, correct security behaviour, and honest documentation over feature
breadth.

- **Stack:** Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.x · Alembic ·
  SQLite (default) / PostgreSQL (alternative)
- **Architecture:** modular monolith, hexagonal (ports & adapters), DDD-aligned
- **Docs:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
  [`docs/DOMAIN_MODEL.md`](docs/DOMAIN_MODEL.md),
  [`docs/SECURITY.md`](docs/SECURITY.md), [`docs/API.md`](docs/API.md),
  [`docs/ADR/`](docs/ADR/), [`OPENHANDS_SPEC.md`](OPENHANDS_SPEC.md),
  [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)

---

## 1. Project overview

The service exposes the following endpoints under `/api/v1`:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/me` | Resolve the authenticated caller |
| `GET` | `/api/v1/users` | List users available for sharing |
| `GET` | `/api/v1/notes` | List notes owned by or shared with the caller |
| `POST` | `/api/v1/notes` | Create a private note |
| `GET` | `/api/v1/notes/{note_id}` | Retrieve a note |
| `PUT` | `/api/v1/notes/{note_id}` | Update a note (optimistic concurrency) |
| `DELETE` | `/api/v1/notes/{note_id}` | Delete a note (owner only) |
| `GET` | `/api/v1/notes/{note_id}/permissions` | List collaborators (owner only) |
| `POST` | `/api/v1/notes/{note_id}/permissions` | Grant/upgrade access (owner only) |
| `DELETE` | `/api/v1/notes/{note_id}/permissions/{user_id}` | Revoke access (owner only) |

Plus unauthenticated `GET /health` and `GET /readiness` probes.

Full request/response details and status codes: [`docs/API.md`](docs/API.md).

## 2. Architecture summary

Five layers with a strict, one-way dependency direction:

```
presentation ─┐
              ├─→ application ─→ domain
bootstrap ────┘        ↑
  (composition root)   │ implements ports
infrastructure ────────┘
```

- **domain** — entities, value objects, invariants, domain errors. No framework
  imports.
- **application** — use cases and *ports* (Protocols for repositories, unit of
  work, identity, authorization, rate limiting, clock).
- **infrastructure** — SQLAlchemy repositories/UoW, identity providers, rate
  limiter, clock.
- **presentation** — FastAPI routers, Pydantic schemas, middleware, error
  translation.
- **bootstrap** — settings and the composition root (`build_container`), the
  only module that wires ports to concrete adapters.

The domain and application layers import no FastAPI, SQLAlchemy, JWT, or HTTP
types. Boundaries are enforced by `tests/architecture/test_layering.py`. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and
[ADR-001](docs/ADR/ADR-001-modular-monolith-hexagonal.md).

## 3. Technology choices and rationale

| Choice | Why |
|---|---|
| **Python 3.12 + FastAPI** | Type-first, async-capable, first-class OpenAPI; large ecosystem. |
| **Pydantic v2** | Request/response validation and settings, with `extra="forbid"`. |
| **SQLAlchemy 2.x** | One model for SQLite and PostgreSQL; typed ORM; migrations via Alembic. |
| **Alembic** | Explicit, reviewable schema migrations; drift detection with `alembic check`. |
| **SQLite default** | Zero-setup for a reviewer; `DATABASE_URL` alone switches to PostgreSQL. |
| **PyJWT + cryptography** | OIDC validation without hand-rolling crypto. |
| **pytest + httpx** | Behavioural tests through the real ASGI app. |
| **Ruff + mypy (strict)** | Fast lint/format and real static typing. |

Dependencies are intentionally few. No message broker, cache, container
orchestrator, or service mesh is used, because nothing in the requirements needs
one.

## 4. Install dependencies

Requires Python 3.12.

```bash
# With uv (recommended; creates .venv and installs dev deps)
uv sync --extra dev

# Or with plain venv + pip
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

All commands below assume the virtualenv is active (`.venv/bin/...` works too).

## 5. Configure the application

Copy the example file and adjust as needed:

```bash
cp .env.example .env
```

Key variables (full list in [`.env.example`](.env.example)):

| Variable | Default | Notes |
|---|---|---|
| `ENVIRONMENT` | `development` | `development` \| `testing` \| `production` |
| `DATABASE_PROVIDER` | `sqlite` | `sqlite` \| `postgresql` |
| `DATABASE_URL` | `sqlite:///./notes.db` | must match the provider |
| `AUTH_DEMO_ENABLED` | `true` | dev-only; **must be false in production** |
| `AUTH_OIDC_ENABLED` | `false` | enable bearer-JWT auth |
| `CORS_ALLOW_ORIGINS` | *(empty)* | comma-separated; required, non-wildcard, in production |
| `RATE_LIMIT_ENABLED` | `true` | `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` |
| `MAX_REQUEST_BODY_BYTES` | `262144` | bodies above this get `413` |
| `LOG_JSON` / `LOG_LEVEL` | `true` / `INFO` | structured logging |

Configuration is validated at startup; unsafe combinations (e.g. demo auth in
production, wildcard CORS in production) fail fast.

## 6. Initialize the database

For a quick local start you can let the app create the schema directly:

```bash
notes-service init-db --seed     # creates tables (SQLite) and seeds demo users
```

For a real setup, prefer migrations (next section) and seed separately.

## 7. Run migrations

```bash
alembic upgrade head      # apply migrations
alembic check             # verify the model matches the migrations (no drift)
alembic downgrade base    # roll everything back (if needed)
```

Migrations work identically for SQLite and PostgreSQL; the single initial
revision creates `users`, `external_identities`, `notes`, and
`note_permissions` with UUID keys, foreign keys, uniqueness constraints, and the
required indexes.

## 8. Start the service

```bash
# Using the CLI
notes-service serve --host 127.0.0.1 --port 8000

# Or with uvicorn directly
uvicorn notes_service.bootstrap.app:create_app --factory --port 8000
```

Then:

- API docs (Swagger UI): <http://127.0.0.1:8000/docs>
- OpenAPI schema: <http://127.0.0.1:8000/openapi.json>
- Health: <http://127.0.0.1:8000/health>

## 9. Run tests

```bash
pytest                       # full suite (SQLite; PostgreSQL tests skipped)
pytest -q                    # quiet
pytest tests/security        # the mandatory authorization scenarios
```

PostgreSQL integration tests (skipped unless a database is provided):

```bash
createdb notes_test
TEST_POSTGRESQL_URL="postgresql+psycopg://user:pass@localhost:5432/notes_test" \
  pytest -m postgresql
```

Lint, format, and types:

```bash
ruff check .
ruff format --check .
mypy
```

### Test suite (executed)

| Suite | File | Tests |
|---|---|---|
| Domain — notes | `tests/domain/test_note.py` | 25 |
| Domain — identity | `tests/domain/test_identity.py` | 15 |
| Application — use cases | `tests/application/test_use_cases.py` | 20 |
| Application — transactions | `tests/application/test_transactions.py` | 1 |
| Authentication | `tests/authentication/test_identity_provider.py` | 19 |
| Security / authorization | `tests/security/test_authorization.py` | 18 |
| Persistence (contract, UoW, constraints) | `tests/persistence/test_repositories.py` | 21 |
| API (validation, codes, pagination, concurrency, rate limit) | `tests/api/test_api.py` | 37 |
| Architecture (layering) | `tests/architecture/test_layering.py` | 5 |

**Result (SQLite): `157 passed, 4 skipped`** — the 4 skipped are the PostgreSQL
integration tests. **With `TEST_POSTGRESQL_URL` set: the 4 PostgreSQL tests
pass.** (161 tests collected.)

## 10. Use the REST API

Authentication in development uses the `X-Demo-User-ID` header (see §11). All
examples below assume the service is running on `127.0.0.1:8000`.

```bash
BASE=http://127.0.0.1:8000/api/v1
ALICE=11111111-1111-1111-1111-111111111111
BOB=22222222-2222-2222-2222-222222222222

# Resolve the current user
curl -s -H "X-Demo-User-ID: $ALICE" $BASE/me

# List users available for sharing
curl -s -H "X-Demo-User-ID: $ALICE" $BASE/users
```

## 11. Demo authentication

Demo authentication exists to exercise multi-user behaviour locally and in
tests. It is **not production authentication**.

- Select a seeded user with the header `X-Demo-User-ID: <uuid>`.
- The id is validated against the database; unknown or inactive users are
  rejected with `401`. The provider never mints identities.
- It is disabled in production at two independent points (settings validation
  and provider construction).

Seeded demo users (created by `notes-service init-db --seed` or
`notes-service seed`):

| Name | `X-Demo-User-ID` | Status |
|---|---|---|
| Alice Anderson | `11111111-1111-1111-1111-111111111111` | active |
| Bob Baker | `22222222-2222-2222-2222-222222222222` | active |
| Carol Chen | `33333333-3333-3333-3333-333333333333` | active |
| Dave Diaz | `44444444-4444-4444-4444-444444444444` | suspended (cannot authenticate) |

For production, disable demo auth and enable OIDC
(`AUTH_OIDC_ENABLED=true`, `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`) and
send `Authorization: Bearer <jwt>`.

## 12. Example multi-user workflow

A complete walkthrough demonstrating ownership, READ sharing, the READ→WRITE
upgrade, and revocation. Copy-paste runnable against a freshly seeded instance.

```bash
BASE=http://127.0.0.1:8000/api/v1
ALICE=11111111-1111-1111-1111-111111111111
BOB=22222222-2222-2222-2222-222222222222
as_alice() { curl -s -H "X-Demo-User-ID: $ALICE" "$@"; }
as_bob()   { curl -s -H "X-Demo-User-ID: $BOB"   "$@"; }

# 1. Alice creates a private note.
NOTE=$(as_alice -X POST -H 'Content-Type: application/json' \
  -d '{"title":"Q4 planning","content":"draft"}' $BASE/notes)
echo "$NOTE"
NID=$(printf '%s' "$NOTE" | python -c 'import sys,json;print(json.load(sys.stdin)["id"])')

# 2. Alice reads it back (200).
as_alice $BASE/notes/$NID; echo

# 3. Bob cannot see Alice's private note -> 404 (existence is not leaked).
echo "Bob GET (expect 404): $(as_bob -o /dev/null -w '%{http_code}' $BASE/notes/$NID)"

# 4. Alice shares it with Bob as READ.
as_alice -X POST -H 'Content-Type: application/json' \
  -d "{\"user_id\":\"$BOB\",\"permission\":\"read\"}" $BASE/notes/$NID/permissions; echo

# 5. Bob can now read it (200).
echo "Bob GET (expect 200): $(as_bob -o /dev/null -w '%{http_code}' $BASE/notes/$NID)"

# 6. But Bob cannot modify it with READ -> 403.
echo "Bob PUT w/ READ (expect 403): $(as_bob -o /dev/null -w '%{http_code}' -X PUT \
  -H 'Content-Type: application/json' -d '{"title":"hacked","content":"","version":1}' $BASE/notes/$NID)"

# 7. Alice upgrades Bob to WRITE.
as_alice -X POST -H 'Content-Type: application/json' \
  -d "{\"user_id\":\"$BOB\",\"permission\":\"write\"}" $BASE/notes/$NID/permissions; echo

# 8. Now Bob can update it (200), version advances to 2.
as_bob -X PUT -H 'Content-Type: application/json' \
  -d '{"title":"Q4 planning (Bob edit)","content":"updated","version":1}' $BASE/notes/$NID; echo

# 9. Bob still cannot delete it (owner only) -> 403.
echo "Bob DELETE (expect 403): $(as_bob -o /dev/null -w '%{http_code}' -X DELETE $BASE/notes/$NID)"

# 10. Alice revokes Bob's access.
echo "Alice revoke (expect 204): $(as_alice -o /dev/null -w '%{http_code}' -X DELETE $BASE/notes/$NID/permissions/$BOB)"

# 11. Bob is denied immediately -> 404.
echo "Bob GET after revoke (expect 404): $(as_bob -o /dev/null -w '%{http_code}' $BASE/notes/$NID)"

# 12. A stale update from Alice (version 1, now version 2) -> 409.
echo "Stale PUT (expect 409): $(as_alice -o /dev/null -w '%{http_code}' -X PUT \
  -H 'Content-Type: application/json' -d '{"title":"x","content":"","version":1}' $BASE/notes/$NID)"
```

> Note: step 12's note version is 2 after Bob's edit. If you re-run the block,
> use the current version from a fresh `GET`.

## 13. Security decisions

Detailed in [`docs/SECURITY.md`](docs/SECURITY.md); summary:

- **Authentication** is a separate, pluggable boundary. Demo auth is dev-only
  and refused in production; OIDC validates signature/issuer/audience/expiry/
  claims and never falls back to demo on failure.
- **Authorization** is centralized, deny-by-default, and enforced on every
  protected operation. Ownership comes only from the authenticated principal —
  never from a request body.
- **Inaccessible resources return `404`**, not `403`, to prevent enumerating
  other users' notes by id (a caller who can read but lacks a specific
  permission gets `403`).
- **Input handling:** Pydantic `extra="forbid"`, field length limits, UUID
  validation, and a request body-size limit (`413`).
- **HTTP:** explicit CORS allowlist, security headers on every response,
  request correlation ids, a single error envelope, and configurable rate
  limiting (`429` with `Retry-After`).
- **Logging:** structured, with request ids; never logs secrets, tokens, or note
  content.
- **Config:** environment-driven and validated at startup; production rejects
  insecure demo auth and wildcard CORS. No secrets are committed.

## 14. Concurrency strategy

Optimistic concurrency control with a monotonic `version` on each note.

- A note starts at version `1`; every accepted content update increments it.
- `PUT /notes/{id}` requires the `version` the client last read.
- The repository performs an atomic
  `UPDATE ... WHERE id = :id AND version = :expected`; if no row matches, the
  write is rejected with **`409 Conflict`**.
- Stale writes never silently overwrite newer data.

See [ADR-007](docs/ADR/ADR-007-optimistic-concurrency.md).

## 15. SQLite and PostgreSQL configuration

**SQLite (default, zero setup):**

```bash
DATABASE_PROVIDER=sqlite
DATABASE_URL=sqlite:///./notes.db
alembic upgrade head
notes-service seed
notes-service serve
```

**PostgreSQL:**

```bash
DATABASE_PROVIDER=postgresql
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/notes
alembic upgrade head
notes-service seed
notes-service serve
```

The same models, repositories, and migration serve both providers. Timestamps
are normalised to timezone-aware UTC on both (see
[ADR-006](docs/ADR/ADR-006-sqlite-default-postgresql.md)).

## 16. Known limitations

- **Rate limiting is process-local.** The in-memory adapter does not coordinate
  across workers or replicas and resets on restart. Documented in
  [`docs/SECURITY.md`](docs/SECURITY.md); swap in a shared store behind the same
  port for production.
- **`/readiness` does not ping the database** (intentionally cheap). A deeper
  check can be added behind the same endpoint.
- **Request body limit** relies on `Content-Length`; chunked bodies without it
  are bounded by the ASGI server/proxy, not this middleware.
- **No live-IdP OIDC handshake was exercised** in this environment; the
  validator is implemented and unit-tested with locally generated keys.
- **OIDC users are not auto-provisioned**; an unknown `sub` is rejected by
  design.

## 17. Deliberately deferred capabilities

- Full end-to-end OIDC against a live provider (relying-party validation is
  implemented; the live handshake is deferred).
- Distributed rate limiting (Redis/gateway).
- Metrics/tracing and an audit-log sink.
- Soft-delete/trash and note history beyond the single `version` counter.
- Auto-provisioning of OIDC users.

Each is an extension point behind an existing port rather than a fake.

## 18. Production hardening considerations

- Set `ENVIRONMENT=production`, `AUTH_DEMO_ENABLED=false`, `DEBUG=false`, and an
  explicit non-wildcard `CORS_ALLOW_ORIGINS` (startup fails otherwise).
- Enable OIDC (`AUTH_OIDC_ENABLED=true` with issuer/audience/JWKS) and terminate
  TLS at the edge (HSTS is already sent).
- Point `DATABASE_URL` at PostgreSQL and run `alembic upgrade head` in the
  deploy pipeline.
- Replace the in-memory rate limiter with a shared store or a gateway limit.
- Ship logs to a central sink; the JSON formatter and request ids are already
  in place.
- Manage secrets via your platform's secret store; nothing is read from
  committed files.
- Run behind a reverse proxy that also enforces request-size limits.

---

## Project layout

```
src/notes_service/
├── domain/          # entities, value objects, invariants (no framework deps)
├── application/     # use cases + ports (Protocols)
├── infrastructure/  # SQLAlchemy, identity providers, rate limiter, clock
├── presentation/    # FastAPI routers, schemas, middleware, error handlers
├── bootstrap/       # settings, composition root, app factory, logging
└── cli.py           # serve / init-db / seed
migrations/          # Alembic
tests/               # domain, application, authentication, security, persistence, api, architecture
docs/                # ARCHITECTURE, DOMAIN_MODEL, SECURITY, API, ADR/
```

## License

MIT.
