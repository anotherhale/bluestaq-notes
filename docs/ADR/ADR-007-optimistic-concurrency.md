# ADR-007: Optimistic concurrency strategy

## Status

Accepted.

## Context

Two clients may load the same note and both submit edits. Last-write-wins would
silently discard one user's work. The assignment requires that a stale update
must never silently overwrite a newer one, and that an outdated client receives
an appropriate conflict response (HTTP 409).

## Decision

Use **optimistic concurrency control with a monotonic `version` field** on the
note.

- `version` starts at `1` and increments by exactly one on every accepted
  content mutation. Sharing changes touch `updated_at` but do **not** bump the
  content version.
- Update requests must include the `version` they edited (`NoteUpdateRequest.version`,
  `ge=1`). Omitting it is a `422`.
- The version is checked in **two places**:
  1. In the domain (`Note.update_content` → `_assert_version`) as a safety net.
  2. In the repository, as a single atomic statement:
     `UPDATE notes SET ... , version = :new WHERE id = :id AND version = :expected`.
     If zero rows match, a `VersionConflict` is raised.
- `VersionConflict` is translated to **HTTP 409** with a message reporting the
  current version.

The repository compare-and-set is what actually protects against a race: two
writers that both loaded version 1 cannot both succeed, because the second
`UPDATE ... WHERE version = 1` matches no rows.

## Alternatives considered

- **Last-write-wins.** Rejected explicitly by the requirements.
- **Pessimistic locking (`SELECT ... FOR UPDATE`).** Works but holds locks for
  the request duration, does not map cleanly onto SQLite, and is unnecessary for
  a low-contention notes domain.
- **ETag / `If-Match` only.** A valid HTTP-native alternative. We chose an
  explicit body `version` field because it is simpler to document and test with
  `curl`, and the version is also useful to clients outside of HTTP. The
  `version` field is exposed in every note response, so it can back an ETag
  later without a schema change.

## Consequences

- Stale writes are rejected with 409, never silently applied.
- Clients have a clear, simple protocol: read `version`, send it back on update.
- Covered by a domain test, an API test (409), and a repository test that runs
  on both SQLite and PostgreSQL.

## Tradeoffs

- Optimistic concurrency can surface conflicts to users who must retry. For this
  domain (human editing of notes) conflicts are rare and a retry is acceptable,
  which is the classic condition under which optimistic control is preferred.
