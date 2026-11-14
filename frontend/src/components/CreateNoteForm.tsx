import { useState } from "react";

import type { NoteDraft } from "../hooks/useWorkspace";

interface CreateNoteFormProps {
  busy: boolean;
  onCreate: (input: NoteDraft) => Promise<boolean>;
  onCancel: () => void;
}

const MAX_TITLE = 200;

/** An inline form for creating a new note. Title is required by the domain. */
export function CreateNoteForm({ busy, onCreate, onCancel }: CreateNoteFormProps) {
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
      <label className="field">
        <span className="field__label">New note title</span>
        <input
          className="field__input"
          type="text"
          value={title}
          maxLength={MAX_TITLE}
          autoFocus
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
          onChange={(event) => setContent(event.target.value)}
          placeholder="Optional"
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
