import { useState } from "react";

import type { Note } from "../api/types";
import { formatDate } from "../lib/formatDate";
import type { ConflictState, NoteDraft } from "../hooks/useWorkspace";
import { Banner } from "./Banner";

interface NoteEditorProps {
  note: Note;
  draft: NoteDraft;
  isOwner: boolean;
  isDirty: boolean;
  saving: boolean;
  deleting: boolean;
  conflict: ConflictState | null;
  onDraftChange: (patch: Partial<NoteDraft>) => void;
  onSave: () => Promise<boolean>;
  onDelete: () => Promise<boolean>;
  onReload: () => Promise<void>;
  onShare: () => void;
  onClose: () => void;
}

/**
 * The note editor.
 *
 * Capability model: the client only knows for certain whether it *owns* a note.
 * Owners may edit, share, and delete; non-owners see a read/edit form but no
 * share/delete controls. We do NOT claim a "read-only because you have READ
 * permission" state, because the API does not disclose a collaborator's level.
 * When a non-owner's edit is rejected, the backend's 403 is shown verbatim —
 * the client never pretends to have enforced anything itself.
 */
export function NoteEditor({
  note,
  draft,
  isOwner,
  isDirty,
  saving,
  deleting,
  conflict,
  onDraftChange,
  onSave,
  onDelete,
  onReload,
  onShare,
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
          <span className="editor__version" title="Optimistic concurrency version">
            v{note.version}
          </span>
        </div>
        <div className="editor__actions">
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
          <strong>Someone else changed this note.</strong> {conflict.message} Your unsaved
          edits are kept below.{" "}
          <button type="button" className="link-button" onClick={() => void onReload()}>
            Load the latest version
          </button>
        </Banner>
      ) : null}

      <label className="field">
        <span className="field__label">Title</span>
        <input
          className="field__input"
          type="text"
          value={draft.title}
          maxLength={200}
          onChange={(event) => onDraftChange({ title: event.target.value })}
          placeholder="Note title"
        />
      </label>

      <label className="field field--grow">
        <span className="field__label">Content</span>
        <textarea
          className="field__textarea"
          value={draft.content}
          rows={16}
          onChange={(event) => onDraftChange({ content: event.target.value })}
          placeholder="Write your note…"
        />
      </label>

      <div className="editor__footer">
        <span className="editor__timestamps">
          Created {formatDate(note.created_at)} · Updated {formatDate(note.updated_at)}
        </span>
        <div className="editor__footer-actions">
          {isDirty ? <span className="editor__dirty">Unsaved changes</span> : null}
          <button
            type="button"
            className="button button--primary"
            disabled={saving || !isDirty}
            onClick={() => void onSave()}
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>
    </div>
  );
}
