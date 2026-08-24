from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from coderio.tools.checkpoint import DEFAULT_CHECKPOINT


class WriteFileArgs(BaseModel):
    path: str = Field(description="Path to the file to write.")
    content: str = Field(description="Full content to write (overwrites existing).")


class WriteFileTool:
    name = "write_file"
    description = (
        "Write content to a file, overwriting if it exists. Creates parent "
        "directories. Requires permission in confirm/plan modes. The prior "
        "state is checkpointed — /undo reverts it."
    )
    args_schema = WriteFileArgs

    def run(self, path: str, content: str) -> str:
        p = Path(path)
        # Snapshot BEFORE touching the file so /undo can restore it.
        DEFAULT_CHECKPOINT.snapshot(p)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {path}"
