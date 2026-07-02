from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class GlobArgs(BaseModel):
    pattern: str = Field(description="Glob pattern (e.g. '**/*.py' to recurse).")
    path: str = Field(default=".", description="Base directory to search in.")


class GlobTool:
    name = "glob"
    description = "Match files by glob pattern (e.g. **/*.py). Returns matching paths."
    args_schema = GlobArgs

    def run(self, pattern: str, path: str = ".") -> str:
        base = Path(path)
        matches = sorted(str(p) for p in base.glob(pattern))
        return "\n".join(matches) if matches else "No matches"
