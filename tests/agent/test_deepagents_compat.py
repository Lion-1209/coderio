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
