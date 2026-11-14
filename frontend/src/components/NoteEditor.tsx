import { useState } from "react";

import type { Note } from "../api/types";
import { formatDate } from "../lib/formatDate";
import type { ConflictState, NoteDraft } from "../hooks/useWorkspace";
import { Banner } from "./Banner";
import { RichTextEditor } from "./RichTextEditor";

interface NoteEditorProps {
  note: Note;
  draft: NoteDraft;
  /** True when the current user owns the note. */
  isOwner: boolean;
  /** True when the current user may edit the content (owner or WRITE). */
  canEdit: boolean;
  /** True when the current user may star (any reader). */
  canStar: boolean;
  isDirty: boolean;
  saving: boolean;
  deleting: boolean;
  conflict: ConflictState | null;
  /** The title of the folder this note lives in, for the location label. */
  folderName: string | null;
  onDraftChange: (patch: Partial<NoteDraft>) => void;
  onSave: () => Promise<boolean>;
  onDelete: () => Promise<boolean>;
  onReload: () => Promise<void>;
  onShare: () => void;
  onMove: () => void;
  onToggleStar: () => void;
  onClose: () => void;
}

/**
 * The note editor: title, formatting toolbar, writing surface and status line.
 *
 * Capability model: `canEdit` comes from the backend's `caller_permission`
 * (owner => write; collaborator => read or write). We hide controls the backend
 * would reject, but never pretend to have enforced anything: a rejected write is
 * still surfaced verbatim from the API.
 */
export function NoteEditor({
  note,
  draft,
  isOwner,
  canEdit,
  canStar,
  isDirty,
  saving,
  deleting,
  conflict,
  folderName,
  onDraftChange,
  onSave,
  onDelete,
  onReload,
  onShare,
  onMove,
  onToggleStar,
  onClose,
}: NoteEditorProps) {
  const [confirmDelete, setConfirmDelete] = useState(false);

  return (
    <div className="editor">
      <div className="editor__toolbar">
        <div className="editor__identity">
          <span className={`badge badge--${isOwner ? "owner" : "shared"}`}>
            {isOwner ? "Owned by you" : "Shared with you"}
          </span>
          <span className="editor__location" title="Location">
            {folderName ?? "My Drive"}
          </span>
          <span className="editor__version" title="Optimistic concurrency version">
            v{note.version}
          </span>
        </div>
        <div className="editor__actions">
          {canStar ? (
            <button
              type="button"
              className={`button button--ghost${note.is_starred ? " button--starred" : ""}`}
              onClick={onToggleStar}
              aria-pressed={note.is_starred}
              aria-label={note.is_starred ? "Unstar note" : "Star note"}
            >
              {note.is_starred ? "★ Starred" : "☆ Star"}
            </button>
          ) : null}
          {isOwner ? (
            <button type="button" className="button button--ghost" onClick={onMove}>
              Move…
            </button>
          ) : null}
          {isOwner ? (
            <button type="button" className="button button--ghost" onClick={onShare}>
              Share…
            </button>
          ) : null}
          {isOwner ? (
            confirmDelete ? (
              <>
                <button
                  type="button"
                  className="button button--danger"
                  disabled={deleting}
                  onClick={() => void onDelete()}
                >
                  {deleting ? "Deleting…" : "Confirm delete"}
                </button>
                <button
                  type="button"
                  className="button button--ghost"
                  onClick={() => setConfirmDelete(false)}
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                type="button"
                className="button button--danger-outline"
                onClick={() => setConfirmDelete(true)}
              >
                Delete
              </button>
            )
          ) : null}
          <button type="button" className="button button--ghost" onClick={onClose}>
            Close
          </button>
        </div>
      </div>

      {conflict ? (
        <Banner tone="error">
          <strong>Someone else changed this note.</strong> {conflict.message} Your unsaved edits
          are kept below.{" "}
          <button type="button" className="link-button" onClick={() => void onReload()}>
            Load the latest version
          </button>
        </Banner>
      ) : null}

      <label className="field">
        <span className="field__label">Title</span>
        <input
          className="field__input field__input--title"
          type="text"
          value={draft.title}
          maxLength={200}
          disabled={!canEdit}
          onChange={(event) => onDraftChange({ title: event.target.value })}
          placeholder="Note title"
        />
      </label>

      {!canEdit ? (
        <p className="editor__readonly-hint">
          You have read-only access to this note. Ask the owner for write access to edit it.
        </p>
      ) : null}

      <RichTextEditor
        value={draft.content}
        readOnly={!canEdit}
        onChange={(html) => onDraftChange({ content: html })}
      />

      <div className="editor__footer">
        <span className="editor__timestamps">
          Created {formatDate(note.created_at)} · Updated {formatDate(note.updated_at)} · v
          {note.version}
        </span>
        <div className="editor__footer-actions">
          {isDirty ? <span className="editor__dirty">Unsaved changes</span> : null}
          {canEdit ? (
            <button
              type="button"
              className="button button--primary"
              disabled={saving || !isDirty}
              onClick={() => void onSave()}
            >
              {saving ? "Saving…" : "Save"}
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}
