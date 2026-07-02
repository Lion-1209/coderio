from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

_LINE_PREFIX_RE = re.compile(r"^\s*\d+\s*?\t")


class EditFileArgs(BaseModel):
    path: str = Field(description="Path to the file to edit.")
    old_string: str = Field(description="Exact text to replace (must match uniquely unless replace_all).")
    new_string: str = Field(description="Text to substitute in place of old_string.")
    replace_all: bool = Field(default=False, description="Replace every occurrence.")


def _strip_line_prefix(text: str) -> str:
    """Strip read_file's 'N\\t' line-number prefix from each line (spec §3.2)."""
    return "\n".join(
        _LINE_PREFIX_RE.sub("", line) if _LINE_PREFIX_RE.match(line) else line
        for line in text.splitlines()
    )


class EditFileTool:
    name = "edit_file"
    description = (
        "Exact string replacement in a file. old_string must match uniquely unless "
        "replace_all is true. Requires permission in confirm/plan modes."
    )
    args_schema = EditFileArgs

    def run(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
        p = Path(path)
        if not p.is_file():
            return f"Error: file not found: {path}"
        text = p.read_text(encoding="utf-8", errors="replace")
        old_string = _strip_line_prefix(old_string)
        new_string = _strip_line_prefix(new_string)
        count = text.count(old_string)
        if count == 0:
            return f"Error: old_string not found in {path}"
        if count > 1 and not replace_all:
            return f"Error: old_string matches {count} times (not unique); set replace_all=true"
        if replace_all:
            new_text = text.replace(old_string, new_string)
        else:
            new_text = text.replace(old_string, new_string, 1)
        p.write_text(new_text, encoding="utf-8")
        return f"Edited {path}: replaced {count if replace_all else 1} occurrence(s)"
