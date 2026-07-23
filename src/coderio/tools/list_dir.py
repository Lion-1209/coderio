from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class ListDirArgs(BaseModel):
    path: str = Field(description="Directory to list.")
    recursive: bool = Field(default=False, description="Recurse into subdirectories.")
    max_depth: int = Field(default=3, description="Max recursion depth (when recursive).")


class ListDirTool:
    name = "list_dir"
    description = (
        "List the contents of a directory. Returns file/dir names (directories marked "
        "with a trailing /). Supports recursive listing with a max depth. Use to "
        "understand project structure."
    )
    args_schema = ListDirArgs

    def run(self, path: str, recursive: bool = False, max_depth: int = 3) -> str:
        base = Path(path)
        if not base.exists():
            return f"Error: path not found: {path}"
        if not base.is_dir():
            return f"Error: not a directory: {path}"

        lines = []

        def _walk(d: Path, prefix: str, depth: int) -> None:
            try:
                entries = sorted(
                    d.iterdir(),
                    key=lambda p: (not p.is_dir(), p.name.lower()),
                )
            except OSError as e:
                lines.append(f"{prefix}<error: {e}>")
                return
            for entry in entries:
                if entry.name in {
                    "__pycache__",
                    "venv",
                    ".venv",
                    ".git",
                    "node_modules",
                }:
                    continue
                if entry.is_dir():
                    lines.append(f"{prefix}{entry.name}/")
                    if recursive and depth + 1 < max_depth:
                        _walk(entry, prefix + "  ", depth + 1)
                else:
                    lines.append(f"{prefix}{entry.name}")

        _walk(base, "", 0)
        return "\n".join(lines) if lines else "(empty directory)"
