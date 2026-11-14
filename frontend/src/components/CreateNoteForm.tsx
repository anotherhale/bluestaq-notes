import { useState } from "react";

import type { NoteDraft } from "../hooks/useWorkspace";

interface CreateNoteFormProps {
  busy: boolean;
  /** The folder the new note will be created in, if any. */
  folderName: string | null;
  onCreate: (input: NoteDraft) => Promise<boolean>;
  onCancel: () => void;
}

const MAX_TITLE = 200;

/**
 * An inline form for creating a new note. The title is required by the domain.
 * The note is created in the currently selected folder (or the root).
 */
export function CreateNoteForm({ busy, folderName, onCreate, onCancel }: CreateNoteFormProps) {
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");

  const titleIsValid = title.trim().length > 0;

  const submit = async () => {
    if (!titleIsValid) return;
    const created = await onCreate({ title: title.trim(), content });
    if (created) {
      setTitle("");
      setContent("");
      onCancel();
    }
  };

  return (
    <form
      className="create-form"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <p className="create-form__location">
        Creating in <strong>{folderName ?? "My Drive"}</strong>
      </p>
      <label className="field">
        <span className="field__label">New note title</span>
        <input
          className="field__input"
          type="text"
          value={title}
          maxLength={MAX_TITLE}
          autoFocus
          aria-label="New note title"
          onChange={(event) => setTitle(event.target.value)}
          placeholder="e.g. Sprint retro"
        />
      </label>
      <label className="field">
        <span className="field__label">Content</span>
        <textarea
          className="field__textarea field__textarea--small"
          value={content}
          rows={4}
          aria-label="New note content"
          onChange={(event) => setContent(event.target.value)}
          placeholder="Optional — you can format it after creating."
        />
      </label>
      <div className="create-form__actions">
        <button type="button" className="button button--ghost" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button
          type="submit"
          className="button button--primary"
          disabled={busy || !titleIsValid}
        >
          {busy ? "Creating…" : "Create note"}
        </button>
      </div>
    </form>
  );
}
