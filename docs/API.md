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

**Exception — the tool gateway** (`/api/v1/agents/tools/*`) is agent-facing: it
accepts only `Authorization: Bearer <agent token>` and deliberately ignores
`X-Demo-User-ID`. See [Tool gateway](#tool-gateway-agents).

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

List notes the caller owns or has been granted access to (de-duplicated), or a
filtered view of them.

Query parameters:

| Param | Values | Meaning |
|---|---|---|
| `limit` | 1–100 (default 20) | Page size |
| `offset` | ≥ 0 (default 0) | Page offset |
| `sort` | `updated_at` \| `-updated_at` \| `created_at` \| `-created_at` \| `title` \| `-title` (default `-updated_at`) | Ordering; `-` = descending; unknown falls back to the default |
| `q` | free text | Case-insensitive match against title **or** content |
| `view` | `all` \| `owned` \| `shared` | All readable / owned by caller / shared *with* caller |
| `folder_id` | uuid | Only notes in that folder (must be a folder the caller owns) |
| `starred` | `true` | Only the caller's starred notes |

Search and filtering are applied to the caller's **authorized** set, so an
unauthorized note can never be returned even if it matches `q`.

**200** — items are summaries (no `content`):
```json
{
  "items": [
    {
      "id": "…", "owner_id": "…", "title": "…", "version": 1,
      "folder_id": "… | null",
      "is_starred": false,
      "caller_permission": "read | write | null",
      "created_at": "2026-11-14T15:53:25.351155Z",
      "updated_at": "2026-11-14T15:53:25.351160Z"
    }
  ],
  "meta": { "total": 1, "limit": 20, "offset": 0 }
}
```

`is_starred` and `caller_permission` are the **caller's own** values;
`caller_permission` is `"write"` for the owner and `null` only if the caller
cannot read the note (in which case it would not be listed).

### `POST /api/v1/notes`

Create a note owned by the caller. Ownership is taken from the principal, never
from the body.

Request:
```json
{ "title": "Meeting notes", "content": "<p>…</p>", "folder_id": "… | null" }
```
`title` is required, 1–200 chars, non-blank. `content` is optional sanitised HTML
(≤ 64 KiB; see [rich text](#rich-text-content)). `folder_id` is optional and must
be a folder the caller owns (`422` otherwise). Unknown fields are rejected
(`422`).

**201** — the created note (full representation).

### `GET /api/v1/notes/{note_id}`

Retrieve one note. `note_id` must be a UUID (`422` otherwise).

**200** — the note (full representation):
```json
{
  "id": "…", "owner_id": "…", "title": "…", "content": "<h1>…</h1>",
  "version": 2,
  "folder_id": "… | null",
  "is_starred": false,
  "caller_permission": "read | write | null",
  "created_at": "…Z", "updated_at": "…Z"
}
```
`404` if it does not exist or the caller cannot read it.

### `PUT /api/v1/notes/{note_id}`

Update title/content with optimistic concurrency control.

Request:
```json
{ "title": "…", "content": "<p>…</p>", "version": 2 }
```
`version` is the version the client last read and is **required** (`≥ 1`).

**200** — the updated note (version incremented).
`403` if the caller can read but not update (e.g. READ collaborator).
`404` if not readable.
`409` if `version` is stale.

### `PATCH /api/v1/notes/{note_id}/folder`

Move a note into a folder, or back to the root. **Owner only** — a WRITE
collaborator may edit content but not reorganise the owner's drive.

Request:
```json
{ "folder_id": "uuid | null" }
```
`null` moves the note to the root. A non-null `folder_id` must be a folder the
caller owns.

**200** — the updated note. `403` if the caller can read but is not the owner.
`404` if not readable. `422` if `folder_id` is not a folder the caller owns.
Moving a note does **not** change its owner or its sharing permissions.

### `PUT /api/v1/notes/{note_id}/star`

Star the note **for the caller** (per-user). Idempotent.

**200** — the updated note with `is_starred: true`. `404` if not readable.

### `DELETE /api/v1/notes/{note_id}/star`

Unstar the note for the caller. Idempotent.

**200** — the updated note with `is_starred: false`. `404` if not readable.

### `DELETE /api/v1/notes/{note_id}`

Delete a note. **Owner only.**

**204** on success. `403` if the caller can read but is not the owner
(e.g. a WRITE collaborator). `404` if not readable.

---

## Folders

Folders are flat and owner-scoped. They organise a user's own notes; they are
never a sharing boundary, and a folder never grants access to the notes inside
it.

### `GET /api/v1/folders`

List the caller's folders.

**200**:
```json
{
  "items": [
    { "id": "…", "owner_id": "…", "name": "Project Alpha",
      "created_at": "…Z", "updated_at": "…Z" }
  ],
  "meta": { "total": 1, "limit": 20, "offset": 0 }
}
```

### `POST /api/v1/folders`

Create a folder owned by the caller.

Request:
```json
{ "name": "Project Alpha" }
```
`name` is required, 1–100 chars, non-blank. Names are unique **per owner**.

**201** — the created folder. `409` if the caller already has a folder with that
name.

### `PATCH /api/v1/folders/{folder_id}`

Rename a folder. **Owner only.**

Request:
```json
{ "name": "Project Beta" }
```
**200** — the updated folder. `404` if it does not exist or the caller is not the
owner. `409` on a duplicate name.

### `DELETE /api/v1/folders/{folder_id}`

Delete a folder. **Owner only.** Notes inside are **not deleted** — they are
moved to the root (`folder_id` becomes `null`).

**204** on success. `404` if it does not exist or the caller is not the owner.

---

## Rich text content

Note `content` is a **sanitised HTML fragment**. The frontend editor (TipTap)
produces this HTML, but the backend never trusts it:

- Content is sanitised with `nh3` **in the domain** (`NoteContent`), on write
  **and** on read. Plain-text content from before this feature is valid HTML and
  is preserved unchanged (no migration was needed).
- Allowed tags: `p`, `br`, `hr`, `h1`–`h3`, `strong`, `b`, `em`, `i`, `u`, `s`,
  `ul`, `ol`, `li`, `blockquote`, `code`, `pre`, `a[href]`.
- Removed: `<script>`, `<style>`, `<iframe>`, all event-handler attributes
  (`onclick`, `onerror`, …), and `javascript:`/`data:` URLs.
- Safe external links gain `rel="noopener noreferrer"`.

Example round-trip:

```json
{ "title": "Plan", "content": "<h1>Q4</h1><p><strong>bold</strong></p><ul><li>one</li></ul>" }
```

Submitting `<script>alert(1)</script><p>ok</p>` stores and returns `<p>ok</p>`.

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

## Tool gateway (agents)

Agent-only surface. It is the HTTP edge over the same governed use case that
application code calls, so audience, revocation, capability, approval, and audit
are enforced in exactly one place — this router adds no decisions of its own.

### `POST /api/v1/agents/tools/invoke`

**Authentication:** `Authorization: Bearer <agent token>` only. The
`X-Demo-User-ID` header is **ignored** here, so holding a demo user id is not
authority to invoke a tool. Missing or malformed credentials → `401`.

The token must have been minted for `TokenAudience.TOOL_GATEWAY`. A Notes-API
token presented here is refused with `403` even though it is otherwise valid.

**Request**

```json
{
  "tool": "read_note",
  "arguments": {"note_id": "b1f2..."},
  "idempotency_key": "run-42-step-7"
}
```

`idempotency_key` is required: a retry with the same key replays the recorded
outcome (`replayed: true`) instead of repeating the side effect. Reusing a key
with *different* arguments is a `409`, never a silent overwrite.

**Response — 200**

```json
{
  "id": "0d9c...",
  "tool": "read_note",
  "capability": "read_note",
  "status": "succeeded",
  "resource_ref": "note:b1f2...",
  "idempotency_key": "run-42-step-7",
  "invoked_at": "2026-11-25T02:40:11Z",
  "completed_at": "2026-11-25T02:40:11Z",
  "outcome": null,
  "replayed": false,
  "output": {"note": {"id": "b1f2...", "title": "..."}}
}
```

**Refusals** are audited and return a status, not a `500`:

| Status | When |
|---|---|
| `401` | No or malformed bearer token |
| `403` | Token audience is not the tool gateway; capability not granted; delegation revoked or attenuated below the tool |
| `409` | `idempotency_key` reused with different arguments |
| `422` | Unknown tool name, or arguments failing the tool's schema |

`output` is empty for a refusal; `outcome` carries the reason. The agent token is
never echoed in any response.

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
