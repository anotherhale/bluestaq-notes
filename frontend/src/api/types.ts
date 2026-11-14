/**
 * Wire types mirroring the backend's Pydantic schemas exactly.
 *
 * These describe the JSON on the wire — nothing more. They are derived from the
 * backend's actual `presentation/schemas.py` and OpenAPI document, not invented.
 * Keep them faithful: if the backend changes a field, this file changes.
 *
 * Timestamps are ISO-8601 strings in UTC (e.g. "2026-11-14T16:03:38.315289Z").
 */

/** A user as exposed for sharing (`UserResponse`). */
export interface User {
  id: string;
  email: string;
  display_name: string;
}

/** The authenticated caller (`CurrentUserResponse`). */
export interface CurrentUser extends User {
  status: "active" | "suspended" | "disabled";
}

/** A selectable development demo identity (`DemoIdentityResponse`). */
export type DemoIdentity = User;

/** `GET /api/v1/demo/identities` (`DemoIdentityListResponse`). */
export interface DemoIdentityList {
  enabled: boolean;
  /** The request header that selects an identity, e.g. "X-Demo-User-ID". */
  header: string;
  items: DemoIdentity[];
  meta: PageMeta;
}

/** A note without its content, as returned by the list endpoint. */
export interface NoteSummary {
  id: string;
  owner_id: string;
  title: string;
  version: number;
  created_at: string;
  updated_at: string;
}

/** A full note, as returned by create/get/update. */
export interface Note extends NoteSummary {
  content: string;
}

export type PermissionLevel = "read" | "write";

/** A permission grant (`PermissionResponse`). */
export interface Permission {
  user_id: string;
  permission: PermissionLevel;
  granted_by: string;
  created_at: string;
}

export interface PageMeta {
  total: number;
  limit: number;
  offset: number;
}

export interface NoteList {
  items: NoteSummary[];
  meta: PageMeta;
}

export interface UserList {
  items: User[];
  meta: PageMeta;
}

export interface PermissionList {
  items: Permission[];
  meta: PageMeta;
}

/** Request body for creating a note. */
export interface NoteCreateInput {
  title: string;
  content: string;
}

/** Request body for updating a note (includes the expected version). */
export interface NoteUpdateInput {
  title: string;
  content: string;
  version: number;
}

/** Request body for granting/upgrading a permission. */
export interface PermissionGrantInput {
  user_id: string;
  permission: PermissionLevel;
}
