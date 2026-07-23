"""Unit tests for HarnessMiddleware (the deepagents adapter for coderio's harness).

These test the middleware in isolation — no deepagents graph, no model. They
verify the adapter correctly translates deepagents tool names, feeds ground truth
to the Harness, and intercepts termination via jump_to.
"""

from unittest.mock import MagicMock

from langchain_core.messages import AIMessage, HumanMessage

from coderio.agent.harness_middleware import HarnessMiddleware, _to_coderio_name


def _tool_call_msg(name, args, mid="c1", content=""):
    return AIMessage(
        content=content,
        tool_calls=[{"name": name, "args": args, "id": mid, "type": "tool_call"}],
    )


def _tool_call_request(name, args):
    """Build a minimal object resembling deepagents' ToolCallRequest."""
    req = MagicMock()
    req.tool_call = {"name": name, "args": args}
    return req


# --- name translation ---


def test_execute_maps_to_bash():
    assert _to_coderio_name("execute") == "bash"


def test_write_todos_maps_to_todo():
    assert _to_coderio_name("write_todos") == "todo"


def test_write_tools_pass_through():
    assert _to_coderio_name("write_file") == "write_file"
    assert _to_coderio_name("edit_file") == "edit_file"


# --- wrap_tool_call: observe ground truth ---


def test_wrap_tool_call_observes_write():
    mw = HarnessMiddleware()
    req = _tool_call_request("write_file", {"path": "a.py", "content": "x"})
    handler = lambda r: "Wrote 5 chars to a.py"
    mw.wrap_tool_call(req, handler)
    assert mw.harness.state.writes_since_verify == ["a.py"]


def test_wrap_tool_call_observes_execute_as_verification():
    """deepagents 'execute' (shell) must count as verification, clearing writes."""
    mw = HarnessMiddleware()
    mw.harness.observe("write_file", {"path": "a.py"}, "Wrote 1 chars")
    assert mw.harness.state.writes_since_verify == ["a.py"]
    req = _tool_call_request("execute", {"command": "python a.py"})
    handler = lambda r: "ok"
    mw.wrap_tool_call(req, handler)
    assert mw.harness.state.writes_since_verify == []  # cleared by execute


def test_wrap_tool_call_plan_gate_nudge_appended():
    """Writing with no todos appends a [nudge] to the result string."""
    mw = HarnessMiddleware()
    req = _tool_call_request("write_file", {"path": "a.py", "content": "x"})
    handler = lambda r: "Wrote 1 chars to a.py"
    result = mw.wrap_tool_call(req, handler)
    assert "[nudge]" in result


def test_wrap_tool_call_no_nudge_after_execute():
    """Once verified (execute ran), a subsequent write doesn't re-nudge if todos exist."""
    mw = HarnessMiddleware()
    # first write → nudge
    r1 = mw.wrap_tool_call(
        _tool_call_request("write_file", {"path": "a.py", "content": "x"}),
        lambda r: "Wrote 1 chars",
    )
    assert "[nudge]" in r1
    # execute clears writes
    mw.wrap_tool_call(_tool_call_request("execute", {"command": "python a.py"}), lambda r: "ok")
    # second write → no nudge (plan_nudged already True this turn)
    r2 = mw.wrap_tool_call(
        _tool_call_request("write_file", {"path": "b.py", "content": "y"}),
        lambda r: "Wrote 1 chars",
    )
    assert "[nudge]" not in r2


# --- after_model: termination interception ---


def _state_with_messages(msgs):
    return {"messages": msgs}


def test_after_model_intercepts_unverified_done():
    """Model wrote code (observed) then returns text-only (wants to end) → intercept."""
    mw = HarnessMiddleware()
    mw.harness.observe("write_file", {"path": "a.py"}, "Wrote 10 chars")
    state = _state_with_messages(
        [
            HumanMessage(content="write a.py"),
            _tool_call_msg("write_file", {"path": "a.py", "content": "x"}),
            AIMessage(content="done", tool_calls=[]),  # wants to end, unverified
        ]
    )
    update = mw.after_model(state, None)
    assert update is not None
    assert update.get("jump_to") == "model"
    assert update["messages"], "must inject a continuation message"
    assert (
        "bash" in update["messages"][0].content
        or "execute" in update["messages"][0].content
        or "verify" in update["messages"][0].content.lower()
    )


def test_after_model_passes_when_verified():
    """After execute (verification), model may end normally → no interception."""
    mw = HarnessMiddleware()
    mw.harness.observe("write_file", {"path": "a.py"}, "Wrote 10 chars")
    mw.harness.observe("bash", {"command": "python a.py"}, "ok")  # verified
    state = _state_with_messages([AIMessage(content="done, verified", tool_calls=[])])
    update = mw.after_model(state, None)
    assert update is None


def test_after_model_no_intercept_when_tool_calls_present():
    """If the model is still calling tools (not ending), don't intercept."""
    mw = HarnessMiddleware()
    mw.harness.observe("write_file", {"path": "a.py"}, "Wrote 10 chars")
    state = _state_with_messages([_tool_call_msg("execute", {"command": "x"})])
    update = mw.after_model(state, None)
    assert update is None


def test_after_model_escalation_releases_with_warning():
    """After 2 interceptions, the gate releases and fires on_harness_warn."""
    warnings = []

    class _Stream:
        def on_harness_warn(self, msg):
            warnings.append(msg)

    mw = HarnessMiddleware(stream=_Stream())
    mw.harness.observe("write_file", {"path": "a.py"}, "Wrote 10 chars")
    state = _state_with_messages([AIMessage(content="done", tool_calls=[])])
    # attempt 0, 1 → intercept; attempt 2 → release + warn
    u0 = mw.after_model(state, None)
    u1 = mw.after_model(state, None)
    u2 = mw.after_model(state, None)
    assert u0 is not None and u0.get("jump_to") == "model"
    assert u1 is not None and u1.get("jump_to") == "model"
    assert u2 is None  # released
    assert warnings, "must fire a warning on escalation release"


def test_after_model_text_only_no_writes_passes():
    """Pure Q&A (no writes) passes through — harness only cares about code writes."""
    mw = HarnessMiddleware()
    state = _state_with_messages([AIMessage(content="The answer is 42.", tool_calls=[])])
    update = mw.after_model(state, None)
    assert update is None


def test_disabled_middleware_passthrough():
    """When disabled, after_model and wrap_tool_call are no-ops."""
    mw = HarnessMiddleware(enabled=False)
    mw.harness.observe("write_file", {"path": "a.py"}, "Wrote 10 chars")  # no-op when disabled
    state = _state_with_messages([AIMessage(content="done", tool_calls=[])])
    assert mw.after_model(state, None) is None
