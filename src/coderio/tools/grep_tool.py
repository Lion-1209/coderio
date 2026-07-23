from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field


class GrepArgs(BaseModel):
    pattern: str = Field(description="Regex pattern to search for.")
    path: str = Field(default=".", description="File or directory to search in.")
    glob: str = Field(default="", description="Optional glob filter (e.g. '*.py').")
    output_mode: str = Field(
        default="content",
        description="'content' (lines), 'files_with_matches' (paths), or 'count'.",
    )


def _rg_available() -> bool:
    return shutil.which("rg") is not None


class GrepTool:
    name = "grep"
    description = (
        "Search file contents by regex. Uses ripgrep if available, else Python fallback. "
        "output_mode: 'content' (default), 'files_with_matches', or 'count'."
    )
    args_schema = GrepArgs

    def run(
        self,
        pattern: str,
        path: str = ".",
        glob: str = "",
        output_mode: str = "content",
    ) -> str:
        if _rg_available():
            return self._with_rg(pattern, path, glob, output_mode)
        return self._python_fallback(pattern, path, glob, output_mode)

    def _with_rg(self, pattern: str, path: str, glob: str, output_mode: str) -> str:
        cmd = ["rg", "--no-heading", "-n"]
        mode_map = {"content": "", "files_with_matches": "-l", "count": "-c"}
        flag = mode_map.get(output_mode, "")
        if flag:
            cmd.append(flag)
        if glob:
            cmd += ["--glob", glob]
        cmd += [pattern, path]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            return (proc.stdout or "").strip() or "No matches"
        except FileNotFoundError:
            return self._python_fallback(pattern, path, glob, output_mode)

    def _python_fallback(self, pattern: str, path: str, glob: str, output_mode: str) -> str:
        base = Path(path)
        if glob:
            files = list(base.rglob(glob))
        else:
            files = list(base.rglob("*"))
        rx = re.compile(pattern)
        content_hits = []
        matched_files = []
        total = 0
        for f in files:
            if not f.is_file():
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            file_count = 0
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    total += 1
                    file_count += 1
                    if output_mode == "files_with_matches":
                        matched_files.append(str(f))
                        break
                    elif output_mode == "content":
                        content_hits.append(f"{f}:{i}:{line}")
        if output_mode == "count":
            return f"{total} matches"
        if output_mode == "files_with_matches":
            return "\n".join(matched_files) if matched_files else "No matches"
        return "\n".join(content_hits) if content_hits else "No matches"
