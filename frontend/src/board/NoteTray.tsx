import { useDraggable } from "@dnd-kit/core";

import { noteDisplayTitle } from "../lib/noteDisplayTitle";
import { cardPreview } from "./layout";
import type { BoardNote, BoardNoteDetail } from "./types";

interface TrayItemProps {
  note: BoardNoteDetail;
  onBoard: boolean;
  onAdd: () => void;
}

/**
 * One draggable note in the tray.
 *
 * Dragging it onto the canvas places a card; clicking "Add" places one at the
 * next free slot without a drag (a keyboard/pointer-free path).
 */
function TrayItem({ note, onBoard, onAdd }: TrayItemProps) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({
    id: `tray:${note.id}`,
    data: { kind: "tray", noteId: note.id },
    disabled: onBoard,
  });

  return (
    <li
      ref={setNodeRef}
      className={`tray__item${onBoard ? " tray__item--on-board" : ""}${isDragging ? " tray__item--dragging" : ""}`}
      data-note-id={note.id}
    >
      <button
        type="button"
        className="tray__grab"
        aria-label={`Drag note ${note.title} onto the board`}
        disabled={onBoard}
        {...attributes}
        {...listeners}
      >
        <span className="tray__title">{noteDisplayTitle(note.title)}</span>
        <span className="tray__preview">{cardPreview(note, 80) || "Empty note"}</span>
      </button>
      <button
        type="button"
        className="tray__add"
        aria-label={onBoard ? `${note.title} is already on the board` : `Add ${note.title} to board`}
        disabled={onBoard}
        onClick={onAdd}
      >
        {onBoard ? "On board" : "Add"}
      </button>
    </li>
  );
}

export interface NoteTrayProps {
  notes: readonly BoardNote[];
  /** Full-note lookup for previews (content is not in the summary). */
  noteDetail: (noteId: string) => BoardNoteDetail | undefined;
  isOnBoard: (noteId: string) => boolean;
  onAddNote: (noteId: string) => void;
  /** Optional folder filter; when set, only that folder's notes are shown. */
  folderFilter?: string | null;
}

/**
 * The note tray: every note the current user can read, ready to be dragged onto
 * the board. It renders workspace notes — it never fetches and never
 * duplicates note state.
 */
export function NoteTray({
  notes,
  noteDetail,
  isOnBoard,
  onAddNote,
  folderFilter = null,
}: NoteTrayProps) {
  const visible = folderFilter
    ? notes.filter((note) => note.folder_id === folderFilter)
    : notes;

  return (
    <aside className="tray" aria-label="Note tray">
      <h3 className="tray__heading">Notes</h3>
      {visible.length === 0 ? (
        <p className="tray__empty">No notes available to place.</p>
      ) : (
        <ul className="tray__list">
          {visible.map((note) => {
            const detail: BoardNoteDetail = { ...note, ...noteDetail(note.id) };
            return (
              <TrayItem
                key={note.id}
                note={detail}
                onBoard={isOnBoard(note.id)}
                onAdd={() => onAddNote(note.id)}
              />
            );
          })}
        </ul>
      )}
    </aside>
  );
}
