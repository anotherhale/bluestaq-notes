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

describe("integration: folders, stars, search and rich text", () => {
  // A unique suffix keeps repeated runs against a shared database independent.
  const suffix = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
  let folderId: string;
  let noteId: string;

  afterAll(async () => {
    if (noteId) await alice.deleteNote(noteId).catch(() => undefined);
    if (folderId) await alice.deleteFolder(folderId).catch(() => undefined);
  });

  it("creates a folder and a note inside it", async () => {
    const folder = await alice.createFolder({ name: `Integration ${suffix}` });
    folderId = folder.id;
    expect(folder.owner_id).toBe(ALICE);

    const note = await alice.createNote({
      title: `Folder note ${suffix}`,
      content: "<h1>Q4</h1><p><strong>bold</strong></p><ul><li>one</li></ul>",
      folder_id: folderId,
    });
    noteId = note.id;
    expect(note.folder_id).toBe(folderId);
    // Rich text survives the round trip.
    expect(note.content).toContain("<h1>Q4</h1>");
    expect(note.content).toContain("<ul>");
  });

  it("stores only sanitised HTML", async () => {
    const note = await alice.createNote({
      title: `Sanitise ${suffix}`,
      content: "<script>alert(1)</script><p onclick='x()'>safe</p><a href='javascript:evil()'>x</a>",
    });
    expect(note.content).not.toContain("<script");
    expect(note.content).not.toContain("onclick");
    expect(note.content).not.toContain("javascript:");
    expect(note.content).toContain("safe");
    await alice.deleteNote(note.id);
  });

  it("stars a note and finds it via the starred filter", async () => {
    const starred = await alice.starNote(noteId);
    expect(starred.is_starred).toBe(true);

    const list = await alice.listNotes({ starred: true });
    expect(list.items.some((note) => note.id === noteId)).toBe(true);
  });

  it("finds the note by title and by content", async () => {
    const byTitle = await alice.listNotes({ q: `Folder note ${suffix}` });
    expect(byTitle.items.some((note) => note.id === noteId)).toBe(true);

    const byContent = await alice.listNotes({ q: "bold" });
    expect(byContent.items.some((note) => note.id === noteId)).toBe(true);
  });

  it("filters by folder", async () => {
    const list = await alice.listNotes({ folder_id: folderId });
    expect(list.items.some((note) => note.id === noteId)).toBe(true);
    expect(list.items.every((note) => note.folder_id === folderId)).toBe(true);
  });

  it("moves a note back to the root without changing its owner", async () => {
    const moved = await alice.moveNote(noteId, { folder_id: null });
    expect(moved.folder_id).toBeNull();
    expect(moved.owner_id).toBe(ALICE);
  });

  it("rejects a move by a non-owner with 403", async () => {
    await alice.grantPermission(noteId, { user_id: BOB, permission: "write" });
    const error = await bob.moveNote(noteId, { folder_id: null }).catch((c: unknown) => c);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(403);
    await alice.revokePermission(noteId, BOB);
  });

  it("reports another user's folder as 404", async () => {
    const error = await bob
      .renameFolder(folderId, { name: "hijacked" })
      .catch((c: unknown) => c);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
  });

  it("deletes a folder but keeps its notes (moved to root)", async () => {
    const folder = await alice.createFolder({ name: `Temp ${suffix}` });
    const note = await alice.createNote({
      title: `Survivor ${suffix}`,
      content: "kept",
      folder_id: folder.id,
    });

    await alice.deleteFolder(folder.id);

    const after = await alice.getNote(note.id);
    expect(after.folder_id).toBeNull();
    await alice.deleteNote(note.id);
  });
});

afterAll(() => {
  // Best-effort cleanup if a test failed before deleting the note.
  if (createdNoteId) {
    void alice.deleteNote(createdNoteId).catch(() => undefined);
  }
});
