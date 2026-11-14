import { useEffect, useState } from "react";

import { Modal } from "./Modal";

interface FolderNameDialogProps {
  open: boolean;
  /** "Create" or "Rename" — drives the copy and the submit label. */
  mode: "create" | "rename";
  initialName?: string;
  busy?: boolean;
  onSubmit: (name: string) => void;
  onCancel: () => void;
}

const MAX_FOLDER_NAME = 100;

/** A dialog for creating or renaming a folder (they share one form). */
export function FolderNameDialog({
  open,
  mode,
  initialName = "",
  busy = false,
  onSubmit,
  onCancel,
}: FolderNameDialogProps) {
  const [name, setName] = useState(initialName);

  useEffect(() => {
    if (open) setName(initialName);
  }, [open, initialName]);

  const valid = name.trim().length > 0 && name.trim().length <= MAX_FOLDER_NAME;

  const submit = () => {
    if (!valid) return;
    onSubmit(name.trim());
  };

  return (
    <Modal
      open={open}
      title={mode === "create" ? "New folder" : "Rename folder"}
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
            disabled={busy || !valid}
          >
            {busy ? "Saving…" : mode === "create" ? "Create folder" : "Rename"}
          </button>
        </>
      }
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <label className="field">
          <span className="field__label">Folder name</span>
          <input
            className="field__input"
            type="text"
            value={name}
            maxLength={MAX_FOLDER_NAME}
            autoFocus
            aria-label="Folder name"
            placeholder="e.g. Project Alpha"
            onChange={(event) => setName(event.target.value)}
          />
        </label>
      </form>
    </Modal>
  );
}
