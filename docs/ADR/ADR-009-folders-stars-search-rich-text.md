# ADR-009: Folders, per-user stars, search and rich-text content

## Status

Accepted.

## Context

The workspace needed four capabilities the original domain did not model:
folder organisation, starring, server-side search/filter/sort, and rich-text
note bodies. Each had a design fork that would have been expensive to reverse,
so the decisions are recorded here.

## Decisions

### 1. Folders are flat and owner-scoped

A `Folder` is `{ id, owner_id, name, created_at, updated_at }` — **no parent**.
The product needs a single level of grouping, and the domain does not justify the
extra invariants (cycle prevention, depth limits, cascading) that a tree brings.
A flat list is the simplest model that satisfies the feature.

Folders are an **organisation** tool, never an authorization boundary:

- A folder belongs to exactly one user.
- A note has one owner and at most one folder, and **only the owner** may change
  that membership. Moving a note never changes its owner.
- Sharing a note does **not** share its folder, and a folder never grants access
  to the notes inside it.
- Folder names are unique per owner (`409` on duplicates).

**Deleting a folder does not delete notes.** Its notes are reassigned to the
root (`folder_id = null`). Deleting a note the owner never explicitly deleted
would be a data-loss footgun; re-parenting is the safe, reversible choice.

### 2. Stars are per-user, not per-note

A star is a personal bookmark, so it is modelled as a `NoteStar(note_id,
user_id)` relationship rather than a boolean on `Note`. One collaborator
starring a note must not star it for everyone. The note responses expose the
**caller's own** `is_starred`. Any user who may read a note may star it.

### 3. Rich-text content is sanitised HTML, as a domain invariant

Note bodies are HTML fragments (TipTap `getHTML()`), stored as text. HTML was
chosen over a JSON document because the existing plain-text content is already
valid HTML and therefore needs **no migration**, and because it round-trips
through the editor and the API cleanly.

Sanitisation is **not** a presentation concern. `NoteContent` runs the value
through `nh3` on construction, so it is applied on write *and* on read, and
cannot be bypassed by adding a transport. The allowlist is exactly the editor's
vocabulary; scripts, iframes, event handlers and `javascript:`/`data:` URLs are
removed. See [FRONTEND_INTEGRATION.md §10](../FRONTEND_INTEGRATION.md).

### 4. Search is SQL `LIKE`, resolved server-side

`GET /notes` gained `q`, `view`, `folder_id`, `starred`, `sort`, `limit`,
`offset`. Search is a case-insensitive `LIKE` over title and content, executed by
the repository against the caller's **authorized** set. This is SQLite- and
PostgreSQL-compatible, needs no new service, and — critically — cannot leak
notes the caller may not read, because authorization is applied by the query, not
after it. A dedicated search engine is unjustified at this scale.

### 5. New endpoints

```
GET    /api/v1/folders
POST   /api/v1/folders
PATCH  /api/v1/folders/{folder_id}
DELETE /api/v1/folders/{folder_id}
PATCH  /api/v1/notes/{note_id}/folder      # move (owner only)
PUT    /api/v1/notes/{note_id}/star
DELETE /api/v1/notes/{note_id}/star
```

These follow the existing naming, envelope and error conventions. Business logic
lives in application use cases behind the existing Unit of Work; route handlers
only translate HTTP. New authorization checks reuse the existing policy.

### 6. New authorization rules

- Only the **note owner** may move a note. A WRITE collaborator may edit content
  but not reorganise the owner's drive (`403`, since they can read the note).
- Folder CRUD is owner-only; another user's folder is `404`, consistent with
  ADR-008.
- Starring requires read access; otherwise `404`.
- The note read/update responses expose `caller_permission` (the caller's own
  level) so a collaborator can be told whether it may edit, without the server
  leaking the full collaborator list to non-owners.

## Consequences

- A flat folder model is easy to reason about; if hierarchy is ever needed it is
  an additive `parent_id` with new invariants, not a rewrite.
- Per-user stars add a join table and one extra lookup per note in a list, which
  is trivial at this scale.
- Sanitising on read costs a little CPU per fetch but makes safety independent of
  write-path correctness.
- Search quality is `LIKE`-level (no ranking, no stemming). Acceptable for the
  demonstration; a search engine remains an isolated future change behind the
  repository port.
