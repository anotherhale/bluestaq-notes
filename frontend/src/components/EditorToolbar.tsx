import type { Editor } from "@tiptap/react";

interface EditorToolbarProps {
  editor: Editor | null;
}

/**
 * The formatting toolbar for the rich-text editor.
 *
 * Every control is a plain button with an explicit accessible label and an
 * active state derived from the editor's own state, so it is testable without
 * depending on icons. The button set is exactly the vocabulary the backend's
 * sanitiser allows (see `domain/notes/rich_text.py`), so nothing the toolbar can
 * produce is ever stripped on save.
 */
export function EditorToolbar({ editor }: EditorToolbarProps) {
  if (!editor) return null;

  const setLink = () => {
    const previous = editor.getAttributes("link").href as string | undefined;
    const url = window.prompt("Link URL", previous ?? "https://");
    if (url === null) return;
    if (url === "") {
      editor.chain().focus().extendMarkRange("link").unsetLink().run();
      return;
    }
    editor.chain().focus().extendMarkRange("link").setLink({ href: url }).run();
  };

  const button = (label: string, onClick: () => void, isActive: boolean) => (
    <button
      type="button"
      className={`toolbar__button${isActive ? " toolbar__button--active" : ""}`}
      aria-label={label}
      aria-pressed={isActive}
      title={label}
      // Prevent the button from stealing focus from the editor, which would
      // collapse the selection before the command runs.
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
    >
      {label}
    </button>
  );

  return (
    <div className="toolbar" role="toolbar" aria-label="Formatting">
      {button("Bold", () => editor.chain().focus().toggleBold().run(), editor.isActive("bold"))}
      {button("Italic", () => editor.chain().focus().toggleItalic().run(), editor.isActive("italic"))}
      {button(
        "Underline",
        () => editor.chain().focus().toggleUnderline().run(),
        editor.isActive("underline"),
      )}
      {button(
        "Heading 1",
        () => editor.chain().focus().toggleHeading({ level: 1 }).run(),
        editor.isActive("heading", { level: 1 }),
      )}
      {button(
        "Heading 2",
        () => editor.chain().focus().toggleHeading({ level: 2 }).run(),
        editor.isActive("heading", { level: 2 }),
      )}
      {button(
        "Bullet list",
        () => editor.chain().focus().toggleBulletList().run(),
        editor.isActive("bulletList"),
      )}
      {button(
        "Numbered list",
        () => editor.chain().focus().toggleOrderedList().run(),
        editor.isActive("orderedList"),
      )}
      {button(
        "Quote",
        () => editor.chain().focus().toggleBlockquote().run(),
        editor.isActive("blockquote"),
      )}
      {button("Link", setLink, editor.isActive("link"))}
      {button("Horizontal rule", () => editor.chain().focus().setHorizontalRule().run(), false)}
      {button("Undo", () => editor.chain().focus().undo().run(), false)}
      {button("Redo", () => editor.chain().focus().redo().run(), false)}
    </div>
  );
}
