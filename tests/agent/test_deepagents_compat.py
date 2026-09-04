"""Tests for the deepagents compatibility layer.

Covers the research subagent's whitelist-first tool isolation (2026-08-07
report P2-9): the subagent must only see explicitly-allowed read-only tools,
not inherit everything and have dangerous tools blacklisted.
"""

from __future__ import annotations

from coderio.agent._deepagents_compat import (
    _tool_name,
    _ToolWhitelistMiddleware,
    make_research_subagent_middleware,
)


class _FakeTool:
    """Stand-in for a langchain BaseTool (only needs .name)."""

    def __init__(self, name: str) -> None:
        self.name = name


class _FakeRequest:
    """Minimal stand-in for deepagents' ModelRequest — just .tools + .override."""

    def __init__(self, tools: list) -> None:
        self.tools = tools

    def override(self, **kw):
        return _FakeRequest(kw.get("tools", self.tools))


def test_tool_name_extracts_from_object_and_dict():
    assert _tool_name(_FakeTool("read_file")) == "read_file"
    assert _tool_name({"name": "ls"}) == "ls"
    assert _tool_name({"name": 123}) is None
    assert _tool_name({}) is None
    assert _tool_name(object()) is None


def test_whitelist_keeps_only_allowed_tools():
    """The whitelist middleware must filter to ONLY the allowed set — any tool
    not in the whitelist (including hypothetical new destructive tools) is
    stripped before the model sees them."""
    mw = _ToolWhitelistMiddleware(allowed=frozenset({"read_file", "ls"}))
    tools = [
        _FakeTool("read_file"),
        _FakeTool("write_file"),  # not allowed → stripped
        _FakeTool("execute"),  # not allowed → stripped
        _FakeTool("ls"),
        _FakeTool("hypothetical_new_delete_tool"),  # not allowed → stripped
    ]
    req = _FakeRequest(tools)
    seen: list[str] = []
    mw.wrap_model_call(req, lambda r: seen.extend(t.name for t in r.tools))
    assert seen == ["read_file", "ls"], f"only allowed tools must survive, got {seen}"


def test_whitelist_blocks_future_destructive_tools_by_default():
    """REGRESSION guard (P2-9): if deepagents adds a new destructive tool in a
    future version, the whitelist approach must block it by default. The old
    blacklist (exclude write_file/edit_file/execute/write_todos) would have
    leaked it until coderio was updated."""
    mw = _ToolWhitelistMiddleware(allowed=frozenset({"read_file"}))
    tools = [_FakeTool("rm_rf"), _FakeTool("read_file"), _FakeTool("network_post")]
    req = _FakeRequest(tools)
    seen: list[str] = []
    mw.wrap_model_call(req, lambda r: seen.extend(t.name for t in r.tools))
    assert seen == ["read_file"], "unknown tools must be blocked by the whitelist"


def test_research_subagent_middleware_is_whitelist():
    """make_research_subagent_middleware must return a _ToolWhitelistMiddleware
    (not the old blacklist _ToolExclusionMiddleware)."""
    mw_list = make_research_subagent_middleware()
    assert len(mw_list) == 1
    assert isinstance(mw_list[0], _ToolWhitelistMiddleware)


def test_research_subagent_only_gets_readonly_tools():
    """The research subagent's allowed set must be read-only — no write, no
    execute, no note, no write_todos. Verify by filtering a full tool set."""
    mw_list = make_research_subagent_middleware()
    mw = mw_list[0]
    all_tools = [
        _FakeTool("read_file"),
        _FakeTool("write_file"),
        _FakeTool("edit_file"),
        _FakeTool("multi_edit"),
        _FakeTool("execute"),
        _FakeTool("ls"),
        _FakeTool("glob"),
        _FakeTool("grep"),
        _FakeTool("write_todos"),
        _FakeTool("web_search"),
        _FakeTool("web_fetch"),
        _FakeTool("note"),
    ]
    req = _FakeRequest(all_tools)
    seen: list[str] = []
    mw.wrap_model_call(req, lambda r: seen.extend(t.name for t in r.tools))
    # No write/execute/note tools may survive.
    forbidden = {"write_file", "edit_file", "multi_edit", "execute", "write_todos", "note"}
    leaked = forbidden & set(seen)
    assert not leaked, f"research subagent must not access write/exec tools, leaked: {leaked}"
    # Must include the core read-only set.
    for required in ("read_file", "ls", "glob", "grep"):
        assert required in seen, f"research subagent must allow {required}"


# --------------------------------------------------------------- state-key adapter


def test_todos_state_key_constant():
    """TODOS_STATE_KEY must match langchain's PlanningState.todos field name.

    If langchain renames this field upstream, this constant is the single
    source of truth — update it here and all get_state_todos() callers follow.
    """
    from coderio.agent._deepagents_compat import TODOS_STATE_KEY

    assert TODOS_STATE_KEY == "todos"


def test_get_state_todos_from_dict():
    """get_state_todos reads 'todos' from a dict-shaped state (the langgraph norm)."""
    from coderio.agent._deepagents_compat import get_state_todos

    state = {"messages": [], "todos": [{"content": "a", "status": "pending"}]}
    todos = get_state_todos(state)
    assert todos == [{"content": "a", "status": "pending"}]


def test_get_state_todos_from_object():
    """get_state_todos falls back to attribute access for object-shaped state."""
    from coderio.agent._deepagents_compat import get_state_todos

    class _ObjState:
        todos = [{"content": "x", "status": "completed"}]

    todos = get_state_todos(_ObjState())
    assert todos == [{"content": "x", "status": "completed"}]


def test_get_state_todos_missing_returns_none():
    """Missing todos key → None (caller treats None as 'nothing to sync')."""
    from coderio.agent._deepagents_compat import get_state_todos

    assert get_state_todos({"messages": []}) is None
    assert get_state_todos(object()) is None  # no .todos attribute


def test_get_state_todos_empty_list_is_truthy_enough():
    """An explicit empty list is returned as-is (not None) — distinguishes
    'no todos key' from 'todos key present but empty'."""
    from coderio.agent._deepagents_compat import get_state_todos

    assert get_state_todos({"todos": []}) == []


# --- v3 #11: deny-all fallback + research permission middleware ---


def test_whitelist_construction_failure_degrades_to_deny_all(monkeypatch):
    """REGRESSION (v3 #11): whitelist-construction failure used to return []
    (NO middleware — the research subagent inherited EVERY tool including
    write/execute). Now it degrades to a deny-all middleware."""
    from coderio.agent import _deepagents_compat as compat

    def _boom(**kwargs):
        raise RuntimeError("API changed")

    monkeypatch.setattr(compat, "_ToolWhitelistMiddleware", _boom)
    mw = compat.make_research_subagent_middleware()
    assert len(mw) == 1
    assert type(mw[0]).__name__ == "_DenyAllToolsMiddleware"


def test_deny_all_middleware_overrides_to_empty_tools():
    """_DenyAllToolsMiddleware strips every tool from the request."""
    from coderio.agent._deepagents_compat import _DenyAllToolsMiddleware

    class _Req:
        tools = [1, 2, 3]

        def override(self, **kw):
            if "tools" in kw:
                self.tools = kw["tools"]
            return self

    mw = _DenyAllToolsMiddleware()
    seen = {}

    def handler(req):
        seen["tools"] = req.tools
        return "ok"

    assert mw.wrap_model_call(_Req(), handler) == "ok"
    assert seen["tools"] == []


def test_whitelist_runtime_error_degrades_to_no_tools():
    """A runtime failure inside wrap_model_call (e.g. _tool_name exploding on
    a weird tool object) degrades to an EMPTY tool set, not fail-open."""
    from coderio.agent._deepagents_compat import _ToolWhitelistMiddleware

    class _EvilTool:
        # no .name, not a dict — makes _tool_name raise
        pass

    class _Req:
        tools = [_EvilTool()]

        def override(self, **kw):
            self.tools = kw.get("tools", self.tools)
            return self

    mw = _ToolWhitelistMiddleware(allowed=frozenset({"read_file"}))
    seen = {}

    def handler(req):
        seen["tools"] = req.tools
        return "ok"

    mw.wrap_model_call(_Req(), handler)
    assert seen["tools"] == [], "runtime whitelist failure must degrade to NO tools"


def test_research_subagent_carries_permission_and_review():
    """v3 #11: research now carries PermissionMiddleware + CommandReviewMiddleware
    (execution-time enforcement under the visibility whitelist)."""
    import inspect

    from coderio.agent.deep_loop import _build_research_subagent

    # The function must NOT accept a 'gate' parameter — the research subagent
    # uses its own hardcoded PLAN gate, period.
    sig = inspect.signature(_build_research_subagent)
    assert "gate" not in sig.parameters, "research subagent must not accept external gate param"

    spec = _build_research_subagent(command_policy=None)
    mw = [type(m).__name__ for m in spec["middleware"]]
    assert "PermissionMiddleware" in mw, mw
    assert "CommandReviewMiddleware" in mw, mw
    # The research subagent must use its own hardcoded PLAN gate, not the
    # caller's — a FULL-mode caller must not be able to upgrade it.
    perm = next(m for m in spec["middleware"] if type(m).__name__ == "PermissionMiddleware")
    assert perm.gate.mode == "plan", f"research subagent must always be PLAN, got {perm.gate.mode}"
