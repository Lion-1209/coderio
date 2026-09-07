from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

# Defensive read cap (audit P2, 2026-09-04): this tool reads the whole file
# before slicing lines, so a minified single-line bundle would push tens of MB
# into one tool result. The production engine is covered by deepagents' own
# caps (2000-line pagination + 20k-token result limit) — this defends the
# standalone tool.
_MAX_READ_BYTES = 2 * 1024 * 1024


class ReadFileArgs(BaseModel):
    path: str = Field(
        description="Path to the file. Relative paths resolve from the project root (cwd), "
        "NOT from the file's own directory. If unsure of the exact path, use glob or "
        "grep to locate it first."
    )
    offset: int = Field(default=0, description="1-based first line number to show (0=start).")
    limit: int = Field(default=2000, description="Maximum number of lines to return.")


class ReadFileTool:
    name = "read_file"
    description = (
        "Read a file from the local filesystem. Returns content with line numbers "
        "(cat -n style). Supports offset and limit.\n\n"
        "PATH RESOLUTION: relative paths resolve from the project root (cwd), not from "
        "the file's own directory. For example, if you saw 'store.py' under 'session/' "
        "in list_dir('src/coderio'), the correct path is 'src/coderio/session/store.py', "
        "NOT 'session/store.py'. When a read returns 'file not found', use glob to find "
        "the actual location: glob('**/store.py')."
    )
    args_schema = ReadFileArgs

    def run(self, path: str, offset: int = 0, limit: int = 2000) -> str:
        p = Path(path)
        if not p.exists():
            return f"Error: file not found: {path}"
        if p.is_dir():
            return f"Error: path is a directory: {path}"
        try:
            with p.open("rb") as f:
                data = f.read(_MAX_READ_BYTES + 1)
        except OSError as e:
            return f"Error reading file: {e}"
        truncated = len(data) > _MAX_READ_BYTES
        text = data[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
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
        result = "\n".join(numbered)
        if truncated:
            result += (
                f"\n[file truncated at {_MAX_READ_BYTES} bytes — use grep to locate content "
                "beyond this point, or read with offset/limit after narrowing]"
            )
        return result
