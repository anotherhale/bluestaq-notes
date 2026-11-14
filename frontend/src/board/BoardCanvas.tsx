import { useDroppable } from "@dnd-kit/core";
import type { RefObject } from "react";

import { StickyCard } from "./StickyCard";
import type { Board, BoardNoteDetail, StickyColor } from "./types";

export interface BoardCanvasProps {
  board: Board;
  /** Board-space -> pixel transform, applied to the inner layer. */
  pan: { x: number; y: number };
  zoom: number;
  /** The scroll viewport element; used for pointer -> board-space math. */
  viewportRef: RefObject<HTMLDivElement>;
  noteDetail: (noteId: string) => BoardNoteDetail | undefined;
  dimensionsFor: (noteId: string) => { width: number; height: number } | null;
  shapeFor: (noteId: string) => string;
  colorFor: (noteId: string) => StickyColor;
  selectedNoteId: string | null;
  onSelectCard: (noteId: string) => void;
  onOpenNote: (noteId: string) => void;
  onRemoveCard: (noteId: string) => void;
  onCycleColor: (noteId: string) => void;
  onSetColor: (noteId: string, color: StickyColor) => void;
  onCycleShape: (noteId: string) => void;
  onTogglePin: (noteId: string) => void;
  onBringToFront: (noteId: string) => void;
  onResize: (noteId: string, size: number) => void;
  /** Pointer handlers for background panning. */
  onBackgroundPointerDown: (event: React.PointerEvent<HTMLDivElement>) => void;
  onBackgroundPointerMove: (event: React.PointerEvent<HTMLDivElement>) => void;
  onBackgroundPointerUp: (event: React.PointerEvent<HTMLDivElement>) => void;
  onWheel: (event: React.WheelEvent<HTMLDivElement>) => void;
}

/**
 * The droppable board canvas.
 *
 * DEF-03: this component calls `useDroppable`, which resolves its context
 * through React context. It is therefore rendered as a *child* of the single
 * `DndContext` in `BoardView` — never above it. Registering the droppable
 * outside the provider fails silently (no drop, no error), so the nesting is
 * asserted by `BoardView.test.tsx`.
 *
 * Card positions are stored in board space; pan and zoom are applied to the
 * inner layer only, so a viewport change never rewrites stored coordinates.
 */
export function BoardCanvas({
  board,
  pan,
  zoom,
  viewportRef,
  noteDetail,
  dimensionsFor,
  shapeFor,
  colorFor,
  selectedNoteId,
  onSelectCard,
  onOpenNote,
  onRemoveCard,
  onCycleColor,
  onSetColor,
  onCycleShape,
  onTogglePin,
  onBringToFront,
  onResize,
  onBackgroundPointerDown,
  onBackgroundPointerMove,
  onBackgroundPointerUp,
  onWheel,
}: BoardCanvasProps) {
  const { setNodeRef, isOver } = useDroppable({ id: "board-canvas" });

  return (
    <div
      ref={viewportRef}
      className="canvas__viewport"
      onWheel={onWheel}
      onPointerDown={onBackgroundPointerDown}
      onPointerMove={onBackgroundPointerMove}
      onPointerUp={onBackgroundPointerUp}
      onPointerCancel={onBackgroundPointerUp}
      data-testid="board-viewport"
    >
      <div
        ref={setNodeRef}
        className={
          "canvas" +
          ` canvas--${board.surface}` +
          (isOver ? " canvas--drop-target" : "")
        }
        data-testid="board-canvas"
        data-surface={board.surface}
        data-drop-active={isOver ? "true" : "false"}
        style={{
          // A large fixed working area; panning moves the viewport over it.
          width: 4000,
          height: 3000,
          transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
          transformOrigin: "0 0",
        }}
      >
        {board.cards.length === 0 ? (
          <p className="canvas__hint" style={{ position: "absolute", left: 48, top: 40 }}>
            Drag a note from the tray onto the board.
          </p>
        ) : null}

        {board.cards.map((card) => {
          const note = noteDetail(card.noteId);
          const size = dimensionsFor(card.noteId) ?? { width: 180, height: 180 };
          const cardNote: BoardNoteDetail =
            note ?? {
              id: card.noteId,
              title: "Unavailable note",
              folder_id: null,
              created_at: "",
              updated_at: "",
            };
          return (
            <StickyCard
              key={card.noteId}
              card={card}
              note={cardNote}
              width={size.width}
              height={size.height}
              shape={shapeFor(card.noteId)}
              color={colorFor(card.noteId)}
              selected={selectedNoteId === card.noteId}
              onSelect={() => onSelectCard(card.noteId)}
              onOpenNote={() => onOpenNote(card.noteId)}
              onRemove={() => onRemoveCard(card.noteId)}
              onCycleColor={() => onCycleColor(card.noteId)}
              onSetColor={(color) => onSetColor(card.noteId, color)}
              onCycleShape={() => onCycleShape(card.noteId)}
              onTogglePin={() => onTogglePin(card.noteId)}
              onBringToFront={() => onBringToFront(card.noteId)}
              onResize={(value) => onResize(card.noteId, value)}
            />
          );
        })}
      </div>
    </div>
  );
}
