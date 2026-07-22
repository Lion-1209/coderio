from __future__ import annotations

from enum import StrEnum
from typing import Any, Callable

from coderio.tools.base import DESTRUCTIVE_TOOLS

# Forward-declared type-only import to avoid a circular dependency at runtime
# (workspace.py imports nothing from permission.py, but we keep the typing tight).
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from coderio.tools.workspace import WorkspacePolicy


class PermissionMode(StrEnum):
    """Permission modes. StrEnum so members ARE strings (== works with raw str),
    but invalid values raise ValueError at construction — catching config typos
    early instead of silently degrading at runtime."""
    CONFIRM = "confirm"
    PLAN = "plan"
    AUTO = "auto"


class PermissionGate:
    """Abstract permission gate. Subclasses implement _ask() for CLI/GUI UI.

    Spec §3.4: confirm mode prompts; plan blocks all destructive; auto allows all.

    ``policy`` (WorkspacePolicy | None): when set, ALL modes (including AUTO)
    enforce the workspace path boundary BEFORE the mode-specific check. This
    means --auto skips interactive confirmation but NEVER skips the workspace
    boundary — a model in auto mode still can't write outside the workspace.
    None = no path check (back-compat for tests / headless use).
    """

    def __init__(self, mode: str, policy: "WorkspacePolicy | None" = None):
        self._mode = PermissionMode(mode)
        self._policy = policy

    @property
    def mode(self) -> str:
        return self._mode

    def check(self, tool_name: str, args: dict[str, Any]) -> bool:
        # --- Workspace boundary (runs in ALL modes, including AUTO) ---
        # This is the security floor: no matter how permissive the mode is,
        # a write tool's path must stay inside the workspace root. Without this,
        # --auto mode would let the model write anywhere on the filesystem.
        if self._policy is not None:
            allowed, _reason = self._policy.check(tool_name, args)
            if not allowed:
                return False
        # --- Mode-specific checks ---
        # note tool: only WRITE/APPEND/DELETE are destructive. read/list are
        # read-only and shouldn't prompt (same as read_file/list_dir). This is
        # action-level, not tool-name-level, because note is polymorphic.
        if tool_name == "note":
            action = str(args.get("action", "")).lower()
            if action in ("read", "list"):
                return True
        if tool_name not in DESTRUCTIVE_TOOLS:
            return True
        if self._mode == PermissionMode.AUTO:
            return True
        if self._mode == PermissionMode.PLAN:
            return False
        return self._ask(tool_name, args)

    def _ask(self, tool_name: str, args: dict[str, Any]) -> bool:
        raise NotImplementedError


def _default_prompt(tool_name: str, args) -> bool:
    """Console prompt used when no custom prompter is supplied."""
    confirm = input(f"Allow {tool_name}({args})? [y/N] ").strip().lower()
    return confirm in {"yes", "y"}


class RichPromptPermissionGate(PermissionGate):
    """Concrete confirm-mode gate using a Rich console (spec §3.4, §5.6 #5).

    `prompt_fn` is injectable so tests can answer without a real TTY.
    """

    def __init__(self, console=None,
                 prompt_fn: Callable[[str, dict[str, Any]], bool] | None = None,
                 policy: "WorkspacePolicy | None" = None):
        super().__init__(PermissionMode.CONFIRM, policy=policy)
        self._console = console
        self._prompt_fn = prompt_fn or _default_prompt

    def _ask(self, tool_name: str, args: dict[str, Any]) -> bool:
        if self._console:
            self._console.print(f"[yellow]permission:[/yellow] {tool_name}({args})")
        return self._prompt_fn(tool_name, args)


class AutoPermissionGate(PermissionGate):
    """Convenience: auto-approve everything — EXCEPT workspace boundary violations.

    For tests / explicit trust. ``policy`` is still enforced when provided:
    auto mode skips interactive confirmation, not the security floor.
    """

    def __init__(self, policy: "WorkspacePolicy | None" = None):
        super().__init__(PermissionMode.AUTO, policy=policy)

    def _ask(self, tool_name: str, args: dict[str, Any]) -> bool:
        return True
