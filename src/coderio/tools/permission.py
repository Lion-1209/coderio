from __future__ import annotations

from enum import StrEnum
from typing import Any, Callable

from coderio.tools.base import DESTRUCTIVE_TOOLS


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
    """

    def __init__(self, mode: str):
        self._mode = PermissionMode(mode)

    @property
    def mode(self) -> str:
        return self._mode

    def check(self, tool_name: str, args: dict[str, Any]) -> bool:
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

    def __init__(self, console=None, prompt_fn: Callable[[str, dict[str, Any]], bool] | None = None):
        super().__init__(PermissionMode.CONFIRM)
        self._console = console
        self._prompt_fn = prompt_fn or _default_prompt

    def _ask(self, tool_name: str, args: dict[str, Any]) -> bool:
        if self._console:
            self._console.print(f"[yellow]permission:[/yellow] {tool_name}({args})")
        return self._prompt_fn(tool_name, args)


class AutoPermissionGate(PermissionGate):
    """Convenience: auto-approve everything. For tests / explicit trust."""

    def __init__(self):
        super().__init__(PermissionMode.AUTO)

    def _ask(self, tool_name: str, args: dict[str, Any]) -> bool:
        return True
