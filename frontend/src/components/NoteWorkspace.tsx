import { useMemo, useState } from "react";

import type { Folder } from "../api/types";
import type { NavView, Workspace } from "../hooks/useWorkspace";
import { Banner } from "./Banner";
import { Breadcrumbs } from "./Breadcrumbs";
import { ConfirmDialog } from "./ConfirmDialog";
import { CreateNoteForm } from "./CreateNoteForm";
import { EmptyState } from "./EmptyState";
import { FolderNameDialog } from "./FolderNameDialog";
import { MoveNoteDialog } from "./MoveNoteDialog";
import { NoteEditor } from "./NoteEditor";
import { NoteList, type NoteRowActions } from "./NoteList";
import { ShareDialog } from "./ShareDialog";
import { Sidebar } from "./Sidebar";

interface NoteWorkspaceProps {
  workspace: Workspace;
}

type DialogState =
  | { kind: "none" }
  | { kind: "create-folder" }
  | { kind: "rename-folder"; folder: Folder }
  | { kind: "delete-folder"; folder: Folder }
  | { kind: "move-note" }
  | { kind: "delete-note"; noteId: string };

const VIEW_TITLES: Record<NavView, string> = {
  drive: "My Drive",
  all: "All notes",
  starred: "Starred",
  shared: "Shared with me",
};

/**
 * The main workspace: sidebar, breadcrumbs, note list, and the editor.
 *
 * It holds only *presentation* state (which dialog is open, whether the inline
 * create form is showing). Every piece of domain state — notes, folders, the
 * active view, the current user — comes from the workspace hook, and every
 * action is a callback into it.
 */
export function NoteWorkspace({ workspace }: NoteWorkspaceProps) {
  const [creating, setCreating] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [dialog, setDialog] = useState<DialogState>({ kind: "none" });

  const {
    view,
    folders,
    selectedFolderId,
    notes,
    notesTotal,
    loadingNotes,
    loadingFolders,
    loadingNote,
    selectedNote,
    draft,
    isDirty,
    currentUser,
    permissions,
    shareCandidates,
    saving,
    deleting,
    sharing,
    searchQuery,
    error,
    conflict,
    notice,
    selectFolder,
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
  } = workspace;

  const currentUserId = currentUser?.id ?? null;
  const selectedFolder = useMemo(
    () => folders.find((folder) => folder.id === selectedFolderId) ?? null,
    [folders, selectedFolderId],
  );

  const selectedNoteFolderName = useMemo(() => {
    if (!selectedNote || !selectedNote.folder_id) return null;
    return folders.find((folder) => folder.id === selectedNote.folder_id)?.name ?? "Folder";
  }, [selectedNote, folders]);

  const isOwner =
    selectedNote !== null && currentUserId !== null && selectedNote.owner_id === currentUserId;
  const canEdit = selectedNote !== null && (isOwner || selectedNote.caller_permission === "write");

  const emptyMessage = useMemo(() => {
    if (searchQuery.trim()) return "No notes match your search.";
    switch (view) {
      case "starred":
        return "No starred notes yet. Star a note to find it here.";
      case "shared":
        return "Nothing has been shared with you yet.";
      case "all":
        return "You have no notes yet.";
      default:
        return selectedFolder
          ? `"${selectedFolder.name}" is empty.`
          : "Your drive is empty. Create a note to get started.";
    }
  }, [view, selectedFolder, searchQuery]);

  const startCreateNote = () => {
    setCreating(true);
  };

  const rowActions: NoteRowActions = {
    onOpen: (noteId) => selectNote(noteId),
    onToggleStar: (noteId, starred) => void toggleStar(noteId, starred),
    onMove: (noteId) => {
      selectNote(noteId);
      setDialog({ kind: "move-note" });
    },
    onShare: (noteId) => {
      selectNote(noteId);
      setShareOpen(true);
    },
    onDelete: (noteId) => setDialog({ kind: "delete-note", noteId }),
    onRename: (noteId) => selectNote(noteId),
  };

  return (
    <div className="workspace">
      <Sidebar
        view={view}
        selectedFolderId={selectedFolderId}
        folders={folders}
        loadingFolders={loadingFolders}
        onSelectView={(next) => {
          selectFolder(null);
          workspace.setView(next);
        }}
        onSelectFolder={selectFolder}
        onNewNote={startCreateNote}
        onNewFolder={() => setDialog({ kind: "create-folder" })}
        onRenameFolder={(folder) => setDialog({ kind: "rename-folder", folder })}
        onDeleteFolder={(folder) => setDialog({ kind: "delete-folder", folder })}
      />

      <section className="workspace__list" aria-label="Notes">
        <div className="workspace__list-header">
          <Breadcrumbs
            view={view}
            folderName={selectedFolder?.name ?? null}
            onNavigateRoot={() => selectFolder(null)}
          />
          <div className="workspace__list-title-row">
            <h2 className="workspace__view-title">
              {view === "drive" && selectedFolder ? selectedFolder.name : VIEW_TITLES[view]}
            </h2>
            {view === "drive" && !creating ? (
              <button
                type="button"
                className="button button--primary"
                onClick={startCreateNote}
              >
                New note
              </button>
            ) : null}
          </div>
        </div>

        {creating ? (
          <CreateNoteForm
            busy={saving}
            folderName={view === "drive" ? selectedFolder?.name ?? null : null}
            onCreate={(input) => createNote(input, view === "drive" ? selectedFolderId : null)}
            onCancel={() => setCreating(false)}
          />
        ) : null}

        {loadingNotes ? (
          <p className="workspace__loading">Loading notes…</p>
        ) : (
          <NoteList
            notes={notes}
            selectedId={selectedNote?.id ?? null}
            currentUserId={currentUserId}
            emptyMessage={emptyMessage}
            actions={rowActions}
          />
        )}
        {notesTotal > notes.length ? (
          <p className="workspace__pagination-hint">
            Showing {notes.length} of {notesTotal} notes.
          </p>
        ) : null}
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
            canEdit={canEdit}
            canStar
            isDirty={isDirty}
            saving={saving}
            deleting={deleting}
            conflict={conflict}
            folderName={selectedNoteFolderName}
            onDraftChange={updateDraft}
            onSave={saveNote}
            onDelete={() => deleteNote()}
            onReload={reloadSelectedNote}
            onShare={() => setShareOpen(true)}
            onMove={() => setDialog({ kind: "move-note" })}
            onToggleStar={() => void toggleStar(selectedNote.id, !selectedNote.is_starred)}
            onClose={clearSelection}
          />
        ) : (
          <EmptyState title="No note selected">
            Select a note from the list, or create a new one. Switch users above to see how sharing
            works from the other side.
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

      <FolderNameDialog
        open={dialog.kind === "create-folder"}
        mode="create"
        busy={saving}
        onSubmit={(name) => {
          void createFolder(name).then((ok) => {
            if (ok) setDialog({ kind: "none" });
          });
        }}
        onCancel={() => setDialog({ kind: "none" })}
      />

      <FolderNameDialog
        open={dialog.kind === "rename-folder"}
        mode="rename"
        initialName={dialog.kind === "rename-folder" ? dialog.folder.name : ""}
        busy={saving}
        onSubmit={(name) => {
          if (dialog.kind !== "rename-folder") return;
          const folderId = dialog.folder.id;
          void renameFolder(folderId, name).then((ok) => {
            if (ok) setDialog({ kind: "none" });
          });
        }}
        onCancel={() => setDialog({ kind: "none" })}
      />

      <ConfirmDialog
        open={dialog.kind === "delete-folder"}
        title="Delete folder"
        message={
          dialog.kind === "delete-folder"
            ? `Delete "${dialog.folder.name}"? Its notes will be moved to My Drive; they will not be deleted.`
            : ""
        }
        confirmLabel="Delete folder"
        busy={saving}
        onConfirm={() => {
          if (dialog.kind !== "delete-folder") return;
          const folderId = dialog.folder.id;
          void deleteFolder(folderId).then(() => setDialog({ kind: "none" }));
        }}
        onCancel={() => setDialog({ kind: "none" })}
      />

      <ConfirmDialog
        open={dialog.kind === "delete-note"}
        title="Delete note"
        message="This permanently deletes the note. This cannot be undone."
        confirmLabel="Delete note"
        busy={deleting}
        onConfirm={() => {
          if (dialog.kind !== "delete-note") return;
          const noteId = dialog.noteId;
          setDialog({ kind: "none" });
          void deleteNote(noteId);
        }}
        onCancel={() => setDialog({ kind: "none" })}
      />

      {selectedNote && isOwner ? (
        <MoveNoteDialog
          open={dialog.kind === "move-note"}
          noteTitle={selectedNote.title}
          currentFolderId={selectedNote.folder_id}
          folders={folders}
          busy={saving}
          onMove={(folderId) => {
            void moveNote(selectedNote.id, folderId).then((ok) => {
              if (ok) setDialog({ kind: "none" });
            });
          }}
          onCancel={() => setDialog({ kind: "none" })}
        />
      ) : null}
    </div>
  );
}
