/// <reference types="node" />
/**
 * Integration tests against a REAL running backend.
 *
 * These are deliberately separate from the component suite: they hit the actual
 * FastAPI service over HTTP and assert that the frontend client speaks the real
 * contract correctly (headers, status codes, error envelopes, concurrency).
 *
 * Run:
 *   BACKEND_BASE_URL=http://127.0.0.1:8211 npm run test:integration
 *
 * The suite requires a development backend seeded with demo users (the same
 * Alice/Bob ids used everywhere else). It creates its own note per run so it is
 * safe to run repeatedly against a shared database.
 */

import { afterAll, beforeAll, describe, expect, it } from "vitest";

import { HttpNotesApi } from "../../api/client";
import { ApiError } from "../../api/errors";

const BASE_URL = process.env.BACKEND_BASE_URL ?? "http://127.0.0.1:8211";

const ALICE = "11111111-1111-1111-1111-111111111111";
const BOB = "22222222-2222-2222-2222-222222222222";

let alice: HttpNotesApi;
let bob: HttpNotesApi;
let createdNoteId: string;

beforeAll(async () => {
  alice = new HttpNotesApi(BASE_URL);
  bob = new HttpNotesApi(BASE_URL);

  // Confirm the backend is up and demo mode is enabled before proceeding.
  const identities = await alice.getDemoIdentities();
  if (!identities.enabled) {
    throw new Error(
      `Backend at ${BASE_URL} does not have demo auth enabled; cannot run integration tests`,
    );
  }
  alice.selectIdentity(ALICE);
  bob.selectIdentity(BOB);
});

describe("integration: real backend contract", () => {
  it("resolves the current user via the demo header", async () => {
    const me = await alice.getCurrentUser();
    expect(me?.id).toBe(ALICE);
    expect(me?.email).toBe("alice@example.com");
  });

  it("creates a private note owned by the caller", async () => {
    const note = await alice.createNote({
      title: "Integration note",
      content: "created by the frontend integration suite",
    });
    createdNoteId = note.id;
    expect(note.owner_id).toBe(ALICE);
    expect(note.version).toBe(1);
  });

  it("keeps the note private from another user (404, not 403)", async () => {
    const error = await bob.getNote(createdNoteId).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
  });

  it("shares the note READ and lets the recipient read it", async () => {
    await alice.grantPermission(createdNoteId, { user_id: BOB, permission: "read" });

    const bobView = await bob.getNote(createdNoteId);
    expect(bobView.title).toBe("Integration note");

    // Bob now sees it in his list.
    const list = await bob.listNotes();
    expect(list.items.some((note) => note.id === createdNoteId)).toBe(true);
  });

  it("prevents a READ recipient from modifying the note (403)", async () => {
    const bobView = await bob.getNote(createdNoteId);
    const error = await bob
      .updateNote(createdNoteId, {
        title: "hacked",
        content: "hacked",
        version: bobView.version,
      })
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(403);

    // The content is unchanged.
    const after = await alice.getNote(createdNoteId);
    expect(after.title).toBe("Integration note");
  });

  it("prevents a non-owner from managing permissions (403)", async () => {
    const error = await bob
      .grantPermission(createdNoteId, { user_id: BOB, permission: "write" })
      .catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(403);
  });

  it("allows a WRITE recipient to modify the note", async () => {
    await alice.grantPermission(createdNoteId, { user_id: BOB, permission: "write" });

    const bobView = await bob.getNote(createdNoteId);
    const updated = await bob.updateNote(createdNoteId, {
      title: "Integration note (edited by Bob)",
      content: "bob edited this",
      version: bobView.version,
    });
    expect(updated.version).toBe(bobView.version + 1);
    expect(updated.title).toBe("Integration note (edited by Bob)");
  });

  it("rejects a stale update with 409", async () => {
    const stale = await alice.getNote(createdNoteId);
    // Alice writes first, advancing the version...
    await alice.updateNote(createdNoteId, {
      title: "Integration note (edited by Alice)",
      content: "alice edited this",
      version: stale.version,
    });
    // ...then Bob reuses the now-stale version.
    const error = await bob
      .updateNote(createdNoteId, {
        title: "stale",
        content: "stale",
        version: stale.version,
      })
      .catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(409);
  });

  it("denies access immediately after revocation", async () => {
    await alice.revokePermission(createdNoteId, BOB);

    const error = await bob.getNote(createdNoteId).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
  });

  it("only the owner can delete the note", async () => {
    // Bob never had delete rights; re-grant WRITE to prove it is not enough.
    await alice.grantPermission(createdNoteId, { user_id: BOB, permission: "write" });
    const bobView = await bob.getNote(createdNoteId);
    expect(bobView.id).toBe(createdNoteId);

    const error = await bob.deleteNote(createdNoteId).catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(403);

    // Alice deletes it successfully.
    await alice.deleteNote(createdNoteId);
    const gone = await alice.getNote(createdNoteId).catch((caught: unknown) => caught);
    expect((gone as ApiError).status).toBe(404);
  });

  it("returns 401 when no identity is selected", async () => {
    const anonymous = new HttpNotesApi(BASE_URL);
    const error = await anonymous.listNotes().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(401);
  });
});

afterAll(() => {
  // Best-effort cleanup if a test failed before deleting the note.
  if (createdNoteId) {
    void alice.deleteNote(createdNoteId).catch(() => undefined);
  }
});
