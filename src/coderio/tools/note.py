from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

_DEFAULT_NOTES_DIR = Path.home() / ".coderio" / "notes"


class NoteArgs(BaseModel):
    action: str = Field(description="One of: write, read, list, append, delete.")
    name: str = Field(default="", description="Note name (becomes <name>.md).")
    content: str = Field(default="", description="Note content (for write/append).")


class NoteTool:
    """Cross-session long-term memory. Notes persist in ~/.coderio/notes/<name>.md
    and survive across REPL sessions, unlike session history."""

    name = "note"
    description = (
        "Persistent notes for cross-session memory. actions: write/read/list/append/delete. "
        "Notes are stored as markdown files and survive across sessions — use them to record "
        "decisions, context, or TODOs you want to recall later."
    )
    args_schema = NoteArgs

    def __init__(self, notes_dir: Path | str | None = None):
        self._dir = Path(notes_dir) if notes_dir else _DEFAULT_NOTES_DIR

    def _path(self, name: str) -> Path:
        safe = "".join(c for c in name if c.isalnum() or c in "-_") or "untitled"
        return self._dir / f"{safe}.md"

    def run(self, action: str, name: str = "", content: str = "") -> str:
        if action == "list":
            self._dir.mkdir(parents=True, exist_ok=True)
            notes = sorted(p.stem for p in self._dir.glob("*.md"))
            if not notes:
                return "No notes."
            return "Notes:\n" + "\n".join(f"  - {n}" for n in notes)
        elif action == "write":
            if not name:
                return "Error: name is required for write."
            p = self._path(name)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return f"Saved note: {p.stem}"
        elif action == "read":
            if not name:
                return "Error: name is required for read."
            p = self._path(name)
            if not p.is_file():
                return f"Note not found: {name}"
            return p.read_text(encoding="utf-8", errors="replace")
        elif action == "append":
            if not name:
                return "Error: name is required for append."
            p = self._path(name)
            p.parent.mkdir(parents=True, exist_ok=True)
            existing = p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
            p.write_text(existing + content, encoding="utf-8")
            return f"Appended to note: {p.stem}"
        elif action == "delete":
            if not name:
                return "Error: name is required for delete."
            p = self._path(name)
            if p.is_file():
                p.unlink()
                return f"Deleted note: {p.stem}"
            return f"Note not found: {name}"
        return f"Error: unknown action {action!r}. Use: write/read/list/append/delete."
