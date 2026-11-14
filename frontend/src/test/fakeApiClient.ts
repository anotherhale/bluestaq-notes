/**
 * A FakeApiClient for component tests.
 *
 * It implements the same {@link NotesApi} interface as the real HTTP client but
 * keeps everything in memory. Crucially, it reproduces the *backend's*
 * authorization rules (ownership, READ/WRITE, revocation, optimistic
 * concurrency, 403-vs-404 semantics) rather than being permissive, so the UI is
 * exercised against realistic success AND failure paths — including the
 * cross-user isolation cases.
 *
 * It is a test double, not a second implementation of the product: it exists so
 * component tests never touch the network. The real backend is exercised
 * separately by the integration suite in src/test/integration.
 */

import { ApiError } from "../api/errors";
import type { NotesApi } from "../api/client";
import type {
  CurrentUser,
  DemoIdentityList,
  Note,
  NoteCreateInput,
  NoteList,
  NoteUpdateInput,
  Permission,
  PermissionGrantInput,
  PermissionList,
  UserList,
} from "../api/types";

interface FakeUser {
  id: string;
  email: string;
  display_name: string;
  status: "active" | "suspended" | "disabled";
}

interface FakePermission {
  note_id: string;
  user_id: string;
  permission: "read" | "write";
  granted_by: string;
  created_at: string;
}

interface FakeNote {
  id: string;
  owner_id: string;
  title: string;
  content: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export const FAKE_ALICE = "11111111-1111-1111-1111-111111111111";
export const FAKE_BOB = "22222222-2222-2222-2222-222222222222";
export const FAKE_CAROL = "33333333-3333-3333-3333-333333333333";

const ISO = "2026-11-14T12:00:00Z";

let idCounter = 0;
function nextId(prefix: string): string {
  idCounter += 1;
  return `${prefix}-0000-4000-8000-${String(idCounter).padStart(12, "0")}`;
}

function notFound(): ApiError {
  return new ApiError({ message: "note not found", status: 404, code: "not_found" });
}
function forbidden(message: string): ApiError {
  return new ApiError({ message, status: 403, code: "forbidden" });
}
function unauthenticated(): ApiError {
  return new ApiError({ message: "Not authenticated", status: 401, code: "unauthenticated" });
}

export class FakeApiClient implements NotesApi {
  private users: FakeUser[];
  private notes: FakeNote[] = [];
  private permissions: FakePermission[] = [];
  private selected: string | null = null;
  private demoEnabled: boolean;

  /** Set to true to simulate the backend being unreachable (status 0). */
  public failNetwork = false;

  constructor(options: { demoEnabled?: boolean; users?: FakeUser[] } = {}) {
    this.demoEnabled = options.demoEnabled ?? true;
    this.users =
      options.users ??
      [
        { id: FAKE_ALICE, email: "alice@example.com", display_name: "Alice Anderson", status: "active" },
        { id: FAKE_BOB, email: "bob@example.com", display_name: "Bob Baker", status: "active" },
        { id: FAKE_CAROL, email: "carol@example.com", display_name: "Carol Chen", status: "active" },
      ];
  }

  // -- test helpers -----------------------------------------------------

  /** Seed a note directly, as if it already existed in the backend. */
  seedNote(ownerId: string, title: string, content = ""): FakeNote {
    const note: FakeNote = {
      id: nextId("note"),
      owner_id: ownerId,
      title,
      content,
      version: 1,
      created_at: ISO,
      updated_at: ISO,
    };
    this.notes.push(note);
    return note;
  }

  /** Seed a permission grant directly. */
  seedPermission(noteId: string, userId: string, level: "read" | "write"): void {
    this.permissions.push({
      note_id: noteId,
      user_id: userId,
      permission: level,
      granted_by: this.ownerOf(noteId),
      created_at: ISO,
    });
  }

  /** Bump a note's version without going through the client (simulates another writer). */
  mutateNoteExternally(noteId: string, content: string): void {
    const note = this.requireNote(noteId);
    note.content = content;
    note.version += 1;
  }

  private guard(): void {
    if (this.failNetwork) {
      throw new ApiError({ message: "Could not reach the backend", status: 0 });
    }
  }

  private currentUserId(): string {
    if (!this.selected) throw unauthenticated();
    return this.selected;
  }

  private user(userId: string): FakeUser | null {
    return this.users.find((user) => user.id === userId) ?? null;
  }

  private ownerOf(noteId: string): string {
    const note = this.requireNote(noteId);
    return note.owner_id;
  }

  private requireNote(noteId: string): FakeNote {
    const note = this.notes.find((candidate) => candidate.id === noteId);
    if (!note) throw notFound();
    return note;
  }

  private levelFor(noteId: string, userId: string): "read" | "write" | null {
    const grant = this.permissions.find(
      (permission) => permission.note_id === noteId && permission.user_id === userId,
    );
    return grant ? grant.permission : null;
  }

  private canRead(noteId: string, userId: string): boolean {
    const note = this.requireNote(noteId);
    return note.owner_id === userId || this.levelFor(noteId, userId) !== null;
  }

  private canWrite(noteId: string, userId: string): boolean {
    const note = this.requireNote(noteId);
    if (note.owner_id === userId) return true;
    return this.levelFor(noteId, userId) === "write";
  }

  // -- NotesApi ---------------------------------------------------------

  selectIdentity(userId: string | null): void {
    this.selected = userId;
  }

  selectedIdentity(): string | null {
    return this.selected;
  }

  async getDemoIdentities(): Promise<DemoIdentityList> {
    this.guard();
    if (!this.demoEnabled) {
      return { enabled: false, header: "X-Demo-User-ID", items: [], meta: { total: 0, limit: 0, offset: 0 } };
    }
    const items = this.users
      .filter((user) => user.status === "active")
      .map(({ id, email, display_name }) => ({ id, email, display_name }));
    return {
      enabled: true,
      header: "X-Demo-User-ID",
      items,
      meta: { total: items.length, limit: 20, offset: 0 },
    };
  }

  async getCurrentUser(): Promise<CurrentUser | null> {
    this.guard();
    if (!this.selected) return null;
    const user = this.user(this.selected);
    if (!user) throw unauthenticated();
    return { id: user.id, email: user.email, display_name: user.display_name, status: user.status };
  }

  async listUsers(): Promise<UserList> {
    this.guard();
    this.currentUserId();
    const items = this.users.map(({ id, email, display_name }) => ({ id, email, display_name }));
    return { items, meta: { total: items.length, limit: 20, offset: 0 } };
  }

  async listNotes(): Promise<NoteList> {
    this.guard();
    const userId = this.currentUserId();
    const items = this.notes
      .filter((note) => this.canRead(note.id, userId))
      .map((note) => ({
        id: note.id,
        owner_id: note.owner_id,
        title: note.title,
        version: note.version,
        created_at: note.created_at,
        updated_at: note.updated_at,
      }));
    return { items, meta: { total: items.length, limit: 20, offset: 0 } };
  }

  async getNote(noteId: string): Promise<Note> {
    this.guard();
    const userId = this.currentUserId();
    const note = this.requireNote(noteId);
    // Unreadable notes are hidden as 404, matching the backend.
    if (!this.canRead(noteId, userId)) throw notFound();
    return { ...note };
  }

  async createNote(input: NoteCreateInput): Promise<Note> {
    this.guard();
    const userId = this.currentUserId();
    if (!input.title.trim()) {
      throw new ApiError({ message: "title must not be empty", status: 422, code: "invalid_input" });
    }
    const note: FakeNote = {
      id: nextId("note"),
      owner_id: userId,
      title: input.title.trim(),
      content: input.content,
      version: 1,
      created_at: ISO,
      updated_at: ISO,
    };
    this.notes.push(note);
    return { ...note };
  }

  async updateNote(noteId: string, input: NoteUpdateInput): Promise<Note> {
    this.guard();
    const userId = this.currentUserId();
    const note = this.requireNote(noteId);
    if (!this.canRead(noteId, userId)) throw notFound();
    if (!this.canWrite(noteId, userId)) {
      throw forbidden("you do not have permission to modify this note");
    }
    if (input.version !== note.version) {
      throw new ApiError({
        message: `note has been modified by another client; current version is ${note.version}`,
        status: 409,
        code: "conflict",
      });
    }
    note.title = input.title.trim();
    note.content = input.content;
    note.version += 1;
    note.updated_at = ISO;
    return { ...note };
  }

  async deleteNote(noteId: string): Promise<void> {
    this.guard();
    const userId = this.currentUserId();
    const note = this.requireNote(noteId);
    if (!this.canRead(noteId, userId)) throw notFound();
    if (note.owner_id !== userId) {
      throw forbidden("only the note owner may delete this note");
    }
    this.notes = this.notes.filter((candidate) => candidate.id !== noteId);
    this.permissions = this.permissions.filter((permission) => permission.note_id !== noteId);
  }

  async listPermissions(noteId: string): Promise<PermissionList> {
    this.guard();
    const userId = this.currentUserId();
    const note = this.requireNote(noteId);
    if (!this.canRead(noteId, userId)) throw notFound();
    if (note.owner_id !== userId) {
      throw forbidden("only the note owner may view permissions");
    }
    const items: Permission[] = this.permissions
      .filter((permission) => permission.note_id === noteId)
      .map((permission) => ({
        user_id: permission.user_id,
        permission: permission.permission,
        granted_by: permission.granted_by,
        created_at: permission.created_at,
      }));
    return { items, meta: { total: items.length, limit: 20, offset: 0 } };
  }

  async grantPermission(noteId: string, input: PermissionGrantInput): Promise<Permission> {
    this.guard();
    const userId = this.currentUserId();
    const note = this.requireNote(noteId);
    if (!this.canRead(noteId, userId)) throw notFound();
    if (note.owner_id !== userId) {
      throw forbidden("only the note owner may manage permissions");
    }
    if (input.user_id === note.owner_id) {
      throw new ApiError({
        message: "the note owner already has full access",
        status: 422,
        code: "invalid_input",
      });
    }
    const target = this.user(input.user_id);
    if (!target || target.status !== "active") {
      throw new ApiError({
        message: "target user does not exist or is not active",
        status: 422,
        code: "invalid_input",
      });
    }
    const existing = this.permissions.find(
      (permission) => permission.note_id === noteId && permission.user_id === input.user_id,
    );
    if (existing) {
      existing.permission = input.permission;
      existing.granted_by = userId;
    } else {
      this.permissions.push({
        note_id: noteId,
        user_id: input.user_id,
        permission: input.permission,
        granted_by: userId,
        created_at: ISO,
      });
    }
    return {
      user_id: input.user_id,
      permission: input.permission,
      granted_by: userId,
      created_at: ISO,
    };
  }

  async revokePermission(noteId: string, userId: string): Promise<void> {
    this.guard();
    const actor = this.currentUserId();
    const note = this.requireNote(noteId);
    if (!this.canRead(noteId, actor)) throw notFound();
    if (note.owner_id !== actor) {
      throw forbidden("only the note owner may manage permissions");
    }
    const before = this.permissions.length;
    this.permissions = this.permissions.filter(
      (permission) => !(permission.note_id === noteId && permission.user_id === userId),
    );
    if (this.permissions.length === before) throw notFound();
  }
}
