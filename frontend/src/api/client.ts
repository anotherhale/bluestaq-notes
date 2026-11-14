/**
 * The API client: the ONLY module in the frontend that performs HTTP.
 *
 * Responsibilities:
 *  - centralize the base URL and request headers;
 *  - expose one typed method per backend endpoint the UI uses;
 *  - convert every non-2xx response into an {@link ApiError};
 *  - attach the development demo identity header when an identity is selected.
 *
 * It contains NO React imports and NO authorization rules: it is a thin,
 * faithful adapter over the backend contract. All domain decisions belong to
 * the backend.
 */

import { ApiError, type ValidationDetail } from "./errors";
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
} from "./types";

/**
 * The interface the workspace hook depends on. The real implementation talks
 * HTTP; tests substitute a FakeApiClient with the same shape.
 */
export interface NotesApi {
  /** Whether demo identity selection is available, and the header to use. */
  getDemoIdentities(): Promise<DemoIdentityList>;
  /** Resolve the currently selected identity, or null if none is selected. */
  getCurrentUser(): Promise<CurrentUser | null>;
  /** Select a demo identity (by user id) for subsequent requests. */
  selectIdentity(userId: string | null): void;
  /** The currently selected demo identity id, if any. */
  selectedIdentity(): string | null;
  listUsers(): Promise<UserList>;
  listNotes(): Promise<NoteList>;
  createNote(input: NoteCreateInput): Promise<Note>;
  getNote(noteId: string): Promise<Note>;
  updateNote(noteId: string, input: NoteUpdateInput): Promise<Note>;
  deleteNote(noteId: string): Promise<void>;
  listPermissions(noteId: string): Promise<PermissionList>;
  grantPermission(noteId: string, input: PermissionGrantInput): Promise<Permission>;
  revokePermission(noteId: string, userId: string): Promise<void>;
}

/** The default backend origin when VITE_API_BASE_URL is not set. */
const DEFAULT_BASE_URL = "http://127.0.0.1:8000";

function resolveBaseUrl(): string {
  const configured = import.meta.env.VITE_API_BASE_URL;
  const base = (configured && configured.trim()) || DEFAULT_BASE_URL;
  return base.replace(/\/+$/, "");
}

/** Parse the backend error envelope into an ApiError. */
async function toApiError(response: Response): Promise<ApiError> {
  const status = response.status;
  let code: string | null = null;
  let message = response.statusText || `Request failed with status ${status}`;
  let requestId: string | null = response.headers.get("X-Request-ID");
  let details: ValidationDetail[] = [];

  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object") {
      const record = body as Record<string, unknown>;
      if (typeof record.request_id === "string") {
        requestId = record.request_id;
      }
      const error = record.error;
      if (error && typeof error === "object") {
        const errorRecord = error as Record<string, unknown>;
        if (typeof errorRecord.code === "string") code = errorRecord.code;
        if (typeof errorRecord.message === "string") message = errorRecord.message;
        if (Array.isArray(errorRecord.details)) {
          details = errorRecord.details.flatMap((detail) => {
            if (!detail || typeof detail !== "object") return [];
            const d = detail as Record<string, unknown>;
            const loc = Array.isArray(d.loc) ? d.loc.map((part) => String(part)) : [];
            return [
              {
                loc,
                msg: typeof d.msg === "string" ? d.msg : "",
                type: typeof d.type === "string" ? d.type : "",
              },
            ];
          });
        }
      }
    }
  } catch {
    // Non-JSON body: keep the status-derived defaults.
  }

  return new ApiError({ message, status, code, requestId, details });
}

export class HttpNotesApi implements NotesApi {
  private readonly baseUrl: string;
  private demoUserId: string | null;

  constructor(baseUrl: string = resolveBaseUrl()) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.demoUserId = null;
  }

  selectIdentity(userId: string | null): void {
    this.demoUserId = userId;
  }

  selectedIdentity(): string | null {
    return this.demoUserId;
  }

  private headers(): Record<string, string> {
    const headers: Record<string, string> = { Accept: "application/json" };
    if (this.demoUserId) {
      headers["X-Demo-User-ID"] = this.demoUserId;
    }
    return headers;
  }

  private async request<T>(
    method: string,
    path: string,
    options: { body?: unknown; requireAuth?: boolean } = {},
  ): Promise<T> {
    const requireAuth = options.requireAuth ?? true;
    if (requireAuth && !this.demoUserId) {
      // No identity selected: this is an authentication failure, not a network
      // one. Represent it consistently so the UI can prompt for a user.
      throw new ApiError({
        message: "No demo identity selected",
        status: 401,
        code: "unauthenticated",
      });
    }

    const headers = this.headers();
    let body: string | undefined;
    if (options.body !== undefined) {
      headers["Content-Type"] = "application/json";
      body = JSON.stringify(options.body);
    }

    let response: Response;
    try {
      response = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        ...(body !== undefined ? { body } : {}),
      });
    } catch (cause) {
      throw new ApiError({
        message:
          cause instanceof Error
            ? `Could not reach the backend: ${cause.message}`
            : "Could not reach the backend",
        status: 0,
      });
    }

    if (!response.ok) {
      throw await toApiError(response);
    }

    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  async getDemoIdentities(): Promise<DemoIdentityList> {
    try {
      return await this.request<DemoIdentityList>("GET", "/api/v1/demo/identities", {
        requireAuth: false,
      });
    } catch (error) {
      // A 404 means demo mode is disabled (or production); surface that as an
      // explicit "disabled" result rather than an error.
      if (error instanceof ApiError && error.isNotFound) {
        return { enabled: false, header: "X-Demo-User-ID", items: [], meta: emptyMeta() };
      }
      throw error;
    }
  }

  async getCurrentUser(): Promise<CurrentUser | null> {
    if (!this.demoUserId) {
      return null;
    }
    return this.request<CurrentUser>("GET", "/api/v1/me");
  }

  listUsers(): Promise<UserList> {
    return this.request<UserList>("GET", "/api/v1/users");
  }

  listNotes(): Promise<NoteList> {
    return this.request<NoteList>("GET", "/api/v1/notes");
  }

  createNote(input: NoteCreateInput): Promise<Note> {
    return this.request<Note>("POST", "/api/v1/notes", { body: input });
  }

  getNote(noteId: string): Promise<Note> {
    return this.request<Note>("GET", `/api/v1/notes/${encodeURIComponent(noteId)}`);
  }

  updateNote(noteId: string, input: NoteUpdateInput): Promise<Note> {
    return this.request<Note>("PUT", `/api/v1/notes/${encodeURIComponent(noteId)}`, {
      body: input,
    });
  }

  deleteNote(noteId: string): Promise<void> {
    return this.request<void>("DELETE", `/api/v1/notes/${encodeURIComponent(noteId)}`);
  }

  listPermissions(noteId: string): Promise<PermissionList> {
    return this.request<PermissionList>(
      "GET",
      `/api/v1/notes/${encodeURIComponent(noteId)}/permissions`,
    );
  }

  grantPermission(noteId: string, input: PermissionGrantInput): Promise<Permission> {
    return this.request<Permission>(
      "POST",
      `/api/v1/notes/${encodeURIComponent(noteId)}/permissions`,
      { body: input },
    );
  }

  revokePermission(noteId: string, userId: string): Promise<void> {
    return this.request<void>(
      "DELETE",
      `/api/v1/notes/${encodeURIComponent(noteId)}/permissions/${encodeURIComponent(userId)}`,
    );
  }
}

function emptyMeta(): { total: number; limit: number; offset: number } {
  return { total: 0, limit: 0, offset: 0 };
}
