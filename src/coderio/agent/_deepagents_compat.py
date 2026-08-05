"""Compatibility layer for deepagents internal APIs.

Centralizes all usage of deepagents internals (non-public APIs) so that:
1. There's a single file to update when deepagents changes its internals.
2. Version-specific breakage produces a clear error message instead of a
   cryptic AttributeError/ImportError deep in a call stack.
3. Future migration to public APIs (if/when available) is a single-file change.

Current internal dependencies:
- BASE_AGENT_PROMPT (deepagents.graph): module-level string, monkey-patched
  to empty to prevent prompt conflicts. Public alternative not yet available.
- _ToolExclusionMiddleware (deepagents.middleware._tool_exclusion): private
  class used to strip write/execute tools from the research subagent. Public
  alternative not yet available.

If either import fails, we degrade gracefully (BASE_AGENT_PROMPT patching
is skipped; research subagent gets no tool exclusion) rather than crashing.
"""

from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def neutralize_base_prompt() -> bool:
    """Set deepagents' BASE_AGENT_PROMPT to empty string.

    create_deep_agent appends BASE_AGENT_PROMPT after the caller's system_prompt,
    causing conflicts (e.g. 'explore first' vs 'match effort', macOS path
    examples vs virtual paths). coderio's prompt covers everything — setting
    BASE to empty ensures only coderio's prompt reaches the model.

    Returns True if successful, False if the internal API has changed (in
    which case the duplicate prompt will appear but won't crash).
    """
    try:
        import deepagents.graph as _dg_graph

        if hasattr(_dg_graph, "BASE_AGENT_PROMPT") and _dg_graph.BASE_AGENT_PROMPT:
            _dg_graph.BASE_AGENT_PROMPT = ""
            return True
    except Exception as e:
        _log.warning("Could not neutralize BASE_AGENT_PROMPT (deepagents API may have changed): %s", e)
    return False


def get_tool_exclusion_middleware():
    """Return the _ToolExclusionMiddleware class, or None if unavailable.

    Used by the research subagent to physically strip write/execute tools.
    If this private API is removed in a future deepagents version, the
    research subagent will still work but its 'read-only' claim will only
    be prompt-level (not enforced at the tool layer).
    """
    try:
        from deepagents.middleware._tool_exclusion import _ToolExclusionMiddleware

        return _ToolExclusionMiddleware
    except ImportError:
        _log.warning(
            "_ToolExclusionMiddleware not found (deepagents API may have changed). "
            "Research subagent tool isolation disabled."
        )
        return None


def make_research_subagent_middleware():
    """Build the middleware list for the research subagent.

    Returns a list with a _ToolExclusionMiddleware if available, or an empty
    list if the private API is unavailable (subagent inherits all tools —
    less safe but won't crash).
    """
    cls = get_tool_exclusion_middleware()
    if cls is not None:
        return [cls(excluded=frozenset({"write_file", "edit_file", "execute", "write_todos"}))]
    return []
