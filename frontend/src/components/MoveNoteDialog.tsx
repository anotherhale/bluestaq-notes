import { useEffect, useState } from "react";

import type { Folder } from "../api/types";
import { Modal } from "./Modal";

interface MoveNoteDialogProps {
  open: boolean;
  noteTitle: string;
  currentFolderId: string | null;
  folders: Folder[];
  busy?: boolean;
  onMove: (folderId: string | null) => void;
  onCancel: () => void;
}

const ROOT = "__root__";

/**
 * The "Move to" dialog.
 *
 * It lists the owner's folders plus an explicit root ("My Drive") destination,
 * shows the note's current location, and submits the chosen destination. Only
 * the owner ever sees this dialog; the backend independently rejects a move by
 * a non-owner.
 */
export function MoveNoteDialog({
  open,
  noteTitle,
  currentFolderId,
  folders,
  busy = false,
  onMove,
  onCancel,
}: MoveNoteDialogProps) {
  const [target, setTarget] = useState<string>(currentFolderId ?? ROOT);

  useEffect(() => {
    if (open) setTarget(currentFolderId ?? ROOT);
  }, [open, currentFolderId]);

  const submit = () => {
    onMove(target === ROOT ? null : target);
  };

  return (
    <Modal
      open={open}
      title="Move note"
      onClose={onCancel}
      footer={
        <>
          <button type="button" className="button" onClick={onCancel} disabled={busy}>
            Cancel
          </button>
          <button
            type="button"
            className="button button--primary"
            onClick={submit}
            disabled={busy}
          >
            {busy ? "Moving…" : "Move here"}
          </button>
        </>
      }
    >
      <p className="modal__subtitle">{noteTitle}</p>
      <p className="move-dialog__current">
        Current location:{" "}
        <strong>
          {currentFolderId
            ? folders.find((folder) => folder.id === currentFolderId)?.name ?? "Folder"
            : "My Drive"}
        </strong>
      </p>
      <label className="field">
        <span className="field__label">Move to</span>
        <select
          className="field__input"
          value={target}
          aria-label="Destination folder"
          onChange={(event) => setTarget(event.target.value)}
        >
          <option value={ROOT}>My Drive (no folder)</option>
          {folders.map((folder) => (
            <option key={folder.id} value={folder.id}>
              {folder.name}
            </option>
          ))}
        </select>
      </label>
    </Modal>
  );
}
