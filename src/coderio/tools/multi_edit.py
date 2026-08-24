from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from coderio.tools.checkpoint import DEFAULT_CHECKPOINT
from coderio.tools.edit_file import _strip_line_prefix


class _SingleEdit(BaseModel):
    old_string: str = Field(description="Exact text to replace (must match uniquely unless replace_all).")
    new_string: str = Field(description="Text to substitute.")
    replace_all: bool = Field(default=False, description="Replace every occurrence.")


class MultiEditArgs(BaseModel):
    path: str = Field(description="Path to the file to edit.")
    edits: list[_SingleEdit] = Field(description="Ordered list of edits to apply sequentially.")


class MultiEditTool:
    name = "multi_edit"
    description = (
        "Apply multiple exact-string edits to one file in a single call. Each edit sees "
        "the result of the previous one. If any edit's old_string is not found (or is "
        "ambiguous), the whole operation aborts with NO changes written. Requires "
        "permission in confirm/plan modes."
    )
    args_schema = MultiEditArgs

    def run(self, path: str, edits: list[dict]) -> str:
        p = Path(path)
        if not p.is_file():
            return f"Error: file not found: {path}"
        if not edits:
            return "No edits provided; file unchanged."
        text = p.read_text(encoding="utf-8", errors="replace")
        applied = 0
        for i, edit in enumerate(edits):
            old = _strip_line_prefix(edit.get("old_string", ""))
            new = _strip_line_prefix(edit.get("new_string", ""))
            replace_all = edit.get("replace_all", False)
            # Empty old_string is a footgun (see edit_file.py): str.replace("",
            # x) inserts x between every character. Reject before it corrupts
            # the file — multi_edit is atomic, so nothing is written yet.
            if not old:
                return (
                    f"Error: edit #{i + 1} old_string is empty; provide the exact "
                    "text to replace. Aborted (no changes written)."
                )
            count = text.count(old)
            if count == 0:
                return f"Error: edit #{i + 1} old_string not found in {path}; aborted (no changes written)."
            if count > 1 and not replace_all:
                return (
                    f"Error: edit #{i + 1} old_string matches {count} times (not unique); "
                    "set replace_all=true or make it unique. Aborted (no changes written)."
                )
            text = text.replace(old, new) if replace_all else text.replace(old, new, 1)
            applied += 1
        # ONE snapshot per run() — multi_edit is one logical operation, and a
        # single /undo should revert the whole batch, not its first slice.
        DEFAULT_CHECKPOINT.snapshot(p)
        p.write_text(text, encoding="utf-8")
        return f"Edited {path}: applied {applied} edit(s)"
