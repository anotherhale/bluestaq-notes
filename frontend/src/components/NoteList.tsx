import type { NoteSummary } from "../api/types";
import { noteDisplayTitle } from "../lib/noteDisplayTitle";
import { formatRelative } from "../lib/formatDate";

interface NoteListProps {
  notes: NoteSummary[];
  selectedId: string | null;
  currentUserId: string | null;
  onSelect: (noteId: string) => void;
}

/**
 * The note list. Each row shows whether the note is owned by the current user
 * or shared with them. It does not claim a permission *level*: the backend only
 * exposes the level to the owner, so inventing one would be a lie.
 */
export function NoteList({ notes, selectedId, currentUserId, onSelect }: NoteListProps) {
  if (notes.length === 0) {
    return (
      <p className="note-list__empty">
        No notes here yet. Create one, or switch users to see notes shared with someone else.
      </p>
    );
  }

  return (
    <ul className="note-list" role="list">
      {notes.map((note) => {
        const isOwner = currentUserId !== null && note.owner_id === currentUserId;
        const isSelected = note.id === selectedId;
        return (
          <li key={note.id}>
            <button
              type="button"
              className={`note-list__item${isSelected ? " note-list__item--selected" : ""}`}
              onClick={() => onSelect(note.id)}
              aria-current={isSelected ? "true" : undefined}
            >
              <span className="note-list__item-title">{noteDisplayTitle(note.title)}</span>
              <span className="note-list__item-meta">
                <span
                  className={`badge badge--${isOwner ? "owner" : "shared"}`}
                  title={isOwner ? "You own this note" : "Shared with you"}
                >
                  {isOwner ? "Owned" : "Shared"}
                </span>
                <span className="note-list__item-updated">
                  v{note.version} · {formatRelative(note.updated_at)}
                </span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
