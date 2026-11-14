import { useEffect, useRef, useState } from "react";

import type { NoteSummary } from "../api/types";
import { noteDisplayTitle } from "../lib/noteDisplayTitle";
import { formatRelative } from "../lib/formatDate";

export interface NoteRowActions {
  onOpen: (noteId: string) => void;
  onToggleStar: (noteId: string, starred: boolean) => void;
  onMove: (noteId: string) => void;
  onShare: (noteId: string) => void;
  onDelete: (noteId: string) => void;
  onRename: (noteId: string) => void;
}

interface NoteRowProps {
  note: NoteSummary;
  selected: boolean;
  /** True when the current user owns the note. */
  isOwner: boolean;
  /** True when the current user may edit (owner or WRITE collaborator). */
  canEdit: boolean;
  actions: NoteRowActions;
}

/**
 * A single note row in the list.
 *
 * It shows whether the note is owned or shared, whether it is starred, and
 * offers contextual actions. Destructive/organisational actions are only shown
 * to the owner; the backend enforces this regardless of what is rendered.
 */
export function NoteRow({ note, selected, isOwner, canEdit, actions }: NoteRowProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const onDocClick = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocClick);
    return () => document.removeEventListener("mousedown", onDocClick);
  }, [menuOpen]);

  const run = (fn: () => void) => {
    setMenuOpen(false);
    fn();
  };

  return (
    <li className={`note-row${selected ? " note-row--selected" : ""}`}>
      <button
        type="button"
        className="note-row__main"
        onClick={() => actions.onOpen(note.id)}
        aria-current={selected ? "true" : undefined}
      >
        <span className="note-row__icon" aria-hidden="true">
          {isOwner ? "📄" : "👥"}
        </span>
        <span className="note-row__body">
          <span className="note-row__title">{noteDisplayTitle(note.title)}</span>
          <span className="note-row__meta">
            <span
              className={`badge badge--${isOwner ? "owner" : "shared"}`}
              title={isOwner ? "You own this note" : "Shared with you"}
            >
              {isOwner ? "Owned" : "Shared"}
            </span>
            <span className="note-row__updated">
              v{note.version} · {formatRelative(note.updated_at)}
            </span>
          </span>
        </span>
      </button>

      <span className="note-row__controls">
        <button
          type="button"
          className={`note-row__star${note.is_starred ? " note-row__star--on" : ""}`}
          aria-label={note.is_starred ? "Unstar note" : "Star note"}
          aria-pressed={note.is_starred}
          onClick={() => actions.onToggleStar(note.id, !note.is_starred)}
        >
          {note.is_starred ? "★" : "☆"}
        </button>

        <div className="note-row__menu" ref={menuRef}>
          <button
            type="button"
            className="note-row__menu-button"
            aria-label="Note actions"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            ⋮
          </button>
          {menuOpen ? (
            <div className="menu" role="menu">
              <button
                type="button"
                role="menuitem"
                className="menu__item"
                onClick={() => run(() => actions.onOpen(note.id))}
              >
                Open
              </button>
              {canEdit ? (
                <button
                  type="button"
                  role="menuitem"
                  className="menu__item"
                  onClick={() => run(() => actions.onRename(note.id))}
                >
                  Rename
                </button>
              ) : null}
              <button
                type="button"
                role="menuitem"
                className="menu__item"
                onClick={() => run(() => actions.onToggleStar(note.id, !note.is_starred))}
              >
                {note.is_starred ? "Remove star" : "Add star"}
              </button>
              {isOwner ? (
                <button
                  type="button"
                  role="menuitem"
                  className="menu__item"
                  onClick={() => run(() => actions.onMove(note.id))}
                >
                  Move to folder…
                </button>
              ) : null}
              {isOwner ? (
                <button
                  type="button"
                  role="menuitem"
                  className="menu__item"
                  onClick={() => run(() => actions.onShare(note.id))}
                >
                  Share…
                </button>
              ) : null}
              {isOwner ? (
                <button
                  type="button"
                  role="menuitem"
                  className="menu__item menu__item--danger"
                  onClick={() => run(() => actions.onDelete(note.id))}
                >
                  Delete
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      </span>
    </li>
  );
}
