# Bluestaq Notes Service

A secure, multi-user REST backend for a note-taking service shared across small
teams, with a Google Drive-inspired React workspace. Notes are **private by
default**; an owner may organise notes into folders, star them, and share a note
with other users at `read` or `write` level. Only the owner may delete, move, or
manage sharing.

This is a small, deliberately well-engineered service. It favours clear
architecture, correct security behaviour, and honest documentation over feature
breadth.

- **Stack:** Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.x · Alembic ·
  SQLite (default) / PostgreSQL (alternative); React · TypeScript · Vite ·
  TipTap
- **Architecture:** modular monolith, hexagonal (ports & adapters), DDD-aligned
- **Docs:** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
  [`docs/DOMAIN_MODEL.md`](docs/DOMAIN_MODEL.md),
  [`docs/SECURITY.md`](docs/SECURITY.md), [`docs/API.md`](docs/API.md),
  [`docs/FRONTEND_INTEGRATION.md`](docs/FRONTEND_INTEGRATION.md),
  [`docs/ADR/`](docs/ADR/), [`OPENHANDS_SPEC.md`](OPENHANDS_SPEC.md),
  [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)

---

## 0. Quickest start — Docker Compose

The whole stack (PostgreSQL, the API, and the web client) starts with a single
command. No Python, Node, or database installation is required — only Docker.

```bash
docker compose up --build
```

Then open:

| Service | URL | Notes |
|---|---|---|
| Web client | http://localhost:8080 | The React workspace, talking to the API same-origin |
| API docs (Swagger UI) | http://localhost:8000/docs | OpenAPI is at `/openapi.json` |
| Health / readiness | http://localhost:8080/health · `/readiness` | Proxied through the web tier too |

On startup the API container waits for PostgreSQL, applies Alembic migrations,
seeds the demo users, and only then serves traffic — so the stack is usable the
moment `up` finishes. To stop it:

```bash
docker compose down          # stop, keep the database volume
docker compose down -v       # stop and delete all data
```

Everything is configurable via a `.env` file next to `docker-compose.yml`
(copy [`.env.docker.example`](.env.docker.example)). Sensible defaults mean no
`.env` is needed to run. See [§19 Docker](#19-docker) for details.

---

## 1. Project overview

The service exposes the following endpoints under `/api/v1`:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/me` | Resolve the authenticated caller |
| `GET` | `/api/v1/users` | List users available for sharing |
| `GET` | `/api/v1/demo/identities` | Discover demo identities (unauthenticated; `404` when disabled) |
| `GET` | `/api/v1/notes` | List/filter/sort notes (see §10) |
| `POST` | `/api/v1/notes` | Create a private note |
| `GET` | `/api/v1/notes/{note_id}` | Retrieve a note |
| `PUT` | `/api/v1/notes/{note_id}` | Update a note (optimistic concurrency) |
| `DELETE` | `/api/v1/notes/{note_id}` | Delete a note (owner only) |
| `PATCH` | `/api/v1/notes/{note_id}/folder` | Move a note to a folder / the root (owner only) |
| `PUT` | `/api/v1/notes/{note_id}/star` | Star a note for the caller |
| `DELETE` | `/api/v1/notes/{note_id}/star` | Unstar a note for the caller |
| `GET` | `/api/v1/folders` | List the caller's folders |
| `POST` | `/api/v1/folders` | Create a folder |
| `PATCH` | `/api/v1/folders/{folder_id}` | Rename a folder (owner only) |
| `DELETE` | `/api/v1/folders/{folder_id}` | Delete a folder (owner only; notes move to root) |
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
| Domain — folders & rich text | `tests/domain/test_folder_and_rich_text.py` | 12 |
| Domain — identity | `tests/domain/test_identity.py` | 11 |
| Application — use cases | `tests/application/test_use_cases.py` | 20 |
| Application — transactions | `tests/application/test_transactions.py` | 1 |
| Authentication | `tests/authentication/test_identity_provider.py` | 19 |
| Security / authorization | `tests/security/test_authorization.py` | 18 |
| Persistence (contract, UoW, constraints) | `tests/persistence/test_repositories.py` | 21 |
| API (validation, codes, pagination, concurrency, rate limit) | `tests/api/test_api.py` | 41 |
| API — folders, stars, search, move | `tests/api/test_folders_stars_search.py` | 27 |
| Architecture (layering) | `tests/architecture/test_layering.py` | 4 |

**Result (SQLite): `205 passed, 4 skipped`** — the 4 skipped are the PostgreSQL
integration tests. **With `TEST_POSTGRESQL_URL` set: the 4 PostgreSQL tests
pass.**

### Frontend tests

```bash
cd frontend
npm test            # vitest, 125 tests (component + hook + client + board)
npm run typecheck   # tsc -b --force
npm run lint        # eslint
npm run build       # tsc -b && vite build
```

The component tests drive the real `App` against an in-memory `FakeApiClient`
that mirrors the backend's authorization rules (folders, stars, search, sharing,
READ/WRITE, revocation, optimistic concurrency). The whiteboard adds unit tests
for the pure geometry and the persistence store, hook tests for board state and
per-user isolation, component tests for board management and card controls, and a
**DEF-03 regression test** that performs a real drag from the tray onto the canvas
through dnd-kit's pointer pipeline (see `docs/WHITEBOARD_DESIGN.md`).

| Suite | File | Tests |
|---|---|---|
| Board geometry (pure) | `src/board/layout.test.ts` | 34 |
| Board store (pure + localStorage) | `src/board/store.test.ts` | 16 |
| Board state + per-user isolation | `src/board/useBoards.test.tsx` | 16 |
| Board UI (management, cards) | `src/board/BoardView.test.tsx` | 11 |
| **DEF-03** drag tray → canvas | `src/board/dragDrop.test.tsx` | 1 |
| App (identity, notes, folders, stars, search, sharing, board) | `src/App.test.tsx` | 21 |
| Workspace hook | `src/hooks/useWorkspace.test.tsx` | 12 |
| API client | `src/api/client.test.ts` | 11 |
| Identity switcher | `src/components/IdentitySwitcher.test.tsx` | 3 |

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
- **Whiteboards are client-side and per-user.** A board is a spatial view of the
  caller's existing notes; layout is stored in `localStorage`, so boards do not
  sync across devices and are lost if storage is cleared. Boards are not shared.
  This is deliberate (the board adds no domain state and grants no access) and
  reversible behind the `BoardStore` interface — see
  [ADR-010](docs/ADR/ADR-010-whiteboard-client-side.md).

## 17. Deliberately deferred capabilities

- Full end-to-end OIDC against a live provider (relying-party validation is
  implemented; the live handshake is deferred).
- Distributed rate limiting (Redis/gateway).
- Metrics/tracing and an audit-log sink.
- Soft-delete/trash and note history beyond the single `version` counter.
- Auto-provisioning of OIDC users.
- Server-backed whiteboards (boards currently persist client-side behind the
  `BoardStore` interface; an `ApiBoardStore` is the documented next step, see
  [ADR-010](docs/ADR/ADR-010-whiteboard-client-side.md)).
- Board sharing/collaboration and board connectors/arrows.

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

## 19. Docker

The repository is fully containerised. One command brings up the complete,
self-contained system:

```bash
docker compose up --build
```

### Services

| Service | Image | Host port | Role |
|---|---|---|---|
| `db` | `postgres:16-alpine` | — (internal) | PostgreSQL 16 with a named volume `db_data` |
| `api` | built from [`Dockerfile`](Dockerfile) | `8000` | FastAPI backend (multi-stage build, non-root) |
| `web` | built from [`frontend/Dockerfile`](frontend/Dockerfile) | `8080` | Static React build served by nginx |

### Startup sequence

The API container's entrypoint ([`docker/api-entrypoint.sh`](docker/api-entrypoint.sh))
runs, in order:

1. **Wait for the database** — polls `SELECT 1` with a bounded timeout
   (`DB_WAIT_TIMEOUT_SECONDS`, default 60s) so a slow PostgreSQL start is not a
   crash-loop.
2. **Apply migrations** — `alembic upgrade head`.
3. **Seed demo users** — idempotent, controlled by `SEED_DEMO_USERS` (default
   `true`).
4. **Serve** — `exec`s uvicorn as PID 1.

`db` and `api` declare healthchecks, and `depends_on: condition: service_healthy`
orders the startup so `web` only starts once the API is healthy.

### How the browser talks to the API

The web tier is served by nginx (`frontend/docker/nginx.conf.template`, rendered
at container start), which serves the SPA and **reverse-proxies `/api`,
`/health`, and `/readiness` to the `api` service**. The frontend is built with an
empty `VITE_API_BASE_URL`, which the client interprets as "same origin", so
requests go to `/api/v1/...` on the page's own origin. This means:

- no CORS configuration is required for the bundled UI, and
- no backend hostname is baked into the image — the same build works anywhere.

The API is also published directly on `:8000` for `curl` and the Swagger UI.

### Configuration

All settings are environment-driven; copy
[`.env.docker.example`](.env.docker.example) to `.env` to override defaults:

```bash
cp .env.docker.example .env
```

Common overrides:

| Variable | Default | Purpose |
|---|---|---|
| `WEB_PORT` | `8080` | **Host** port for the web client |
| `API_PORT` | `8000` | **Host** port for the API |
| `WEB_INTERNAL_PORT` | `80` | Port nginx listens on *inside* the web container |
| `API_INTERNAL_PORT` | `8000` | Port uvicorn listens on *inside* the api container |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `notes` | Database credentials |
| `ENVIRONMENT` | `development` | `production` disables demo auth (startup fails if demo auth is on) |
| `AUTH_DEMO_ENABLED` | `true` | Toggles the demo identity mechanism |
| `RATE_LIMIT_REQUESTS` / `RATE_LIMIT_WINDOW_SECONDS` | `120` / `60` | Rate limit |
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `true` | Logging |

Host and container-internal ports are separate so a host-port collision can be
worked around without touching any file — for example `API_PORT=18000` publishes
the API on `18000` while the container keeps listening on `8000`. If you change
`API_INTERNAL_PORT`, the web tier's nginx proxy follows automatically (it is
rendered from `frontend/docker/nginx.conf.template` at container start).

The compose stack runs the API against **PostgreSQL** (not SQLite) to exercise
the same production-style provider.

### Verifying the running stack

A dependency-free smoke test exercises the full multi-user workflow *through the
nginx proxy* — the exact path the browser uses:

```bash
python3 docker/smoke-test.py
```

It checks identity resolution, cross-user isolation, READ/WRITE sharing,
revocation, `409` concurrency conflicts and input validation (26 checks). It
requires only a running stack and the Python standard library.

### Building the images individually

```bash
docker build -t bluestaq-notes-api .            # backend
docker build -t bluestaq-notes-web ./frontend   # frontend
```

The backend image is a two-stage build (a wheel is built, then installed into a
slim runtime) and runs as a non-root user. The frontend image builds the SPA with
Node and serves it from nginx.

### Running without Docker

Docker is a convenience, not a requirement. The manual (SQLite) workflow in
[§4](#4-install-dependencies)–[§8](#8-start-the-service) remains the primary
development path and needs no containers.

---

## Frontend workspace

A Google Drive-inspired React workspace lives in `frontend/`. It is a thin,
presentation-oriented client: every rule is enforced by the backend.

- **Layout:** header (logo, search, current user, demo indicator, identity
  switcher) · left sidebar (New note / New folder, My Drive, All notes, Starred,
  Shared with me, folder list) · main workspace (breadcrumbs, view title, sort,
  note list, editor).
- **State:** a single layer, `src/hooks/useWorkspace.ts`, owns the current user,
  view, folder, notes, selection, search, filters, sort, star state and
  permissions. Components are presentational and receive data via props.
- **HTTP:** only `src/api/client.ts` calls `fetch`; components depend on the
  `NotesApi` interface, not on HTTP.
- **Editor:** TipTap (`StarterKit` + Underline, Link, Placeholder) producing
  sanitised HTML. Explicit **Save** with the backend's optimistic-concurrency
  `version`; a `409` keeps the draft and shows a conflict banner.
- **Capabilities:** folders (create/rename/delete/select, move notes), per-user
  stars, debounced server-side search/filter/sort, and the full sharing dialog.
  Owners see Move/Share/Delete; READ collaborators get a read-only editor.

### Whiteboard view

The header's **List / Board** toggle switches to a whiteboard: multiple boards,
a tray of the user's notes, and sticky cards that can be dragged from the tray,
moved, resized, recoloured, reshaped, pinned, stacked, and auto-arranged
(by grid, colour, folder, or recency). The canvas supports pan and zoom and four
cosmetic surfaces.

- **A board is a per-user spatial view of existing notes, not a new kind of
  note.** A card holds only placement (`x`, `y`, colour, shape, size, z, pinned);
  the note itself is unchanged and still comes from the workspace. Removing a
  card removes the placement, never the note.
- **Boards are keyed by the current user**, like a star or a folder, so switching
  demo users shows a different board set and no boards leak between users.
- **Persistence is behind a `BoardStore` interface.** The prototype uses
  `localStorage` (`LocalStorageBoardStore`); boards are therefore client-side and
  do not sync across devices. Everything that is *domain* state — notes, folders,
  stars, sharing, permissions, content, concurrency — remains backend-owned and
  backend-enforced; the board grants no new access and can only show notes the
  workspace already returned for that user.
- **Structure:** `frontend/src/board/` — `types.ts` and `layout.ts` are pure and
  framework-free; `store.ts` is the persistence seam; `useBoards.ts` owns state;
  `BoardView` owns the single `DndContext` and renders `NoteTray` + `BoardCanvas`
  (a `useDroppable`) **inside** it (see DEF-03 below).

The decision and its migration path are recorded in
[ADR-010](docs/ADR/ADR-010-whiteboard-client-side.md) and
[`docs/WHITEBOARD_DESIGN.md`](docs/WHITEBOARD_DESIGN.md).

Full API contract and integration assumptions:
[`docs/FRONTEND_INTEGRATION.md`](docs/FRONTEND_INTEGRATION.md). Design decisions:
[ADR-009](docs/ADR/ADR-009-folders-stars-search-rich-text.md) (folders, stars,
search, rich text) and [ADR-010](docs/ADR/ADR-010-whiteboard-client-side.md)
(whiteboard).

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
frontend/            # React + TypeScript + Vite workspace (TipTap editor, whiteboard)
migrations/          # Alembic
tests/               # domain, application, authentication, security, persistence, api, architecture
docs/                # ARCHITECTURE, DOMAIN_MODEL, SECURITY, API, FRONTEND_INTEGRATION, WHITEBOARD_DESIGN, ADR/
Dockerfile           # backend image (multi-stage, non-root)
docker-compose.yml   # db + api + web
docker/              # api entrypoint + smoke-test.py
frontend/Dockerfile  # frontend image (build → nginx)
frontend/docker/     # nginx.conf (SPA + /api reverse proxy)
```

## License

MIT.
