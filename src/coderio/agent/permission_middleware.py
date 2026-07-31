"""Permission gate as a deepagents AgentMiddleware.

Wraps coderio's PermissionGate (the 4-tier access system: plan/confirm/
auto_edit/full + workspace boundary) so it runs BEFORE every tool execution
inside a deepagents agent loop. Without this, deepagents' built-in tools
(read_file/write_file/execute) bypass the workspace safety boundary entirely.

Translation layer: deepagents uses different tool names and arg keys than
coderio's gate expects:
  - 'execute' (shell)     → gate sees 'bash'
  - 'write_file/edit_file'→ arg 'file_path' → gate sees 'path'
  - 'write_todos' (plan)  → gate sees 'todo' (non-destructive, auto-allowed)

The gate.check() return value maps to:
  - True  → execute the tool (call handler)
  - False → block, return "Permission denied" as the tool result
  - str   → block, return "Permission denied by user: {text}" (custom reply)
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

# deepagents → coderio tool-name translation (shared with HarnessMiddleware).
_NAME_MAP = {
    "execute": "bash",        # deepagents shell → coderio bash
    "write_todos": "todo",    # deepagents planning → coderio todo
    "ls": "list_dir",         # deepagents ls → coderio list_dir
}
# Arg key translation: deepagents write/edit use 'file_path', gate expects 'path'.
_ARG_FILE_KEY = "file_path"


def _to_coderio_name(name: str) -> str:
    return _NAME_MAP.get(name, name)


def _translate_args(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Normalize deepagents arg keys to coderio's gate expectations."""
    translated = dict(args)
    # write_file/edit_file use 'file_path'; gate's WorkspacePolicy reads 'path'.
    if _ARG_FILE_KEY in translated and "path" not in translated:
        translated["path"] = translated[_ARG_FILE_KEY]
    return translated


class PermissionMiddleware(AgentMiddleware):
    """Enforces coderio's permission gate inside a deepagents agent loop.

    Intercepts every tool call via wrap_tool_call. Calls gate.check() with the
    translated tool name + args BEFORE execution. Blocks destructive tools that
    the user hasn't approved, and enforces the workspace boundary in ALL modes.
    """

    def __init__(self, gate) -> None:
        self.gate = gate

    def wrap_tool_call(self, request, handler):
        """Check permission before executing; block if denied."""
        tc = getattr(request, "tool_call", None) or {}
        deep_name = tc.get("name", "")
        raw_args = dict(tc.get("args", {}) or {})

        # Translate to coderio's naming so gate.check + WorkspacePolicy work.
        coderio_name = _to_coderio_name(deep_name)
        coderio_args = _translate_args(deep_name, raw_args)

        decision = self.gate.check(coderio_name, coderio_args)
        if decision is True:
            return handler(request)

        # Denied (False) or custom reply (str). Build a ToolMessage so the model
        # gets a clear, actionable result it can react to.
        tool_call_id = tc.get("id", "")
        if decision is False:
            content = f"Permission denied: tool {coderio_name!r} blocked in {self.gate.mode} mode."
        else:
            # str — user's custom reply. Deny execution but feed the instruction.
            content = f"Permission denied by user: {decision}"
        return ToolMessage(content=content, tool_call_id=tool_call_id, name=deep_name)
