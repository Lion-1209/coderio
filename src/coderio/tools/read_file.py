from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class ReadFileArgs(BaseModel):
    path: str = Field(description="Absolute or relative path to the file.")
    offset: int = Field(default=0, description="1-based first line number to show (0=start).")
    limit: int = Field(default=2000, description="Maximum number of lines to return.")


class ReadFileTool:
    name = "read_file"
    description = (
        "Read a file from the local filesystem. Returns content with line numbers "
        "(cat -n style). Supports offset and limit. Errors for missing files or directories."
    )
    args_schema = ReadFileArgs

    def run(self, path: str, offset: int = 0, limit: int = 2000) -> str:
        p = Path(path)
        if not p.exists():
            return f"Error: file not found: {path}"
        if p.is_dir():
            return f"Error: path is a directory: {path}"
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return f"Error reading file: {e}"
        lines = text.splitlines()
        if offset > 0:
            start_idx = max(0, offset - 1)
        else:
            start_idx = 0
        if limit:
            end_idx = start_idx + limit
        else:
            end_idx = len(lines)
        numbered = [f"{i}\t{line}" for i, line in enumerate(lines[start_idx:end_idx], start_idx + 1)]
        return "\n".join(numbered)
