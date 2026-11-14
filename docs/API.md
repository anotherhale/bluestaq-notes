# API Reference

Base path: `/api/v1`. All request and response bodies are JSON. Interactive
documentation is served at `/docs` (Swagger UI) and the schema at
`/openapi.json`.

## Authentication

Every `/api/v1` endpoint requires authentication.

- **Development/testing:** send the seeded user's id in the `X-Demo-User-ID`
  header. This is **development-only** and is refused when
  `ENVIRONMENT=production`.
- **Production:** send `Authorization: Bearer <jwt>` (OIDC; see
  `docs/SECURITY.md`).

Missing or invalid credentials → `401` with `WWW-Authenticate: Bearer`.

## Error envelope

Every failure uses the same shape:

```json
{
  "error": {
    "code": "not_found",
    "message": "note not found",
    "details": null
  },
  "request_id": "b3f1c2..."
}
```

`details` is populated only for request-validation errors (a list of
`{loc, msg, type}`).

### Status codes

| Status | When |
|---|---|
| `200` | Successful retrieval/update |
| `201` | Successful creation (note, permission) |
| `204` | Successful deletion (note, permission) |
| `400` | Malformed request (e.g. invalid `Content-Length`) |
| `401` | Missing/invalid authentication |
| `403` | Authenticated and can read, but not permitted to act |
| `404` | Resource not found **or** not readable by the caller |
| `409` | Optimistic-concurrency conflict |
| `413` | Request body too large |
| `422` | Request validation failure |
| `429` | Rate limit exceeded (`Retry-After` header) |
| `500` | Unexpected server error (details not leaked) |

`code` values: `unauthenticated`, `forbidden`, `not_found`, `conflict`,
`invalid_input`, `validation_error`, `rate_limited`, `payload_too_large`,
`internal_error`.

## Common headers

- `X-Request-ID` — correlation id, echoed on every response (inbound value
  honoured if well-formed).
- `Retry-After` — present on `429`.
- `WWW-Authenticate: Bearer` — present on `401`.

---

## Identity

### `GET /api/v1/me`

Resolve the authenticated caller.

**200**
```json
{
  "id": "11111111-1111-1111-1111-111111111111",
  "email": "alice@example.com",
  "display_name": "Alice Anderson",
  "status": "active"
}
```

`404` if the principal no longer maps to an active user.

---

## Users

### `GET /api/v1/users`

List users available for sharing. Returns only sharing-relevant fields.

Query: `limit` (1–100, default 20), `offset` (≥ 0, default 0).

**200**
```json
{
  "items": [
    { "id": "11111111-...", "email": "alice@example.com", "display_name": "Alice Anderson" }
  ],
  "meta": { "total": 4, "limit": 20, "offset": 0 }
}
```

---

## Notes

### `GET /api/v1/notes`

List notes the caller owns or has been granted access to (de-duplicated).

Query: `limit` (1–100, default 20), `offset` (≥ 0, default 0), `sort`
(one of `updated_at`, `-updated_at`, `created_at`, `-created_at`, `title`,
`-title`; default `-updated_at`). An unrecognised `sort` falls back to the
default.

**200** — items are summaries (no `content`):
```json
{
  "items": [
    {
      "id": "…", "owner_id": "…", "title": "…", "version": 1,
      "created_at": "2026-11-14T15:53:25.351155Z",
      "updated_at": "2026-11-14T15:53:25.351160Z"
    }
  ],
  "meta": { "total": 1, "limit": 20, "offset": 0 }
}
```

### `POST /api/v1/notes`

Create a note owned by the caller. Ownership is taken from the principal, never
from the body.

Request:
```json
{ "title": "Meeting notes", "content": "…" }
```
`title` is required, 1–200 chars, non-blank. `content` is optional, ≤ 64 KiB.
Unknown fields are rejected (`422`).

**201** — the created note (full representation).

### `GET /api/v1/notes/{note_id}`

Retrieve one note. `note_id` must be a UUID (`422` otherwise).

**200** — the note (full representation):
```json
{
  "id": "…", "owner_id": "…", "title": "…", "content": "…", "version": 2,
  "created_at": "…Z", "updated_at": "…Z"
}
```
`404` if it does not exist or the caller cannot read it.

### `PUT /api/v1/notes/{note_id}`

Update title/content with optimistic concurrency control.

Request:
```json
{ "title": "…", "content": "…", "version": 2 }
```
`version` is the version the client last read and is **required** (`≥ 1`).

**200** — the updated note (version incremented).
`403` if the caller can read but not update (e.g. READ collaborator).
`404` if not readable.
`409` if `version` is stale.

### `DELETE /api/v1/notes/{note_id}`

Delete a note. **Owner only.**

**204** on success. `403` if the caller can read but is not the owner
(e.g. a WRITE collaborator). `404` if not readable.

---

## Permissions

All permission endpoints are **owner-only**.

### `GET /api/v1/notes/{note_id}/permissions`

List collaborators on a note.

**200**
```json
{
  "items": [
    {
      "user_id": "22222222-…", "permission": "read",
      "granted_by": "11111111-…", "created_at": "…Z"
    }
  ],
  "meta": { "total": 1, "limit": 1, "offset": 0 }
}
```
`403` if the caller can read but is not the owner. `404` if not readable.

### `POST /api/v1/notes/{note_id}/permissions`

Grant or upgrade a collaborator's access. Re-sharing an existing collaborator at
a higher level upgrades the grant in place.

Request:
```json
{ "user_id": "22222222-…", "permission": "read" }
```
`permission` ∈ `read` | `write` (case-insensitive on input).

**201** — the grant:
```json
{ "user_id": "…", "permission": "read", "granted_by": "…", "created_at": "…Z" }
```
`403` if not the owner. `404` if the note is not readable.
`422` if the target user does not exist or is inactive, or if the target is the
owner.

### `DELETE /api/v1/notes/{note_id}/permissions/{user_id}`

Revoke a collaborator's access.

**204** on success. `404` if no such grant exists or the note is not readable.
`403` if the caller can read but is not the owner.

---

## Meta

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness probe → `{"status":"ok"}` (unauthenticated) |
| `GET /readiness` | Readiness probe → `{"status":"ready"}` (unauthenticated) |
| `GET /docs` | Swagger UI |
| `GET /openapi.json` | OpenAPI 3 schema |

---

## Worked example

See the README for a full multi-user `curl` walkthrough (create → share as READ
→ verify no write → upgrade to WRITE → verify write → revoke → verify denial).
