import { useEffect } from "react";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Link from "@tiptap/extension-link";
import Placeholder from "@tiptap/extension-placeholder";
import Underline from "@tiptap/extension-underline";

import { EditorToolbar } from "./EditorToolbar";

interface RichTextEditorProps {
  /** Current HTML content (already sanitised by the backend on the way in). */
  value: string;
  /** Called with the new HTML whenever the document changes. */
  onChange: (html: string) => void;
  /** Read-only mode: renders content with no toolbar and no editing. */
  readOnly?: boolean;
  placeholder?: string;
  ariaLabel?: string;
}

/**
 * The WYSIWYG editor (TipTap).
 *
 * The editor is a *controlled* component: it never owns the source of truth.
 * `value` is the draft held by the workspace hook, and every edit is reported
 * back through `onChange` as HTML. The toolbar vocabulary matches exactly the
 * tags the backend sanitiser permits, so anything typed here survives a save.
 *
 * `readOnly` is a presentation affordance only — the backend still rejects a
 * write from a READ collaborator; this just avoids offering an editor that
 * could not be saved.
 */
export function RichTextEditor({
  value,
  onChange,
  readOnly = false,
  placeholder = "Write your note…",
  ariaLabel = "Note content",
}: RichTextEditorProps) {
  const editor = useEditor({
    extensions: [
      StarterKit.configure({ heading: { levels: [1, 2, 3] } }),
      Underline,
      Link.configure({ openOnClick: false, autolink: true }),
      Placeholder.configure({ placeholder }),
    ],
    content: value,
    editable: !readOnly,
    editorProps: {
      attributes: {
        class: "rich-editor__surface",
        role: "textbox",
        "aria-multiline": "true",
        "aria-label": ariaLabel,
      },
    },
    onUpdate: ({ editor: instance }) => {
      onChange(instance.getHTML());
    },
  });

  // Keep the editor in sync when the draft is replaced from outside (loading a
  // note, resolving a conflict, switching notes) without clobbering an edit in
  // progress: only setContent when the incoming HTML differs from the document.
  useEffect(() => {
    if (!editor) return;
    if (value !== editor.getHTML()) {
      editor.commands.setContent(value, false);
    }
  }, [editor, value]);

  useEffect(() => {
    if (!editor) return;
    editor.setEditable(!readOnly);
  }, [editor, readOnly]);

  if (!editor) {
    return <div className="rich-editor rich-editor--loading">Loading editor…</div>;
  }

  return (
    <div className={`rich-editor${readOnly ? " rich-editor--readonly" : ""}`}>
      {!readOnly ? <EditorToolbar editor={editor} /> : null}
      <EditorContent editor={editor} />
    </div>
  );
}
