import type { NoteSummary } from "../api/types";
import { NoteRow, type NoteRowActions } from "./NoteRow";

export type { NoteRowActions };

interface NoteListProps {
  notes: NoteSummary[];
  selectedId: string | null;
  currentUserId: string | null;
  emptyMessage: string;
  actions: NoteRowActions;
}

/**
 * The note list (the default "My Drive" style list view).
 *
 * Ownership is derived from the current user's id against each note's owner_id —
 * the same rule the backend enforces. Editability uses the note's own
 * `caller_permission`, which the backend computes for the caller only.
 */
export function NoteList({
  notes,
  selectedId,
  currentUserId,
  emptyMessage,
  actions,
}: NoteListProps) {
  if (notes.length === 0) {
    return <p className="note-list__empty">{emptyMessage}</p>;
  }

  return (
    <ul className="note-list" role="list">
      {notes.map((note) => {
        const isOwner = currentUserId !== null && note.owner_id === currentUserId;
        const canEdit = isOwner || note.caller_permission === "write";
        return (
          <NoteRow
            key={note.id}
            note={note}
            selected={note.id === selectedId}
            isOwner={isOwner}
            canEdit={canEdit}
            actions={actions}
          />
        );
      })}
    </ul>
  );
}
