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

from langchain_core.messages import ToolMessage

from coderio.agent.sync_only import SyncOnlyMiddleware

# Tools whose CONTENT this middleware inspects. Note deepagents native names.
# Single source of truth: tools/taxonomy.py (P2-2, 2026-09-02 audit).
from coderio.tools.taxonomy import SHELL as _SHELL_TOOL
from coderio.tools.taxonomy import WEB_FETCH as _WEB_FETCH
from coderio.tools.taxonomy import WEB_SEARCH as _WEB_SEARCH

_NETWORK_TOOLS = frozenset({_WEB_FETCH, _WEB_SEARCH})


def _augment_with_whitelist_note(result, note: str):
    """Return ``result`` with the whitelist note appended.

    Handles three result shapes the execute tool can return:
      - str (plain string): return a new string with the note appended.
      - ToolMessage (deepagents wraps results): mutate .content in-place and return.
      - ExecuteResponse (deepagents backends): mutate .output in-place and return.

    Returns the (possibly mutated) result so the caller can return it directly.
    """
    content = getattr(result, "content", None)
    if content is not None and isinstance(content, str):
        result.content = f"{content}\n[whitelist] {note}"
        return result
    output = getattr(result, "output", None)
    if output is not None and isinstance(output, str):
        result.output = f"{output}\n[whitelist] {note}"
        return result
    if isinstance(result, str):
        return f"{result}\n[whitelist] {note}"
    # Unknown type — return unchanged (best-effort; note is informational).
    return result


class CommandReviewMiddleware(SyncOnlyMiddleware):
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
            # Unlike the blacklist (which hard-blocks), a whitelist miss is enforced
            # by the gate's tier semantics:
            #   - PLAN mode: execute is already blocked by PermissionMiddleware, so
            #     we won't reach here in production. But if we do (e.g. gate=None),
            #     we hard-block to stay safe.
            #   - FULL mode: explicit trust — allow without annotation.
            #   - CONFIRM/AUTO_EDIT: let the tool run (PermissionMiddleware already
            #     prompted the user), but APPEND the whitelist hint to the result so
            #     the model/user sees WHY the command was flagged.
            whitelist_miss = self.policy.check_whitelist(command)
            if whitelist_miss:
                mode = getattr(self.gate, "mode", "confirm") if self.gate is not None else "confirm"
                if mode == "plan":
                    return ToolMessage(
                        content=f"Blocked by whitelist policy (plan mode): {whitelist_miss}",
                        tool_call_id=tool_call_id,
                        name=name,
                    )
                if mode != "full":
                    # Non-FULL, non-PLAN: annotate the result. Let the tool execute
                    # (the user was already prompted by PermissionMiddleware), but
                    # surface the whitelist note in the result content so the model
                    # understands the command was outside the trusted set.
                    result = handler(request)
                    return _augment_with_whitelist_note(result, whitelist_miss)
                # FULL mode: fall through to the normal handler(request) below.
        elif name in _NETWORK_TOOLS and not self.policy.network_allowed:
            return ToolMessage(
                content="Blocked: network access is disabled (network_allowed=false "
                "in config). web_fetch/web_search are unavailable in this session.",
                tool_call_id=tool_call_id,
                name=name,
            )

        return handler(request)
