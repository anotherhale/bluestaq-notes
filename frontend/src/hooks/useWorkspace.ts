/**
 * useWorkspace — the single application layer.
 *
 * This hook owns all state and is the only place (besides the API client it is
 * given) that talks to the backend. Components are purely presentational: they
 * receive data and callbacks through props and never call fetch or know a URL.
 *
 * It deliberately contains NO authorization logic. It may *hide* controls the
 * backend would reject (for usability), but every action is still enforced by
 * the backend, and backend errors are surfaced verbatim.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { NotesApi } from "../api/client";
import { ApiError, isApiError } from "../api/errors";
import type {
  CurrentUser,
  DemoIdentity,
  Note,
  NoteSummary,
  Permission,
  PermissionLevel,
  User,
} from "../api/types";

export type WorkspaceView = "mine" | "shared";

export interface NoteDraft {
  title: string;
  content: string;
}

export interface ConflictState {
  message: string;
}

export interface Workspace {
  // Demo identity
  demoEnabled: boolean;
  demoHeader: string;
  identities: DemoIdentity[];
  currentUser: CurrentUser | null;

  // Notes
  view: WorkspaceView;
  notes: NoteSummary[];
  selectedNoteId: string | null;
  selectedNote: Note | null;
  draft: NoteDraft;
  isDirty: boolean;

  // Sharing (owner view)
  permissions: Permission[];
  shareCandidates: User[];

  // Loading / error / conflict
  loadingIdentities: boolean;
  loadingNotes: boolean;
  loadingNote: boolean;
  saving: boolean;
  deleting: boolean;
  sharing: boolean;
  error: ApiError | null;
  conflict: ConflictState | null;
  notice: string | null;

  // Actions
  selectIdentity: (userId: string) => void;
  refresh: () => void;
  setView: (view: WorkspaceView) => void;
  selectNote: (noteId: string) => void;
  clearSelection: () => void;
  updateDraft: (patch: Partial<NoteDraft>) => void;
  createNote: (input: NoteDraft) => Promise<boolean>;
  saveNote: () => Promise<boolean>;
  deleteNote: () => Promise<boolean>;
  reloadSelectedNote: () => Promise<void>;
  grantPermission: (userId: string, level: PermissionLevel) => Promise<boolean>;
  revokePermission: (userId: string) => Promise<boolean>;
  dismissError: () => void;
}

function toApiError(error: unknown): ApiError {
  if (isApiError(error)) return error;
  return new ApiError({
    message: error instanceof Error ? error.message : "Unexpected error",
    status: 0,
  });
}

export function useWorkspace(api: NotesApi): Workspace {
  const [demoEnabled, setDemoEnabled] = useState(false);
  const [demoHeader, setDemoHeader] = useState("X-Demo-User-ID");
  const [identities, setIdentities] = useState<DemoIdentity[]>([]);
  const [currentUser, setCurrentUser] = useState<CurrentUser | null>(null);

  const [view, setViewState] = useState<WorkspaceView>("mine");
  const [notes, setNotes] = useState<NoteSummary[]>([]);
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [selectedNote, setSelectedNote] = useState<Note | null>(null);
  const [draft, setDraft] = useState<NoteDraft>({ title: "", content: "" });

  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [shareCandidates, setShareCandidates] = useState<User[]>([]);

  const [loadingIdentities, setLoadingIdentities] = useState(true);
  const [loadingNotes, setLoadingNotes] = useState(false);
  const [loadingNote, setLoadingNote] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [sharing, setSharing] = useState(false);

  const [error, setError] = useState<ApiError | null>(null);
  const [conflict, setConflict] = useState<ConflictState | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Guards against duplicate submissions from repeated clicks: the disabled
  // state is the UI affordance, this ref is the correctness backstop.
  const busyRef = useRef(false);

  const isDirty = useMemo(() => {
    if (!selectedNote) return draft.title.trim().length > 0 || draft.content.length > 0;
    return draft.title !== selectedNote.title || draft.content !== selectedNote.content;
  }, [selectedNote, draft]);

  const loadIdentities = useCallback(async () => {
    setLoadingIdentities(true);
    try {
      const result = await api.getDemoIdentities();
      setDemoEnabled(result.enabled);
      setDemoHeader(result.header);
      setIdentities(result.items);
      if (result.enabled && result.items.length > 0) {
        const first = result.items[0];
        if (first && api.selectedIdentity() === null) {
          api.selectIdentity(first.id);
        }
      }
    } catch (caught) {
      setError(toApiError(caught));
    } finally {
      setLoadingIdentities(false);
    }
  }, [api]);

  const loadCurrentUser = useCallback(async () => {
    try {
      const user = await api.getCurrentUser();
      setCurrentUser(user);
    } catch (caught) {
      setCurrentUser(null);
      setError(toApiError(caught));
    }
  }, [api]);

  const loadNotes = useCallback(async () => {
    if (!api.selectedIdentity()) {
      setNotes([]);
      return;
    }
    setLoadingNotes(true);
    try {
      const result = await api.listNotes();
      setNotes(result.items);
    } catch (caught) {
      setNotes([]);
      setError(toApiError(caught));
    } finally {
      setLoadingNotes(false);
    }
  }, [api]);

  const loadPermissions = useCallback(
    async (noteId: string) => {
      try {
        const result = await api.listPermissions(noteId);
        setPermissions(result.items);
      } catch (caught) {
        const apiError = toApiError(caught);
        // 403/404 simply means "not the owner": no permission list to show.
        if (!apiError.isForbidden && !apiError.isNotFound) {
          setError(apiError);
        }
        setPermissions([]);
      }
    },
    [api],
  );

  const loadNote = useCallback(
    async (noteId: string) => {
      setLoadingNote(true);
      setError(null);
      try {
        const note = await api.getNote(noteId);
        setSelectedNote(note);
        setSelectedNoteId(note.id);
        setDraft({ title: note.title, content: note.content });
        setConflict(null);
        // Only the owner may list permissions; others would receive 403/404.
        if (currentUser && note.owner_id === currentUser.id) {
          await loadPermissions(note.id);
        } else {
          setPermissions([]);
        }
      } catch (caught) {
        const apiError = toApiError(caught);
        // A note that vanished or became inaccessible: drop the selection.
        if (apiError.isNotFound || apiError.isForbidden) {
          setSelectedNote(null);
          setSelectedNoteId(null);
          setNotice("That note is no longer available to you.");
        } else {
          setError(apiError);
        }
      } finally {
        setLoadingNote(false);
      }
    },
    [api, currentUser, loadPermissions],
  );

  const loadShareCandidates = useCallback(async () => {
    try {
      const result = await api.listUsers();
      setShareCandidates(result.items);
    } catch (caught) {
      setError(toApiError(caught));
    }
  }, [api]);

  // Initial bootstrap: discover demo identities and the current user.
  useEffect(() => {
    void loadIdentities();
  }, [loadIdentities]);

  useEffect(() => {
    if (!loadingIdentities) {
      void loadCurrentUser();
    }
  }, [loadingIdentities, loadCurrentUser]);

  useEffect(() => {
    if (currentUser) {
      void loadNotes();
      void loadShareCandidates();
    }
  }, [currentUser, loadNotes, loadShareCandidates]);

  const selectIdentity = useCallback(
    (userId: string) => {
      // Clear ALL user-specific state so nothing from the previous identity can
      // linger on screen.
      api.selectIdentity(userId);
      setSelectedNote(null);
      setSelectedNoteId(null);
      setDraft({ title: "", content: "" });
      setPermissions([]);
      setConflict(null);
      setError(null);
      setNotice(null);
      setNotes([]);
      setCurrentUser(null);
      void loadCurrentUser();
    },
    [api, loadCurrentUser],
  );

  const refresh = useCallback(() => {
    void loadCurrentUser();
    void loadNotes();
    if (selectedNoteId) {
      void loadNote(selectedNoteId);
    }
  }, [loadCurrentUser, loadNotes, loadNote, selectedNoteId]);

  const setView = useCallback((next: WorkspaceView) => {
    setViewState(next);
  }, []);

  const selectNote = useCallback(
    (noteId: string) => {
      void loadNote(noteId);
    },
    [loadNote],
  );

  const clearSelection = useCallback(() => {
    setSelectedNote(null);
    setSelectedNoteId(null);
    setDraft({ title: "", content: "" });
    setPermissions([]);
    setConflict(null);
  }, []);

  const updateDraft = useCallback((patch: Partial<NoteDraft>) => {
    setDraft((prev) => ({ ...prev, ...patch }));
  }, []);

  const createNote = useCallback(
    async (input: NoteDraft): Promise<boolean> => {
      if (busyRef.current) return false;
      busyRef.current = true;
      setSaving(true);
      setError(null);
      setConflict(null);
      try {
        const created = await api.createNote({ title: input.title, content: input.content });
        await loadNotes();
        await loadNote(created.id);
        setNotice("Note created.");
        return true;
      } catch (caught) {
        setError(toApiError(caught));
        return false;
      } finally {
        setSaving(false);
        busyRef.current = false;
      }
    },
    [api, loadNotes, loadNote],
  );

  const saveNote = useCallback(async (): Promise<boolean> => {
    if (!selectedNote || busyRef.current) return false;
    busyRef.current = true;
    setSaving(true);
    setError(null);
    setConflict(null);
    try {
      // Send the version we loaded: the backend rejects a stale write with 409.
      const updated = await api.updateNote(selectedNote.id, {
        title: draft.title,
        content: draft.content,
        version: selectedNote.version,
      });
      setSelectedNote(updated);
      setDraft({ title: updated.title, content: updated.content });
      setNotice("Changes saved.");
      await loadNotes();
      return true;
    } catch (caught) {
      const apiError = toApiError(caught);
      if (apiError.isConflict) {
        // Preserve the user's unsaved content; offer an explicit reload.
        setConflict({ message: apiError.message });
      } else {
        setError(apiError);
      }
      return false;
    } finally {
      setSaving(false);
      busyRef.current = false;
    }
  }, [api, selectedNote, draft, loadNotes]);

  const deleteNote = useCallback(async (): Promise<boolean> => {
    if (!selectedNote || busyRef.current) return false;
    busyRef.current = true;
    setDeleting(true);
    setError(null);
    try {
      await api.deleteNote(selectedNote.id);
      clearSelection();
      await loadNotes();
      setNotice("Note deleted.");
      return true;
    } catch (caught) {
      setError(toApiError(caught));
      return false;
    } finally {
      setDeleting(false);
      busyRef.current = false;
    }
  }, [api, selectedNote, clearSelection, loadNotes]);

  const reloadSelectedNote = useCallback(async () => {
    if (!selectedNoteId) return;
    setConflict(null);
    await loadNote(selectedNoteId);
  }, [selectedNoteId, loadNote]);

  const grantPermission = useCallback(
    async (userId: string, level: PermissionLevel): Promise<boolean> => {
      if (!selectedNote || busyRef.current) return false;
      busyRef.current = true;
      setSharing(true);
      setError(null);
      try {
        await api.grantPermission(selectedNote.id, { user_id: userId, permission: level });
        await loadPermissions(selectedNote.id);
        setNotice("Access updated.");
        return true;
      } catch (caught) {
        setError(toApiError(caught));
        return false;
      } finally {
        setSharing(false);
        busyRef.current = false;
      }
    },
    [api, selectedNote, loadPermissions],
  );

  const revokePermission = useCallback(
    async (userId: string): Promise<boolean> => {
      if (!selectedNote || busyRef.current) return false;
      busyRef.current = true;
      setSharing(true);
      setError(null);
      try {
        await api.revokePermission(selectedNote.id, userId);
        await loadPermissions(selectedNote.id);
        setNotice("Access revoked.");
        return true;
      } catch (caught) {
        setError(toApiError(caught));
        return false;
      } finally {
        setSharing(false);
        busyRef.current = false;
      }
    },
    [api, selectedNote, loadPermissions],
  );

  const dismissError = useCallback(() => setError(null), []);

  // Auto-dismiss transient notices.
  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(null), 4000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  return {
    demoEnabled,
    demoHeader,
    identities,
    currentUser,
    view,
    notes,
    selectedNoteId,
    selectedNote,
    draft,
    isDirty,
    permissions,
    shareCandidates,
    loadingIdentities,
    loadingNotes,
    loadingNote,
    saving,
    deleting,
    sharing,
    error,
    conflict,
    notice,
    selectIdentity,
    refresh,
    setView,
    selectNote,
    clearSelection,
    updateDraft,
    createNote,
    saveNote,
    deleteNote,
    reloadSelectedNote,
    grantPermission,
    revokePermission,
    dismissError,
  };
}
