import { useMemo, useState } from "react";

import type { Workspace } from "../hooks/useWorkspace";
import { Banner } from "./Banner";
import { CreateNoteForm } from "./CreateNoteForm";
import { EmptyState } from "./EmptyState";
import { NoteEditor } from "./NoteEditor";
import { NoteList } from "./NoteList";
import { ShareDialog } from "./ShareDialog";

interface NoteWorkspaceProps {
  workspace: Workspace;
}

/**
 * The two-column notes workspace: list on the left, editor on the right.
 *
 * "Owned" and "Shared with me" are computed from the current user's id against
 * each note's owner_id — the same rule the backend enforces. The editor's
 * capability model follows ownership; every action is still authorized by the
 * backend.
 */
export function NoteWorkspace({ workspace }: NoteWorkspaceProps) {
  const [creating, setCreating] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);

  const {
    view,
    notes,
    selectedNote,
    draft,
    isDirty,
    currentUser,
    permissions,
    shareCandidates,
    loadingNotes,
    loadingNote,
    saving,
    deleting,
    sharing,
    error,
    conflict,
    notice,
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
  } = workspace;

  const currentUserId = currentUser?.id ?? null;

  const ownedNotes = useMemo(
    () => notes.filter((note) => currentUserId !== null && note.owner_id === currentUserId),
    [notes, currentUserId],
  );
  const sharedNotes = useMemo(
    () => notes.filter((note) => currentUserId === null || note.owner_id !== currentUserId),
    [notes, currentUserId],
  );

  const visibleNotes = view === "mine" ? ownedNotes : sharedNotes;
  const isOwner =
    selectedNote !== null && currentUserId !== null && selectedNote.owner_id === currentUserId;

  return (
    <div className="workspace">
      <section className="workspace__list" aria-label="Notes">
        <div className="workspace__list-header">
          <div className="tabs" role="tablist" aria-label="Note scope">
            <button
              type="button"
              role="tab"
              aria-selected={view === "mine"}
              className={`tab${view === "mine" ? " tab--active" : ""}`}
              onClick={() => setView("mine")}
            >
              Owned ({ownedNotes.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "shared"}
              className={`tab${view === "shared" ? " tab--active" : ""}`}
              onClick={() => setView("shared")}
            >
              Shared with me ({sharedNotes.length})
            </button>
          </div>
          {view === "mine" && !creating ? (
            <button
              type="button"
              className="button button--primary"
              onClick={() => setCreating(true)}
            >
              New note
            </button>
          ) : null}
        </div>

        {creating ? (
          <CreateNoteForm
            busy={saving}
            onCreate={createNote}
            onCancel={() => setCreating(false)}
          />
        ) : null}

        {loadingNotes ? (
          <p className="workspace__loading">Loading notes…</p>
        ) : (
          <NoteList
            notes={visibleNotes}
            selectedId={selectedNote?.id ?? null}
            currentUserId={currentUserId}
            onSelect={selectNote}
          />
        )}
      </section>

      <section className="workspace__detail" aria-label="Note detail">
        {notice ? <Banner tone="notice">{notice}</Banner> : null}
        {error ? (
          <Banner tone="error" onDismiss={dismissError}>
            {error.message}
            {error.requestId ? (
              <span className="banner__meta"> (request {error.requestId})</span>
            ) : null}
          </Banner>
        ) : null}

        {loadingNote ? (
          <p className="workspace__loading">Loading note…</p>
        ) : selectedNote ? (
          <NoteEditor
            note={selectedNote}
            draft={draft}
            isOwner={isOwner}
            isDirty={isDirty}
            saving={saving}
            deleting={deleting}
            conflict={conflict}
            onDraftChange={updateDraft}
            onSave={saveNote}
            onDelete={deleteNote}
            onReload={reloadSelectedNote}
            onShare={() => setShareOpen(true)}
            onClose={clearSelection}
          />
        ) : (
          <EmptyState title="No note selected">
            Select a note from the list, or create a new one. Switch users above to see how
            sharing works from the other side.
          </EmptyState>
        )}
      </section>

      {selectedNote && isOwner ? (
        <ShareDialog
          open={shareOpen}
          noteTitle={selectedNote.title}
          ownerId={selectedNote.owner_id}
          permissions={permissions}
          candidates={shareCandidates}
          busy={sharing}
          onClose={() => setShareOpen(false)}
          onGrant={grantPermission}
          onRevoke={revokePermission}
        />
      ) : null}
    </div>
  );
}
