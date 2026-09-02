from __future__ import annotations

from enum import StrEnum
from typing import Any, Callable

from coderio.tools.base import DESTRUCTIVE_TOOLS, FILE_EDIT_TOOLS
from coderio.tools.taxonomy import NOTE as _NOTE_TOOL
from coderio.tools.taxonomy import SHELL as _SHELL_TOOL

# Heuristic keywords for classifying MCP tool risk by NAME. MCP tools arrive
# with server-prefixed names (e.g. "filesystem_write_file", "github_create_pr")
# that PermissionGate's DESTRUCTIVE_TOOLS set doesn't recognize — without this
# classification, a PLAN-mode agent could freely call an MCP server's write/exec
# tools because they fall through to the "not in DESTRUCTIVE_TOOLS → allow" path.
#
# This is a NAME-HEURISTIC, not a capability check: a server could expose a
# destructive tool under a benign name. The goal is to catch the obvious cases
# (write/create/delete/execute in the tool name) so the tier gate engages, same
# as for coderio's built-in tools. False negatives are possible; false positives
# only trigger a confirm prompt (safe direction).
_MCP_DESTRUCTIVE_KEYWORDS = frozenset(
    {
        "write",
        "create",
        "delete",
        "remove",
        "execute",
        "exec",
        "run",
        "shell",
        "fetch",  # network egress
        "request",
        "post",  # HTTP POST
        "put",  # HTTP PUT
        "patch",  # HTTP PATCH
    }
)

# Built-in tool names that CONTAIN a destructive keyword but must NOT be
# classified destructive by the heuristic — they're handled explicitly by the
# mode logic (or are read-only despite the name). Without this exclusion,
# write_todos (deepagents' planning tool) would match "write" and be blocked in
# PLAN mode, preventing the agent from ever creating a todo list.
_HEURISTIC_EXCLUDE = frozenset({"write_todos"})


def _is_mcp_destructive(tool_name: str) -> bool:
    """Heuristic: does this tool name suggest a destructive/network action?

    Used for MCP tools whose names aren't in the static DESTRUCTIVE_TOOLS set.
    Matches any keyword as a substring (case-insensitive) — "filesystem_write",
    "github_create_pr", "db_delete_row" all match.

    Excludes built-in names in _HEURISTIC_EXCLUDE that happen to contain a
    keyword but have their own explicit handling (e.g. write_todos is a
    planning tool, not a file write).
    """
    if tool_name in _HEURISTIC_EXCLUDE:
        return False
    lower = tool_name.lower()
    return any(kw in lower for kw in _MCP_DESTRUCTIVE_KEYWORDS)


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

    def __init__(self, mode: str, auto_allow_execute: bool = False):
        """Initialize the gate.

        Args:
            mode: permission tier (plan/confirm/auto_edit/full).
            auto_allow_execute: when True, the ``execute`` (shell) tool is
                auto-allowed in CONFIRM/AUTO_EDIT modes without prompting —
                this is the "sandbox 消除问题" design from Claude Code
                (``autoAllowBashIfSandboxed``). Intended for use when
                ``sandbox_mode != "off"``: the sandbox provides the real
                isolation boundary, so the per-command prompt becomes noise.
                PLAN mode is unaffected (always read-only — sandbox doesn't
                change that semantics). The command blacklist still applies
                (via CommandReviewMiddleware, which runs after this gate).
        """
        self._mode = PermissionMode.normalize(mode)
        self._auto_allow_execute = auto_allow_execute

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def auto_allow_execute(self) -> bool:
        return self._auto_allow_execute

    def check(self, tool_name: str, args: dict[str, Any]) -> bool | str:
        # note tool: only WRITE/APPEND/DELETE are destructive. read/list are
        # read-only and shouldn't prompt (same as read_file/ls).
        if tool_name == _NOTE_TOOL:
            action = str(args.get("action", "")).lower()
            if action in ("read", "list"):
                return True
        if tool_name in DESTRUCTIVE_TOOLS:
            pass  # fall through to mode-based decision below
        elif _is_mcp_destructive(tool_name):
            # MCP tool with a destructive-sounding name (write/create/delete/execute/...).
            # Treat it like a built-in destructive tool so the tier gate engages.
            # This prevents a PLAN-mode agent from calling an MCP server's write
            # tool just because "filesystem_write_file" isn't in DESTRUCTIVE_TOOLS.
            pass
        else:
            # Read-only tool (built-in or MCP) — allow regardless of mode.
            return True
        # FULL: auto-allow everything.
        if self._mode == PermissionMode.FULL:
            return True
        # auto_allow_execute: the "sandbox 消除问题" path. When a sandbox is
        # active, the OS provides the real isolation boundary, so prompting
        # the user for every shell command becomes noise. Skip the prompt for
        # the execute tool specifically. PLAN mode is excluded — PLAN is always
        # read-only, and sandbox doesn't change that contract.
        if tool_name == _SHELL_TOOL and self._auto_allow_execute and self._mode != PermissionMode.PLAN:
            return True
        # AUTO_EDIT: auto-allow file edits, shell/web/note still confirm.
        # MCP destructive tools always confirm in AUTO_EDIT (we can't reliably
        # tell an MCP write from an MCP execute, so be conservative).
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
        auto_allow_execute: bool = False,
    ):
        super().__init__(PermissionMode.CONFIRM, auto_allow_execute=auto_allow_execute)
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
