"""Workspace path-boundary policy (read/write separation).

Prevents file-write tools and bash's cwd from escaping the trusted workspace
root, while leaving read tools free to access anywhere (an agent needs to read
site-packages, system configs, etc. to do its job — restricting reads makes it
"blind" and pushes it toward bash workarounds).

The policy is enforced inside PermissionGate.check(), NOT in the tools
themselves — so all 12 tools stay unchanged (zero intrusion) and there's a
single choke point. AutoPermissionGate also runs the policy, so --auto mode
skips interactive confirmation but NEVER skips the workspace boundary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


class WorkspacePolicy:
    """Trusted-workspace path boundary with read/write separation.

    - READ tools (read_file / list_dir / glob / grep): always allowed, any path.
    - WRITE tools (write_file / edit_file / multi_edit / bash): the path
      (or bash's cwd) must resolve() to a location inside ``root``.
    - Other tools (web_search / web_fetch / todo / note): no path args, skipped.

    Matching uses Path.resolve() (expands ~, follows symlinks, collapses ..),
    then relative_to(root). This is stronger than lexical folding: a symlink
    inside root pointing outside is correctly resolved and rejected.
    """

    READ_TOOLS: frozenset[str] = frozenset({"read_file", "list_dir", "glob", "grep"})
    WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file", "multi_edit", "bash"})

    def __init__(self, root: Path | str = ""):
        # Default to the process CWD when no root is configured — this matches
        # the pre-policy behavior (tools operated relative to where coderio was
        # launched). An explicit root lets users pin the workspace regardless
        # of where the process starts.
        self.root = Path(root or Path.cwd()).expanduser().resolve()

    def check(self, tool_name: str, args: dict[str, Any]) -> tuple[bool, str]:
        """Return (allowed, reason). When allowed=False, reason explains why.

        Read tools and path-less tools always pass. Write tools have their
        path/cwd args resolved and checked against the workspace root.
        """
        if tool_name in self.READ_TOOLS:
            return (True, "")
        if tool_name not in self.WRITE_TOOLS:
            return (True, "")  # web_search / web_fetch / todo / note / etc.
        for raw_path in self._extract_write_paths(tool_name, args):
            if not raw_path:
                continue
            try:
                resolved = Path(raw_path).expanduser().resolve()
            except (OSError, RuntimeError):
                # Path can't be resolved (broken symlink, encoding issue) —
                # fail closed: treat as outside rather than risk allowing it.
                return (
                    False,
                    f"path '{raw_path}' could not be resolved (outside workspace?)",
                )
            try:
                resolved.relative_to(self.root)
            except ValueError:
                return (False, f"path '{raw_path}' is outside workspace ({self.root})")
        return (True, "")

    @staticmethod
    def _extract_write_paths(tool_name: str, args: dict[str, Any]) -> list[str]:
        """Pull the path argument(s) from a write tool's args.

        bash uses ``cwd`` (empty = process default, which is typically the
        workspace root itself, so empty is left unvalidated). The file-write
        tools all use ``path``.
        """
        if tool_name == "bash":
            cwd = str(args.get("cwd", "") or "").strip()
            return [cwd] if cwd else []
        # write_file / edit_file / multi_edit
        p = str(args.get("path", "") or "").strip()
        return [p] if p else []
