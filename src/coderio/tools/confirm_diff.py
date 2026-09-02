"""Diff preview for confirm-mode file-write confirmations (P3-1, 2026-09-02).

When CONFIRM mode asks the user to allow write_file / edit_file / multi_edit,
a raw args dump (`old_string='...'` on one line) makes an informed decision
impossible. This module renders a unified diff of what the tool WILL do:

- write_file : existing file vs new content (or a "(new file)" block)
- edit_file  : the file with old_string replaced by new_string (first match)
- multi_edit : the file with each edit applied in order

BEST EFFORT by design: anything that can't be rendered (missing file for an
edit, unreadable, unexpected args) returns None and the confirmation menu
looks exactly like before — the preview must never block or break the
confirmation flow. Output is bounded (max_lines): a whole-file rewrite of a
huge file must not flood the menu.

Layering: lives in tools/ (not cli/) because the CALLER is
TuiPermissionGate._ask in cli/repl.py — cli must not be imported downward.
"""

from __future__ import annotations

import difflib
from pathlib import Path

from coderio.tools.taxonomy import EDIT_FILE, MULTI_EDIT, WRITE_FILE

_MAX_LINE_LEN = 240  # per-line cap (minified JS renders unreadable when long)


def _read_text(p: Path) -> str | None:
    try:
        if not p.is_file():
            return None
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _resolve(file_path: str, workdir) -> Path:
    q = Path(file_path)
    if q.is_absolute():
        return q
    base = Path(workdir) if workdir else Path.cwd()
    return base / q


def _clip(diff_lines: list[str], max_lines: int) -> str:
    if len(diff_lines) <= max_lines:
        return "\n".join(diff_lines)
    return "\n".join(diff_lines[:max_lines]) + f"\n… (+{len(diff_lines) - max_lines} more diff lines, truncated)"


def _shorten(line: str) -> str:
    return line if len(line) <= _MAX_LINE_LEN else line[:_MAX_LINE_LEN] + " …"


def _unified(old: str, new: str, path_display: str, max_lines: int) -> str:
    diff = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile=f"{path_display} (current)",
        tofile=f"{path_display} (after)",
    )
    lines = [_shorten(ln) for ln in list(diff)[2:]]  # drop the ---/+++ headers
    return _clip(lines, max_lines)


def build_diff_preview(
    tool_name: str,
    args: dict,
    workdir: str | Path | None = None,
    max_lines: int = 16,
) -> str | None:
    """Render a unified-diff preview for a file-write tool call, or None.

    ``workdir`` is the engine's backend root (workspace_root or the launch
    dir) — relative file_path args resolve against it, exactly like the
    tools themselves do.
    """
    try:
        if tool_name == WRITE_FILE:
            fp = str(args.get("file_path") or args.get("path") or "")
            if not fp:
                return None
            p = _resolve(fp, workdir)
            old = _read_text(p)
            content = str(args.get("content", ""))
            if old is None:
                lines = [f"(new file) {fp}"] + [f"+{_shorten(ln)}" for ln in content.splitlines()]
                return _clip(lines, max_lines + 1)
            return _unified(old, content, fp, max_lines)

        if tool_name in (EDIT_FILE, MULTI_EDIT):
            fp = str(args.get("file_path") or args.get("path") or "")
            if not fp:
                return None
            p = _resolve(fp, workdir)
            old = _read_text(p)
            if old is None:
                return f"(cannot preview: file not found: {fp})"
            if tool_name == EDIT_FILE:
                edits = [{"old_string": args.get("old_string", ""), "new_string": args.get("new_string", "")}]
            else:
                edits = list(args.get("edits", []))
            new = old
            applied = 0
            for e in edits:
                o = str(e.get("old_string", ""))
                n = str(e.get("new_string", ""))
                if o and o in new:
                    new = new.replace(o, n, 1)
                    applied += 1
            if not applied:
                # the real edit will fail the same way — telling the user up
                # front is more useful than an empty preview
                return "(cannot preview: old_string not found in file — the edit would fail)"
            return _unified(old, new, fp, max_lines)
    except Exception:  # noqa: BLE001 — preview must never break confirmation
        return None
    return None
