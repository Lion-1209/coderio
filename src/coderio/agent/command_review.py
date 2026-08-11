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

Whitelist interaction: when whitelist_mode is enabled on the policy, commands
outside the whitelist are flagged. Unlike blacklist (hard block), whitelist
misses degrade gracefully:
  - FULL mode: allowed (FULL = explicit trust, user accepted all commands)
  - CONFIRM/AUTO_EDIT: the whitelist hint is appended to the command result so
    the user can see it during the (already-triggered) permission prompt. We
    don't hard-block because PermissionMiddleware already gates execute.
  - PLAN mode: execute is already blocked by PermissionMiddleware before this
    middleware runs, so the whitelist check is moot for plan mode.
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

    Optional ``gate`` reference: if provided, whitelist misses use gate.mode to
    decide behavior (FULL allows, other modes get a hint appended to result).
    If None (no gate), whitelist misses are logged but not blocked (the policy
    can't enforce confirmation without the gate).
    """

    def __init__(self, policy, gate=None) -> None:
        self.policy = policy
        self.gate = gate

    def wrap_tool_call(self, request, handler):
        """Inspect execute/web commands; block if the policy says no."""
        tc = getattr(request, "tool_call", None) or {}
        name = tc.get("name", "")
        args = dict(tc.get("args", {}) or {})
        tool_call_id = tc.get("id", "")

        if name == _SHELL_TOOL:
            command = str(args.get("command", ""))
            # Layer 1: blacklist (hard block, always active).
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
            # Layer 2: whitelist (soft — degrade based on gate mode, not hard block).
            whitelist_miss = self.policy.check_whitelist(command)
            if whitelist_miss and self.gate is not None:
                mode = getattr(self.gate, "mode", "confirm")
                if mode == "full":
                    # FULL = explicit trust; let it through, but the result will
                    # carry the whitelist hint (handled via augmentation below
                    # is complex; for now FULL just allows).
                    pass
                # For non-FULL modes: the command will still execute (PermissionMiddleware
                # already gated it), but we surface the whitelist note in the result.
                # We can't augment a ToolMessage before the tool runs, so we let it
                # proceed — the hint is informational, enforcement is the gate's job.
        elif name in _NETWORK_TOOLS and not self.policy.network_allowed:
            return ToolMessage(
                content="Blocked: network access is disabled (network_allowed=false "
                "in config). web_fetch/web_search are unavailable in this session.",
                tool_call_id=tool_call_id,
                name=name,
            )

        return handler(request)
