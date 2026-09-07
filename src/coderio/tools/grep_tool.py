from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from pydantic import BaseModel, Field

# Fallback caps (audit P2, 2026-09-04): files over 1MB are skipped in the
# Python fallback (a minified bundle would be read fully for every match),
# content output is capped so a loose pattern can't flood the context, and
# ripgrep's stdout is capped symmetrically. The production engine uses
# deepagents' grep (output-capped) — these defend the standalone tool.
_MAX_GREP_FILE_BYTES = 1024 * 1024
_MAX_CONTENT_HITS = 2000
_MAX_OUTPUT_CHARS = 100_000


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
            out = (proc.stdout or "").strip() or "No matches"
            if len(out) > _MAX_OUTPUT_CHARS:
                out = out[:_MAX_OUTPUT_CHARS] + "\n[truncated — narrow the pattern or path]"
            return out
        except FileNotFoundError:
            return self._python_fallback(pattern, path, glob, output_mode)

    def _python_fallback(self, pattern: str, path: str, glob: str, output_mode: str) -> str:
        base = Path(path)
        if base.is_file():
            # rg accepts a bare file path; the fallback previously rglob'd it
            # (yields nothing on a file) and silently returned "No matches".
            files = [base]
        elif glob:
            files = list(base.rglob(glob))
        else:
            files = list(base.rglob("*"))
        rx = re.compile(pattern)
        content_hits = []
        matched_files = []
        total = 0
        hit_cap_reached = False
        for f in files:
            if hit_cap_reached:
                break
            if not f.is_file():
                continue
            try:
                if f.stat().st_size > _MAX_GREP_FILE_BYTES:
                    continue  # oversized: skipped, not read (minified bundles)
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
                        if len(content_hits) >= _MAX_CONTENT_HITS:
                            hit_cap_reached = True
                            break
        if output_mode == "count":
            return f"{total} matches"
        if output_mode == "files_with_matches":
            return "\n".join(matched_files) if matched_files else "No matches"
        result = "\n".join(content_hits) if content_hits else "No matches"
        if hit_cap_reached:
            result += f"\n[truncated: hit cap of {_MAX_CONTENT_HITS} matches — narrow the pattern or path]"
        return result
