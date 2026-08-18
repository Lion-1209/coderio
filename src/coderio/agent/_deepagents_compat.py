"""Compatibility layer for deepagents internal APIs.

Centralizes all usage of deepagents internals (non-public APIs) so that:
1. There's a single file to update when deepagents changes its internals.
2. Version-specific breakage produces a clear error message instead of a
   cryptic AttributeError/ImportError deep in a call stack.
3. Future migration to public APIs (if/when available) is a single-file change.

Current internal dependencies:
- BASE_AGENT_PROMPT (deepagents.graph): module-level string, monkey-patched
  to empty to prevent prompt conflicts. Public alternative not yet available.
- PlanningState.todos state key (langchain.agents.middleware.todo): the graph
  state field where write_todos persists its output. The key name "todos" is
  an undocumented implementation detail of langchain's planning middleware —
  centralized here (TODOS_STATE_KEY + get_state_todos) so a rename upstream
  only requires updating this one constant instead of scattered state.get()
  reads across harness_middleware.

The research subagent's tool isolation (_ToolWhitelistMiddleware below) is
NOT a deepagents internal dependency — it subclasses the public AgentMiddleware
base and uses the documented ModelRequest.override mechanism. The old
_ToolExclusionMiddleware (blacklist) was removed in favor of this whitelist
approach (2026-08-07 report P2-9).

If BASE_AGENT_PROMPT patching fails, we degrade gracefully (the duplicate
prompt will appear but won't crash).
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware

_log = logging.getLogger(__name__)

# Graph state key under which langchain's planning middleware (TodoListMiddleware)
# persists the todo list. Sourced from langchain.agents.middleware.todo.PlanningState
# (the "todos: Annotated[NotRequired[list[Todo]], OmitFromInput]" field). This key
# name is an undocumented implementation detail — if langchain renames it (e.g. to
# "todo_list"), only this constant needs updating, not every state.get("todos") call.
TODOS_STATE_KEY = "todos"


def get_state_todos(state: Any) -> list | None:
    """Read the todos list from graph state, dict-or-object agnostic.

    Centralizes the state.get("todos") read so that a key rename upstream
    produces a single-file fix (update TODOS_STATE_KEY) instead of scattered
    silent failures across harness_middleware. Returns None when the key is
    absent (the caller treats None as "no todos to sync").
    """
    if hasattr(state, "get"):
        return state.get(TODOS_STATE_KEY)
    return getattr(state, TODOS_STATE_KEY, None)


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


def _tool_name(tool: Any) -> str | None:
    """Extract the name from a BaseTool or dict tool (mirrors deepagents' helper)."""
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


class _ToolWhitelistMiddleware(AgentMiddleware):
    """Whitelist-first tool filter — the model only sees tools in ``allowed``.

    Safer than the blacklist approach (``_ToolExclusionMiddleware``) because a
    new destructive tool added by deepagents in a future version is
    automatically blocked (it's not in the whitelist). The blacklist would
    leak it until coderio is updated to exclude the new name.

    Wraps the same ModelRequest.override + filter mechanism as
    ``_ToolExclusionMiddleware`` but inverts the predicate.

    Runtime failures degrade to an EMPTY tool set (deny-all), never to the
    unfiltered original — a broken research subagent is preferable to a
    research subagent that can suddenly see write/execute tools (v3 audit #11
    reversed the old fail-open direction).
    """

    def __init__(self, *, allowed: frozenset[str]) -> None:
        self._allowed = allowed

    def wrap_model_call(self, request, handler):
        try:
            filtered = [t for t in request.tools if _tool_name(t) in self._allowed]
            return handler(request.override(tools=filtered))
        except Exception as e:  # noqa: BLE001 — degrade to deny-all, never fail-open
            _log.warning("tool whitelist failed at runtime (degrading to NO tools): %s", e)
            return handler(request.override(tools=[]))

    async def awrap_model_call(self, request, handler):
        try:
            filtered = [t for t in request.tools if _tool_name(t) in self._allowed]
            return await handler(request.override(tools=filtered))
        except Exception as e:  # noqa: BLE001 — degrade to deny-all, never fail-open
            _log.warning("tool whitelist failed at runtime (degrading to NO tools): %s", e)
            return await handler(request.override(tools=[]))


class _DenyAllToolsMiddleware(AgentMiddleware):
    """Last-resort middleware: the model sees ZERO tools.

    Returned when the whitelist middleware itself cannot be constructed — the
    research subagent becomes useless but stays harmless (v3 audit #11: the
    old fallback was an EMPTY middleware list, i.e. the subagent inherited
    EVERY tool including write/execute — fail-open in the most dangerous
    direction).
    """

    def wrap_model_call(self, request, handler):
        return handler(request.override(tools=[]))

    async def awrap_model_call(self, request, handler):
        return await handler(request.override(tools=[]))


def make_research_subagent_middleware():
    """Build the middleware list for the research subagent.

    WHITELIST approach (2026-08-07 report P2-9): the research subagent can
    ONLY use tools in the explicit read-only set below. This is safer than the
    old blacklist (exclude write_file/edit_file/execute/write_todos) because
    any new tool deepagents adds is blocked by default until explicitly
    allowlisted here.

    Returns a list with a _ToolWhitelistMiddleware. If the deepagents
    AgentMiddleware base class API is unavailable (very old or very new
    version), falls back to _DenyAllToolsMiddleware — the subagent sees no
    tools rather than all of them (fail-closed; v3 audit #11).
    """
    # Read-only tools the research subagent is allowed to use. Add new tools
    # here ONLY after confirming they're safe for a read-only agent.
    allowed = frozenset({"read_file", "ls", "glob", "grep", "web_search"})
    try:
        return [_ToolWhitelistMiddleware(allowed=allowed)]
    except Exception as e:  # noqa: BLE001 — AgentMiddleware API may differ
        _log.warning("Could not build _ToolWhitelistMiddleware (deny-all fallback): %s", e)
        try:
            return [_DenyAllToolsMiddleware()]
        except Exception:  # noqa: BLE001 — even the fallback construction failed
            return []
