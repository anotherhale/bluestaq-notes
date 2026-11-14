import { useDraggable } from "@dnd-kit/core";
import { useRef } from "react";

import { cardPreview } from "./layout";
import type { BoardCard, BoardNoteDetail, StickyColor } from "./types";
import { STICKY_COLORS } from "./types";

export interface StickyCardProps {
  card: BoardCard;
  note: BoardNoteDetail;
  width: number;
  height: number;
  /** The concrete shape after resolving `auto`. */
  shape: string;
  /** The effective color after resolving the default. */
  color: StickyColor;
  selected: boolean;
  onSelect: () => void;
  onOpenNote: () => void;
  onRemove: () => void;
  onCycleColor: () => void;
  onSetColor: (color: StickyColor) => void;
  onCycleShape: () => void;
  onTogglePin: () => void;
  onBringToFront: () => void;
  onResize: (size: number) => void;
}

/**
 * A single sticky-note card on the board canvas.
 *
 * The card is a dnd-kit draggable, but only the drag *handle* is the listener —
 * the body stays selectable and the controls stay clickable. Position is
 * applied by the parent (which owns the pan/zoom transform) so this component
 * stays unaware of the viewport.
 */
export function StickyCard({
  card,
  note,
  width,
  height,
  shape,
  color,
  selected,
  onSelect,
  onOpenNote,
  onRemove,
  onCycleColor,
  onSetColor,
  onCycleShape,
  onTogglePin,
  onBringToFront,
  onResize,
}: StickyCardProps) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: `card:${card.noteId}`,
    data: { kind: "card", noteId: card.noteId },
  });

  // dnd-kit reports the pointer delta in screen pixels; the parent divides by
  // the zoom before committing, so here we only need the raw transform.
  const dragStyle = transform
    ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` }
    : undefined;

  const resizeStart = useRef<{ pointerX: number; pointerY: number; size: number } | null>(null);

  const beginResize = (event: React.PointerEvent<HTMLButtonElement>) => {
    event.preventDefault();
    event.stopPropagation();
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
    resizeStart.current = { pointerX: event.clientX, pointerY: event.clientY, size: card.size };
  };

  const onResizeMove = (event: React.PointerEvent<HTMLButtonElement>) => {
    const start = resizeStart.current;
    if (!start) return;
    const delta = Math.max(event.clientX - start.pointerX, event.clientY - start.pointerY);
    // 180px of travel roughly doubles the card; the hook clamps to bounds.
    onResize(start.size + delta / 180);
  };

  const endResize = (event: React.PointerEvent<HTMLButtonElement>) => {
    resizeStart.current = null;
    (event.target as HTMLElement).releasePointerCapture?.(event.pointerId);
  };

  return (
    <article
      ref={setNodeRef}
      className={
        "sticky" +
        ` sticky--${shape}` +
        (selected ? " sticky--selected" : "") +
        (card.pinned ? " sticky--pinned" : "") +
        (isDragging ? " sticky--dragging" : "")
      }
      style={{
        left: card.x,
        top: card.y,
        width,
        height,
        zIndex: card.z,
        ...dragStyle,
      }}
      data-color={color}
      data-note-id={card.noteId}
      aria-label={`Note card: ${note.title}`}
      onPointerDown={onSelect}
      onFocus={onSelect}
    >
      <header className="sticky__bar">
        <button
          type="button"
          className="sticky__handle"
          aria-label={`Move card ${note.title}`}
          title="Drag to move"
          {...attributes}
          {...listeners}
        >
          <span aria-hidden="true">⠿</span>
        </button>
        <span className="sticky__title" title={note.title}>
          {note.title || "Untitled"}
        </span>
        <button
          type="button"
          className="sticky__icon"
          aria-label={card.pinned ? `Unpin ${note.title}` : `Pin ${note.title}`}
          aria-pressed={card.pinned}
          onClick={onTogglePin}
        >
          {card.pinned ? "📌" : "📍"}
        </button>
      </header>

      <button type="button" className="sticky__body" onClick={onOpenNote}>
        <span className="sticky__preview">{cardPreview(note) || "Empty note"}</span>
      </button>

      <div className="sticky__controls" role="group" aria-label={`Card controls for ${note.title}`}>
        <button
          type="button"
          className="sticky__icon"
          aria-label={`Change color of ${note.title}`}
          onClick={onCycleColor}
        >
          🎨
        </button>
        <button
          type="button"
          className="sticky__icon"
          aria-label={`Change shape of ${note.title}`}
          onClick={onCycleShape}
        >
          ▭
        </button>
        <button
          type="button"
          className="sticky__icon"
          aria-label={`Bring ${note.title} to front`}
          onClick={onBringToFront}
        >
          ⤒
        </button>
        <button
          type="button"
          className="sticky__icon sticky__icon--danger"
          aria-label={`Remove ${note.title} from board`}
          onClick={onRemove}
        >
          ✕
        </button>
      </div>

      <div className="sticky__swatches" role="group" aria-label={`Color swatches for ${note.title}`}>
        {STICKY_COLORS.map((option) => (
          <button
            key={option}
            type="button"
            className={`sticky__swatch${option === color ? " sticky__swatch--active" : ""}`}
            data-color={option}
            aria-label={`Set color ${option} on ${note.title}`}
            aria-pressed={option === color}
            onClick={() => onSetColor(option)}
          />
        ))}
      </div>

      <button
        type="button"
        className="sticky__resize"
        aria-label={`Resize card ${note.title}`}
        onPointerDown={beginResize}
        onPointerMove={onResizeMove}
        onPointerUp={endResize}
        onPointerCancel={endResize}
      />
    </article>
  );
}
