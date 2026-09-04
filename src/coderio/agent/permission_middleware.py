"""Permission gate as a deepagents AgentMiddleware.

Wraps coderio's PermissionGate (the 4-tier access system: plan/confirm/
auto_edit/full) so it runs BEFORE every tool execution inside a deepagents
agent loop.

No path translation or WorkspacePolicy — file path isolation is handled by
deepagents' backend virtual_mode (/foo.py → {root}/foo.py, forced
relative_to(root) check). This middleware only controls WHICH tool types
may execute based on the permission mode.
"""

from __future__ import annotations

from langchain_core.messages import ToolMessage

from coderio.agent.sync_only import SyncOnlyMiddleware


class PermissionMiddleware(SyncOnlyMiddleware):
    """Enforces coderio's permission gate inside a deepagents agent loop.

    Intercepts every tool call via wrap_tool_call. Calls gate.check() with
    the tool name + args BEFORE execution. The gate decides based on tool
    type + permission mode (FULL/CONFIRM/AUTO_EDIT/PLAN).

    Tool names are passed as-is (deepagents native names like 'execute',
    'write_file', 'edit_file'). The tool classification sets in base.py
    use these same native names.
    """

    def __init__(self, gate) -> None:
        self.gate = gate

    def wrap_tool_call(self, request, handler):
        """Check permission before executing; block if denied."""
        tc = getattr(request, "tool_call", None) or {}
        name = tc.get("name", "")
        args = dict(tc.get("args", {}) or {})

        decision = self.gate.check(name, args)
        if decision is True:
            return handler(request)

        # Denied (False) or custom reply (str). Build a ToolMessage so the model
        # gets a clear, actionable result it can react to.
        tool_call_id = tc.get("id", "")
        if decision is False:
            content = f"Permission denied: tool {name!r} blocked in {self.gate.mode} mode."
        else:
            # str — user's custom reply. Deny execution but feed the instruction.
            content = f"Permission denied by user: {decision}"
        return ToolMessage(content=content, tool_call_id=tool_call_id, name=name)
