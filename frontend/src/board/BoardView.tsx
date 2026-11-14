import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  rectIntersection,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import { useCallback, useEffect, useRef, useState } from "react";

import { ConfirmDialog } from "../components/ConfirmDialog";
import { EmptyState } from "../components/EmptyState";
import { FolderNameDialog } from "../components/FolderNameDialog";
import type { Folder } from "../api/types";
import { freeSlot } from "./layout";
import { BoardCanvas } from "./BoardCanvas";
import { NoteTray } from "./NoteTray";
import type { Boards } from "./useBoards";
import {
  BOARD_SURFACES,
  type ArrangeMode,
  type BoardNote,
  type BoardNoteDetail,
  type BoardSurface,
} from "./types";

export interface BoardViewProps {
  boards: Boards;
  /** Notes available to the user (from the workspace). */
  notes: readonly BoardNote[];
  noteDetail: (noteId: string) => BoardNoteDetail | undefined;
  /** Ask the workspace to load a note's content (for previews); idempotent. */
  requestNoteDetail: (noteId: string) => void;
  folders: Folder[];
  /** Open a note in the existing editor and switch back to the list view. */
  onOpenNote: (noteId: string) => void;
}

const ARRANGE_MODES: ArrangeMode[] = ["grid", "color", "folder", "recency"];
const ZOOM_STEPS = [0.5, 0.75, 1, 1.25, 1.5, 2];
const MIN_ZOOM = 0.5;
const MAX_ZOOM = 2;

/**
 * The whiteboard view.
 *
 * This component owns the single `DndContext` that spans BOTH the note tray and
 * the canvas. `BoardCanvas` (which calls `useDroppable`) is rendered inside
 * this provider — see DEF-03. The pan/zoom transform lives here because it is
 * what converts a screen-space drop into a board-space position.
 */
export function BoardView({
  boards,
  notes,
  noteDetail,
  requestNoteDetail,
  folders,
  onOpenNote,
}: BoardViewProps) {
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const [pan, setPan] = useState({ x: 24, y: 24 });
  const [zoom, setZoom] = useState(1);
  const [selectedNoteId, setSelectedNoteId] = useState<string | null>(null);
  const [dialog, setDialog] = useState<
    | { kind: "none" }
    | { kind: "create" }
    | { kind: "rename" }
    | { kind: "delete" }
  >({ kind: "none" });
  const panState = useRef<{ pointerX: number; pointerY: number; originX: number; originY: number } | null>(
    null,
  );

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 4 } }),
    useSensor(KeyboardSensor),
  );

  const { activeBoard } = boards;

  // Warm previews for every card once (the tray also warms its own notes).
  const cardNoteIds = activeBoard?.cards.map((card) => card.noteId).join(",") ?? "";
  useEffect(() => {
    if (!cardNoteIds) return;
    for (const noteId of cardNoteIds.split(",")) requestNoteDetail(noteId);
  }, [cardNoteIds, requestNoteDetail]);

  useEffect(() => {
    for (const note of notes) requestNoteDetail(note.id);
  }, [notes, requestNoteDetail]);

  // -- pan / zoom ---------------------------------------------------------

  const beginPan = (event: React.PointerEvent<HTMLDivElement>) => {
    // Only start a pan when the gesture begins on the background, not a card.
    if (event.target !== event.currentTarget && !(event.target as HTMLElement).dataset.canvas) {
      const isBackground = (event.target as HTMLElement).classList.contains("canvas");
      if (!isBackground) return;
    }
    panState.current = {
      pointerX: event.clientX,
      pointerY: event.clientY,
      originX: pan.x,
      originY: pan.y,
    };
  };

  const movePan = (event: React.PointerEvent<HTMLDivElement>) => {
    const start = panState.current;
    if (!start) return;
    setPan({
      x: start.originX + (event.clientX - start.pointerX),
      y: start.originY + (event.clientY - start.pointerY),
    });
  };

  const endPan = () => {
    panState.current = null;
  };

  const onWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    if (!event.ctrlKey && !event.metaKey) return;
    event.preventDefault();
    setZoom((current) => clampZoom(current - event.deltaY / 500));
  };

  const zoomTo = (next: number) => setZoom(clampZoom(next));

  // -- drop handling ------------------------------------------------------

  const onDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over, delta } = event;
      const data = active.data.current as { kind?: string; noteId?: string } | undefined;
      if (!data?.noteId || !activeBoard) return;

      if (data.kind === "tray") {
        if (over?.id !== "board-canvas") return;
        const point = boardPointFromActive(active.rect.current.translated, viewportRef.current, pan, zoom);
        boards.addNote(data.noteId, point.x, point.y);
        return;
      }

      if (data.kind === "card") {
        const card = boards.cardFor(data.noteId);
        if (!card) return;
        // Screen delta -> board delta: divide by the zoom scale.
        boards.moveCard(data.noteId, card.x + delta.x / zoom, card.y + delta.y / zoom);
      }
    },
    [activeBoard, boards, pan, zoom],
  );

  // -- render -------------------------------------------------------------

  if (boards.boards.length === 0) {
    return (
      <div className="board-view board-view--empty">
        <EmptyState title="No boards yet">
          <span>Create your first whiteboard to organise notes spatially.</span>
          <button type="button" className="button button--primary" onClick={() => boards.createBoard()}>
            New board
          </button>
        </EmptyState>
      </div>
    );
  }

  return (
    <div className="board-view">
      <div className="board-toolbar">
        <div className="board-toolbar__group">
          <label className="board-toolbar__label" htmlFor="board-select">
            Board
          </label>
          <select
            id="board-select"
            className="board-toolbar__select"
            value={boards.activeBoardId ?? ""}
            onChange={(event) => boards.selectBoard(event.target.value)}
          >
            {boards.boards.map((board) => (
              <option key={board.id} value={board.id}>
                {board.name}
              </option>
            ))}
          </select>
          <button type="button" className="button button--ghost" onClick={() => setDialog({ kind: "create" })}>
            New
          </button>
          <button
            type="button"
            className="button button--ghost"
            disabled={!activeBoard}
            onClick={() => setDialog({ kind: "rename" })}
          >
            Rename
          </button>
          <button
            type="button"
            className="button button--ghost"
            disabled={!activeBoard}
            onClick={() => setDialog({ kind: "delete" })}
          >
            Delete
          </button>
        </div>

        {activeBoard ? (
          <>
            <div className="board-toolbar__group">
              <label className="board-toolbar__label" htmlFor="board-surface">
                Surface
              </label>
              <select
                id="board-surface"
                className="board-toolbar__select"
                value={activeBoard.surface}
                onChange={(event) => boards.setSurface(event.target.value as BoardSurface)}
              >
                {BOARD_SURFACES.map((surface) => (
                  <option key={surface} value={surface}>
                    {surface}
                  </option>
                ))}
              </select>
              <button
                type="button"
                className="button button--ghost"
                onClick={boards.cycleSurface}
                aria-label="Cycle board surface"
              >
                Next surface
              </button>
            </div>

            <div className="board-toolbar__group">
              <label className="board-toolbar__label" htmlFor="board-folder">
                Folder link
              </label>
              <select
                id="board-folder"
                className="board-toolbar__select"
                value={activeBoard.folderId ?? ""}
                onChange={(event) => boards.setFolder(event.target.value || null)}
              >
                <option value="">No folder</option>
                {folders.map((folder) => (
                  <option key={folder.id} value={folder.id}>
                    {folder.name}
                  </option>
                ))}
              </select>
            </div>

            <div className="board-toolbar__group">
              <span className="board-toolbar__label">Arrange</span>
              {ARRANGE_MODES.map((mode) => (
                <button
                  key={mode}
                  type="button"
                  className="button button--ghost"
                  onClick={() => boards.arrangeBoard(mode)}
                  aria-label={`Arrange cards by ${mode}`}
                >
                  {mode}
                </button>
              ))}
            </div>

            <div className="board-toolbar__group">
              <span className="board-toolbar__label">View</span>
              <button
                type="button"
                className="button button--ghost"
                aria-label="Zoom out"
                onClick={() => zoomTo(ZOOM_STEPS[Math.max(0, ZOOM_STEPS.indexOf(zoom) - 1)] ?? MIN_ZOOM)}
              >
                −
              </button>
              <span className="board-toolbar__zoom" aria-live="polite">
                {Math.round(zoom * 100)}%
              </span>
              <button
                type="button"
                className="button button--ghost"
                aria-label="Zoom in"
                onClick={() => zoomTo(ZOOM_STEPS[Math.min(ZOOM_STEPS.length - 1, ZOOM_STEPS.indexOf(zoom) + 1)] ?? MAX_ZOOM)}
              >
                +
              </button>
              <button
                type="button"
                className="button button--ghost"
                onClick={() => {
                  setPan({ x: 24, y: 24 });
                  setZoom(1);
                }}
              >
                Reset view
              </button>
            </div>
          </>
        ) : null}
      </div>

      {activeBoard ? (
        <DndContext sensors={sensors} collisionDetection={rectIntersection} onDragEnd={onDragEnd}>
          <div className="board-view__body">
            <NoteTray
              notes={notes}
              noteDetail={noteDetail}
              isOnBoard={boards.isOnBoard}
              onAddNote={(noteId) => {
                // Keyboard/pointer-free path: drop it at the next free slot.
                const slot = freeSlot(activeBoard.cards);
                boards.addNote(noteId, slot.x, slot.y);
              }}
              folderFilter={activeBoard.folderId}
            />

            <BoardCanvas
              board={activeBoard}
              pan={pan}
              zoom={zoom}
              viewportRef={viewportRef}
              noteDetail={noteDetail}
              dimensionsFor={boards.dimensionsFor}
              shapeFor={boards.shapeFor}
              colorFor={boards.colorFor}
              selectedNoteId={selectedNoteId}
              onSelectCard={(noteId) => {
                setSelectedNoteId(noteId);
                boards.bringToFront(noteId);
              }}
              onOpenNote={onOpenNote}
              onRemoveCard={boards.removeCard}
              onCycleColor={boards.cycleCardColor}
              onSetColor={boards.setCardColor}
              onCycleShape={boards.cycleCardShape}
              onTogglePin={boards.togglePin}
              onBringToFront={boards.bringToFront}
              onResize={boards.resizeCard}
              onBackgroundPointerDown={beginPan}
              onBackgroundPointerMove={movePan}
              onBackgroundPointerUp={endPan}
              onWheel={onWheel}
            />
          </div>
        </DndContext>
      ) : (
        <EmptyState title="No board selected">
          <span>Create a board to get started.</span>
        </EmptyState>
      )}

      <FolderNameDialog
        open={dialog.kind === "create"}
        mode="create"
        onSubmit={(name) => {
          boards.createBoard(name);
          setDialog({ kind: "none" });
        }}
        onCancel={() => setDialog({ kind: "none" })}
      />

      <FolderNameDialog
        open={dialog.kind === "rename"}
        mode="rename"
        initialName={activeBoard?.name ?? ""}
        onSubmit={(name) => {
          if (activeBoard) boards.renameBoard(activeBoard.id, name);
          setDialog({ kind: "none" });
        }}
        onCancel={() => setDialog({ kind: "none" })}
      />

      <ConfirmDialog
        open={dialog.kind === "delete"}
        title="Delete board"
        message={`Delete "${activeBoard?.name ?? ""}"? The notes themselves are not affected.`}
        confirmLabel="Delete board"
        onConfirm={() => {
          if (activeBoard) boards.deleteBoard(activeBoard.id);
          setDialog({ kind: "none" });
        }}
        onCancel={() => setDialog({ kind: "none" })}
      />
    </div>
  );
}

function clampZoom(value: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, value));
}

/** Convert a screen-space rect (the dragged item) into board-space coords. */
function boardPointFromActive(
  translated: { left: number; top: number; width: number; height: number } | null,
  viewport: HTMLDivElement | null,
  pan: { x: number; y: number },
  zoom: number,
): { x: number; y: number } {
  if (!translated || !viewport) return { x: 48, y: 48 };
  const bounds = viewport.getBoundingClientRect();
  const centerX = translated.left + translated.width / 2;
  const centerY = translated.top + translated.height / 2;
  return {
    x: (centerX - bounds.left - pan.x) / zoom,
    y: (centerY - bounds.top - pan.y) / zoom,
  };
}
