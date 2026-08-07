"""Command-content review as a deepagents AgentMiddleware.

Sits in the same middleware chain as PermissionMiddleware, AFTER it. While
PermissionMiddleware decides based on TOOL TYPE + MODE (which tier), this
middleware inspects the CONTENT of shell commands and network calls — blocking
destructive patterns (``rm -rf /``, ``mkfs``, fork bombs) regardless of the
permission mode.

This is NOT a real OS sandbox: a determined model can obfuscate commands to
bypass regex matching. The goal is to stop accidental/careless destruction,
which is the vast majority of real-world incidents. See command_policy.py for
the pattern list and the limitations.

Runs even in FULL mode: safety takes priority over the "FULL = allow
everything" literal semantics. A user who selects FULL accepts that file edits
and shell execution happen without prompts, but not that ``rm -rf /`` proceeds
without even a logged block.
"""

from __future__ import annotations

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import ToolMessage

# Tools whose CONTENT this middleware inspects. Note deepagents native names.
_SHELL_TOOL = "execute"
_NETWORK_TOOLS = frozenset({"web_fetch", "web_search"})


class CommandReviewMiddleware(AgentMiddleware):
    """Inspects shell commands and network calls against a CommandPolicy.

    Sibling to PermissionMiddleware. Both run in wrap_tool_call; this one is
    content-focused (regex on the command string), the other is tier-focused
    (plan/confirm/full mode). Order: PermissionMiddleware first (tier gate),
    then this (content gate) — so we only inspect commands that already passed
    the tier check. But the content rules are mode-independent: even FULL mode
    blocks ``rm -rf /``.
    """

    def __init__(self, policy) -> None:
        self.policy = policy

    def wrap_tool_call(self, request, handler):
        """Inspect execute/web commands; block if the policy says no."""
        tc = getattr(request, "tool_call", None) or {}
        name = tc.get("name", "")
        args = dict(tc.get("args", {}) or {})
        tool_call_id = tc.get("id", "")

        if name == _SHELL_TOOL:
            command = str(args.get("command", ""))
            violation = self.policy.check_command(command)
            if violation:
                # Surface the reason so the model understands WHY and can
                # reformulate — e.g. "rm -rf /build" is fine, "rm -rf /" is not.
                return ToolMessage(
                    content=f"Blocked by command policy: {violation}. "
                    "Reformulate the command to avoid the destructive pattern "
                    "(e.g. target a specific subdirectory, not root/home).",
                    tool_call_id=tool_call_id,
                    name=name,
                )
        elif name in _NETWORK_TOOLS and not self.policy.network_allowed:
            return ToolMessage(
                content="Blocked: network access is disabled (network_allowed=false "
                "in config). web_fetch/web_search are unavailable in this session.",
                tool_call_id=tool_call_id,
                name=name,
            )

        return handler(request)
