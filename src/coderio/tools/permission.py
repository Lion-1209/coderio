from __future__ import annotations

from enum import StrEnum
from typing import Any, Callable

from coderio.tools.base import DESTRUCTIVE_TOOLS, FILE_EDIT_TOOLS


class PermissionMode(StrEnum):
    """Four permission levels (industry-standard tiered access).

    StrEnum so members ARE strings (== works with raw str), but invalid values
    raise ValueError at construction — catching config typos early.

    Levels (least → most permissive):
      PLAN      — read-only, blocks ALL writes/shell (safe exploration)
      CONFIRM   — prompts before each destructive action
      AUTO_EDIT — auto-allow file edits, but shell/web/note still need confirm
      FULL      — auto-allow everything (no prompts)

    Backward compat: the old "auto" string maps to FULL via normalize().

    Security model: file path isolation is handled by deepagents' backend
    virtual_mode (not by WorkspacePolicy — which was deleted because it
    couldn't handle virtual paths). Permission gates only control WHICH tools
    may execute, not WHERE they can write.
    """

    PLAN = "plan"
    CONFIRM = "confirm"
    AUTO_EDIT = "auto_edit"
    FULL = "full"

    @classmethod
    def normalize(cls, raw: str) -> "PermissionMode":
        """Map a raw config string to a PermissionMode, with backward compat.

        Old configs may have permission_mode = "auto" (the pre-v0.2 name for
        FULL). This silently upgrades it so users don't get a ValueError on
        existing configs.
        """
        if raw == "auto":
            return cls.FULL
        return cls(raw)


class PermissionGate:
    """Permission gate based on tool type + mode (no path checking).

    Path isolation is delegated to deepagents' backend virtual_mode, which
    maps virtual paths (e.g. /foo.py) to the workspace root and enforces
    that all file operations stay inside it. This gate only decides whether
    a tool TYPE is allowed in the current MODE — it does NOT inspect paths.
    """

    def __init__(self, mode: str):
        self._mode = PermissionMode.normalize(mode)

    @property
    def mode(self) -> str:
        return self._mode

    def check(self, tool_name: str, args: dict[str, Any]) -> bool | str:
        # note tool: only WRITE/APPEND/DELETE are destructive. read/list are
        # read-only and shouldn't prompt (same as read_file/ls).
        if tool_name == "note":
            action = str(args.get("action", "")).lower()
            if action in ("read", "list"):
                return True
        if tool_name not in DESTRUCTIVE_TOOLS:
            return True
        # FULL: auto-allow everything.
        if self._mode == PermissionMode.FULL:
            return True
        # AUTO_EDIT: auto-allow file edits, shell/web/note still confirm.
        if self._mode == PermissionMode.AUTO_EDIT:
            if tool_name in FILE_EDIT_TOOLS:
                return True
            return self._ask(tool_name, args)
        if self._mode == PermissionMode.PLAN:
            return False
        # CONFIRM: prompt for all destructive tools.
        return self._ask(tool_name, args)

    def _ask(self, tool_name: str, args: dict[str, Any]) -> bool | str:
        raise NotImplementedError


def _default_prompt(tool_name: str, args) -> bool:
    """Console prompt used when no custom prompter is supplied."""
    confirm = input(f"Allow {tool_name}({args})? [y/N] ").strip().lower()
    return confirm in {"yes", "y"}


class RichPromptPermissionGate(PermissionGate):
    """Concrete confirm-mode gate using a Rich console.

    `prompt_fn` is injectable so tests can answer without a real TTY.
    """

    def __init__(
        self,
        console=None,
        prompt_fn: Callable[[str, dict[str, Any]], bool] | None = None,
    ):
        super().__init__(PermissionMode.CONFIRM)
        self._console = console
        self._prompt_fn = prompt_fn or _default_prompt

    def _ask(self, tool_name: str, args: dict[str, Any]) -> bool:
        if self._console:
            self._console.print(f"[yellow]permission:[/yellow] {tool_name}({args})")
        return self._prompt_fn(tool_name, args)


class AutoPermissionGate(PermissionGate):
    """Auto-approve everything (FULL mode). For tests / explicit trust."""

    def __init__(self):
        super().__init__(PermissionMode.FULL)

    def _ask(self, tool_name: str, args: dict[str, Any]) -> bool:
        return True
