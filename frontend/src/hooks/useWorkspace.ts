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
 *
 * Navigation, search, filtering and sorting are all resolved server-side: the
 * hook sends the active view/folder/query/sort to the API and renders exactly
 * what comes back, so it can never display a note the caller may not read.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { NotesApi } from "../api/client";
import { ApiError, isApiError } from "../api/errors";
import type {
  CurrentUser,
  DemoIdentity,
  Folder,
  Note,
  NoteListQuery,
  NoteSort,
  NotesView,
  NoteSummary,
  Permission,
  PermissionLevel,
  User,
} from "../api/types";

/** The top-level navigation destinations shown in the sidebar. */
export type NavView = "drive" | "all" | "starred" | "shared";

export interface NoteDraft {
  title: string;
  content: string;
}

export interface ConflictState {
  message: string;
}

/** Delay before a keystroke becomes a search request. */
const SEARCH_DEBOUNCE_MS = 250;

export interface Workspace {
  // Demo identity
  demoEnabled: boolean;
  demoHeader: string;
  identities: DemoIdentity[];
  currentUser: CurrentUser | null;

  // Navigation and filters
  view: NavView;
  selectedFolderId: string | null;
  searchQuery: string;
  sort: NoteSort;
  folders: Folder[];

  // Notes
  notes: NoteSummary[];
  notesTotal: number;
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
  loadingFolders: boolean;
  saving: boolean;
  deleting: boolean;
  sharing: boolean;
  error: ApiError | null;
  conflict: ConflictState | null;
  notice: string | null;

  // Actions
  selectIdentity: (userId: string) => void;
  refresh: () => void;
  setView: (view: NavView) => void;
  selectFolder: (folderId: string | null) => void;
  setSearchQuery: (query: string) => void;
  setSort: (sort: NoteSort) => void;
  selectNote: (noteId: string) => void;
  clearSelection: () => void;
  updateDraft: (patch: Partial<NoteDraft>) => void;
  createNote: (input: NoteDraft, folderId?: string | null) => Promise<boolean>;
  saveNote: () => Promise<boolean>;
  deleteNote: (noteId?: string) => Promise<boolean>;
  reloadSelectedNote: () => Promise<void>;
  toggleStar: (noteId: string, starred: boolean) => Promise<boolean>;
  moveNote: (noteId: string, folderId: string | null) => Promise<boolean>;
  createFolder: (name: string) => Promise<boolean>;
  renameFolder: (folderId: string, name: string) => Promise<boolean>;
  deleteFolder: (folderId: string) => Promise<boolean>;
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

  const [view, setViewState] = useState<NavView>("drive");
  const [selectedFolderId, setSelectedFolderId] = useState<string | null>(null);
  const [searchQuery, setSearchQueryState] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [sort, setSortState] = useState<NoteSort>("-updated_at");
  const [folders, setFolders] = useState<Folder[]>([]);

  const [notes, setNotes] = useState<NoteSummary[]>([]);
  const [notesTotal, setNotesTotal] = useState(0);
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [selectedNote, setSelectedNote] = useState<Note | null>(null);
  const [draft, setDraft] = useState<NoteDraft>({ title: "", content: "" });

  const [permissions, setPermissions] = useState<Permission[]>([]);
  const [shareCandidates, setShareCandidates] = useState<User[]>([]);

  const [loadingIdentities, setLoadingIdentities] = useState(true);
  const [loadingNotes, setLoadingNotes] = useState(false);
  const [loadingNote, setLoadingNote] = useState(false);
  const [loadingFolders, setLoadingFolders] = useState(false);
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

  // -- bootstrap ----------------------------------------------------------

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

  // -- queries ------------------------------------------------------------

  const buildQuery = useCallback(
    (overrides: { view?: NavView; folderId?: string | null; query?: string; sort?: NoteSort } = {}) => {
      const activeView = overrides.view ?? view;
      const folderId = overrides.folderId !== undefined ? overrides.folderId : selectedFolderId;
      const q = overrides.query !== undefined ? overrides.query : debouncedQuery;
      const activeSort = overrides.sort ?? sort;

      const viewMap: Record<NavView, NotesView> = {
        drive: "owned",
        all: "all",
        starred: "all",
        shared: "shared",
      };
      // Build the query incrementally so absent filters are omitted entirely
      // (the client treats a missing key and an undefined value the same, but
      // building it this way keeps `exactOptionalPropertyTypes` happy).
      const result: NoteListQuery = { view: viewMap[activeView], sort: activeSort };
      const trimmed = q.trim();
      if (trimmed) result.q = trimmed;
      // A folder selection implies an owned-notes folder filter. Starred and
      // shared views ignore the folder selection (folders are per-owner).
      if (activeView === "drive" && folderId) result.folder_id = folderId;
      if (activeView === "starred") result.starred = true;
      return result;
    },
    [view, selectedFolderId, debouncedQuery, sort],
  );

  const loadNotes = useCallback(
    async (overrides: Parameters<typeof buildQuery>[0] = {}) => {
      if (!api.selectedIdentity()) {
        setNotes([]);
        setNotesTotal(0);
        return;
      }
      setLoadingNotes(true);
      try {
        const result = await api.listNotes(buildQuery(overrides));
        setNotes(result.items);
        setNotesTotal(result.meta.total);
      } catch (caught) {
        setNotes([]);
        setNotesTotal(0);
        setError(toApiError(caught));
      } finally {
        setLoadingNotes(false);
      }
    },
    [api, buildQuery],
  );

  const loadFolders = useCallback(async () => {
    if (!api.selectedIdentity()) {
      setFolders([]);
      return;
    }
    setLoadingFolders(true);
    try {
      const result = await api.listFolders();
      setFolders(result.items);
    } catch (caught) {
      setFolders([]);
      setError(toApiError(caught));
    } finally {
      setLoadingFolders(false);
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
      void loadFolders();
      void loadShareCandidates();
    }
    // loadNotes depends on the active filters; those changes trigger their own
    // effect below, so this effect intentionally keys on identity only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentUser, loadFolders, loadShareCandidates]);

  // Debounce the search box so a request is not sent on every keystroke.
  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(searchQuery), SEARCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [searchQuery]);

  // Re-query whenever the resolved filters change (and once the user exists).
  useEffect(() => {
    if (currentUser) {
      void loadNotes();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentUser, view, selectedFolderId, debouncedQuery, sort]);

  // -- identity -----------------------------------------------------------

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
      setNotesTotal(0);
      setFolders([]);
      setSelectedFolderId(null);
      setViewState("drive");
      setSearchQueryState("");
      setDebouncedQuery("");
      setCurrentUser(null);
      void loadCurrentUser();
    },
    [api, loadCurrentUser],
  );

  const refresh = useCallback(() => {
    void loadCurrentUser();
    void loadNotes();
    void loadFolders();
    if (selectedNoteId) {
      void loadNote(selectedNoteId);
    }
  }, [loadCurrentUser, loadNotes, loadFolders, loadNote, selectedNoteId]);

  // -- navigation ---------------------------------------------------------

  const setView = useCallback((next: NavView) => {
    setViewState(next);
    // Folder selection only applies to the "My Drive" destination.
    setSelectedFolderId(null);
  }, []);

  const selectFolder = useCallback((folderId: string | null) => {
    setSelectedFolderId(folderId);
    setViewState("drive");
  }, []);

  const setSearchQuery = useCallback((query: string) => {
    setSearchQueryState(query);
  }, []);

  const setSort = useCallback((next: NoteSort) => {
    setSortState(next);
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

  // -- mutations ----------------------------------------------------------

  const createNote = useCallback(
    async (input: NoteDraft, folderId?: string | null): Promise<boolean> => {
      if (busyRef.current) return false;
      busyRef.current = true;
      setSaving(true);
      setError(null);
      setConflict(null);
      try {
        const created = await api.createNote({
          title: input.title,
          content: input.content,
          folder_id: folderId ?? null,
        });
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

  const deleteNote = useCallback(
    async (noteId?: string): Promise<boolean> => {
      const targetId = noteId ?? selectedNote?.id;
      if (!targetId || busyRef.current) return false;
      busyRef.current = true;
      setDeleting(true);
      setError(null);
      try {
        await api.deleteNote(targetId);
        if (selectedNote?.id === targetId) {
          clearSelection();
        }
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
    },
    [api, selectedNote, clearSelection, loadNotes],
  );

  const reloadSelectedNote = useCallback(async () => {
    if (!selectedNoteId) return;
    setConflict(null);
    await loadNote(selectedNoteId);
  }, [selectedNoteId, loadNote]);

  const toggleStar = useCallback(
    async (noteId: string, starred: boolean): Promise<boolean> => {
      // Optimistic update: flip the star locally, roll back on failure.
      const snapshot = notes;
      setNotes((prev) =>
        prev.map((note) => (note.id === noteId ? { ...note, is_starred: starred } : note)),
      );
      if (selectedNote?.id === noteId) {
        setSelectedNote({ ...selectedNote, is_starred: starred });
      }
      try {
        const updated = starred ? await api.starNote(noteId) : await api.unstarNote(noteId);
        if (selectedNote?.id === noteId) {
          setSelectedNote(updated);
        }
        // A star change can add/remove the note from the Starred view.
        if (view === "starred") {
          await loadNotes();
        } else {
          setNotes((prev) =>
            prev.map((note) => (note.id === noteId ? { ...note, is_starred: updated.is_starred } : note)),
          );
        }
        return true;
      } catch (caught) {
        setNotes(snapshot); // roll back
        if (selectedNote?.id === noteId) {
          setSelectedNote(selectedNote);
        }
        setError(toApiError(caught));
        return false;
      }
    },
    [api, notes, selectedNote, view, loadNotes],
  );

  const moveNote = useCallback(
    async (noteId: string, folderId: string | null): Promise<boolean> => {
      if (busyRef.current) return false;
      busyRef.current = true;
      setError(null);
      try {
        const updated = await api.moveNote(noteId, { folder_id: folderId });
        if (selectedNote?.id === noteId) {
          setSelectedNote(updated);
        }
        // A move changes which folder view the note belongs to.
        await loadNotes();
        setNotice("Note moved.");
        return true;
      } catch (caught) {
        setError(toApiError(caught));
        return false;
      } finally {
        busyRef.current = false;
      }
    },
    [api, selectedNote, loadNotes],
  );

  const createFolder = useCallback(
    async (name: string): Promise<boolean> => {
      if (busyRef.current) return false;
      busyRef.current = true;
      setError(null);
      try {
        const folder = await api.createFolder({ name });
        await loadFolders();
        setSelectedFolderId(folder.id);
        setViewState("drive");
        setNotice("Folder created.");
        return true;
      } catch (caught) {
        setError(toApiError(caught));
        return false;
      } finally {
        busyRef.current = false;
      }
    },
    [api, loadFolders],
  );

  const renameFolder = useCallback(
    async (folderId: string, name: string): Promise<boolean> => {
      if (busyRef.current) return false;
      busyRef.current = true;
      setError(null);
      try {
        await api.renameFolder(folderId, { name });
        await loadFolders();
        setNotice("Folder renamed.");
        return true;
      } catch (caught) {
        setError(toApiError(caught));
        return false;
      } finally {
        busyRef.current = false;
      }
    },
    [api, loadFolders],
  );

  const deleteFolder = useCallback(
    async (folderId: string): Promise<boolean> => {
      if (busyRef.current) return false;
      busyRef.current = true;
      setError(null);
      try {
        await api.deleteFolder(folderId);
        if (selectedFolderId === folderId) {
          setSelectedFolderId(null);
        }
        await loadFolders();
        await loadNotes();
        setNotice("Folder deleted; its notes moved to My Drive.");
        return true;
      } catch (caught) {
        setError(toApiError(caught));
        return false;
      } finally {
        busyRef.current = false;
      }
    },
    [api, selectedFolderId, loadFolders, loadNotes],
  );

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
    selectedFolderId,
    searchQuery,
    sort,
    folders,
    notes,
    notesTotal,
    selectedNoteId,
    selectedNote,
    draft,
    isDirty,
    permissions,
    shareCandidates,
    loadingIdentities,
    loadingNotes,
    loadingNote,
    loadingFolders,
    saving,
    deleting,
    sharing,
    error,
    conflict,
    notice,
    selectIdentity,
    refresh,
    setView,
    selectFolder,
    setSearchQuery,
    setSort,
    selectNote,
    clearSelection,
    updateDraft,
    createNote,
    saveNote,
    deleteNote,
    reloadSelectedNote,
    toggleStar,
    moveNote,
    createFolder,
    renameFolder,
    deleteFolder,
    grantPermission,
    revokePermission,
    dismissError,
  };
}
