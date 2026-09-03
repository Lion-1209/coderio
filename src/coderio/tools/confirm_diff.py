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


def _as_bool(v: object) -> bool:
    """Parse a bool the way the pydantic schema does: the raw model-JSON args
    are NOT validated before reaching the preview, so `"false"` (string) must
    parse as False — a plain bool("false") is True and showed a misleading
    all-occurrences preview (audit 2026-09-03 P2)."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes", "y", "on")
    return bool(v)


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
    base = Path(workdir).resolve() if workdir else Path.cwd().resolve()
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
            # align with the real tool: deepagents normalizes CRLF/CR to LF
            # before matching (filesystem.py), so the preview must too —
            # otherwise a CRLF file + CRLF old_string renders "would fail"
            # while the real edit succeeds (audit P2).
            old = old.replace("\r\n", "\n").replace("\r", "\n")
            from coderio.tools.edit_file import _strip_line_prefix

            if tool_name == EDIT_FILE:
                edits = [
                    {
                        "old_string": _strip_line_prefix(str(args.get("old_string", ""))),
                        "new_string": _strip_line_prefix(str(args.get("new_string", ""))),
                        "replace_all": _as_bool(args.get("replace_all", False)),
                    }
                ]
            else:
                edits = [
                    {
                        "old_string": _strip_line_prefix(str(e.get("old_string", ""))),
                        "new_string": _strip_line_prefix(str(e.get("new_string", ""))),
                        "replace_all": _as_bool(e.get("replace_all", False)),
                    }
                    for e in list(args.get("edits", []))
                ]
            # Match the REAL tool semantics (deepagents
            # perform_string_replacement + multi_edit all-or-nothing):
            #   0 occurrences          -> the edit fails, nothing is written
            #   >1 without replace_all -> the edit fails, nothing is written
            #   replace_all=True       -> EVERY occurrence is replaced
            # A preview that shows a partial/partially-applied result would
            # mislead the approval (2026-09-03 audit finding 2).
            new = old
            for idx, e in enumerate(edits, 1):
                o = str(e.get("old_string", ""))
                n = str(e.get("new_string", ""))
                replace_all = e["replace_all"]
                count = new.count(o) if o else 0
                if count == 0:
                    return f"(cannot preview: edit {idx}: string not found in file — the tool would fail)"
                if count > 1 and not replace_all:
                    return (
                        f"(cannot preview: edit {idx}: the target string appears {count} times — "
                        f"the tool would fail without replace_all)"
                    )
                new = new.replace(o, n)
            return _unified(old, new, fp, max_lines)
    except Exception:  # noqa: BLE001 — preview must never break confirmation
        return None
    return None
