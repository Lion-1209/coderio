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
    # The injected message must reference deepagents' shell tool name ('execute'),
    # NOT 'bash' — the harness.py prose says "use bash" but the model must call
    # the tool that actually exists in the deepagents backend.
    inject_text = update["messages"][0].content
    assert "execute" in inject_text.lower(), f"inject must say 'execute', got: {inject_text!r}"
    assert "bash" not in inject_text.lower(), f"inject must NOT say 'bash', got: {inject_text!r}"
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


def test_phase_tracker_wired_when_stream_supports_it():
    """REGRESSION (2026-08-07 report P1-2): the AgentStateTracker was never
    instantiated in production — Harness.state_tracker stayed None, so the TUI
    status bar's phase slot was always empty despite README advertising an
    'explicit state machine'. When the stream declares on_phase_change, the
    middleware must wire a tracker so observe()/check_termination drive phase
    transitions."""
    phases: list[str] = []

    class _PhaseStream:
        def on_phase_change(self, state: str, step: int, hint: str) -> None:
            phases.append(state)

    mw = HarnessMiddleware(stream=_PhaseStream())
    assert mw.harness.state_tracker is not None, "tracker must be wired"
    # A write should fire a PLAN transition (writes exist, no todos).
    mw.harness.observe("write_file", {"path": "a.py"}, "Wrote 10 chars")
    assert "plan" in phases, f"write should fire plan phase, got {phases}"
    # Turn end should fire COMPLETE.
    state = _state_with_messages([AIMessage(content="done", tool_calls=[])])
    # Drive it to release (3 after_model calls — escalates after MAX=2).
    mw.after_model(state, None)
    mw.after_model(state, None)
    mw.after_model(state, None)
    assert "complete" in phases, f"turn end should fire complete, got {phases}"


def test_phase_tracker_not_wired_without_stream():
    """No stream (or a stream without on_phase_change) → no tracker overhead,
    no session-jsonl timeline pollution. Headless tests / NullStream stay quiet."""
    mw = HarnessMiddleware()  # no stream
    assert mw.harness.state_tracker is None

    class _BareStream:
        pass

    mw2 = HarnessMiddleware(stream=_BareStream())
    assert mw2.harness.state_tracker is None, "stream without on_phase_change = no tracker"


# --- after_model: checkpoint-recovery todos sync (P1-1) ---


def test_after_model_syncs_state_todos_into_harness():
    """Graph state todos must be synced into the harness's TodoStore on each
    after_model call.

    This covers the checkpoint-resume path: write_todos ran in a PREVIOUS turn
    (persisted to graph state via sqlite checkpoint), but the HarnessMiddleware
    was recreated for this run_deep_agent call (its TodoStore starts empty).
    Without this sync, CompletionGate would see an empty todo list and let the
    model end despite pending todos — a silent regression of the harness's
    hard constraint.

    P1-1 (2026-08-10 report): the state.get('todos') read is now centralized in
    _deepagents_compat.get_state_todos so a langchain key rename upstream is a
    single-file fix, not a scattered silent failure.
    """
    mw = HarnessMiddleware()
    # Simulate a state restored from checkpoint: messages + a pending todo list.
    state = {
        "messages": [AIMessage(content="all done", tool_calls=[])],
        "todos": [
            {"content": "implement feature X", "status": "completed"},
            {"content": "write tests for X", "status": "pending"},
        ],
    }
    # The harness's TodoStore starts empty (rebuilt each run_deep_agent call).
    assert mw.harness.todos.todos == []

    mw.after_model(state, None)

    # The sync must populate the harness's TodoStore from graph state.
    synced = mw.harness.todos.todos
    assert len(synced) == 2, f"expected 2 todos synced from state, got {len(synced)}"
    assert synced[0].content == "implement feature X"
    assert synced[0].status == "completed"
    assert synced[1].content == "write tests for X"
    assert synced[1].status == "pending"


def test_after_model_no_sync_when_state_has_no_todos():
    """No todos key in state → no sync, harness TodoStore stays as-is (empty)."""
    mw = HarnessMiddleware()
    state = {"messages": [AIMessage(content="done", tool_calls=[])]}  # no "todos" key
    mw.after_model(state, None)
    assert mw.harness.todos.todos == []


def test_after_model_sync_handles_empty_todo_list():
    """An explicit empty todos list in state syncs to an empty TodoStore (not an
    error). Distinguishes 'no todos key' (None) from 'todos present but empty'."""
    mw = HarnessMiddleware()
    state = {"messages": [AIMessage(content="done", tool_calls=[])], "todos": []}
    mw.after_model(state, None)
    assert mw.harness.todos.todos == []


# --- MIDDLEWARE-LAYER CONTRACT tests (2026-08-14 v2 audit follow-up) ---
# The test_harness.py CONTRACT tests pin the FORMAT STRINGS ([Command failed
# with exit code N]) by calling h.observe("bash", ..., str) directly. But the
# production path delivers a langchain ToolMessage OBJECT to wrap_tool_call —
# if upstream changes the object shape such that _result_to_text can't extract
# .content anymore, those string tests still pass while production breaks
# (exactly how P0-1 stayed hidden). These tests pin the OBJECT SHAPE: a real
# ToolMessage flows through wrap_tool_call and the harness must still parse
# the exit code out of its content.


def _toolmessage_result(content: str):
    """A real langchain ToolMessage, as deepagents' filesystem middleware
    returns from the execute tool (deepagents/middleware/filesystem.py:1759)."""
    from langchain_core.messages import ToolMessage

    return ToolMessage(content=content, tool_call_id="tc-contract-1", name="execute")


def test_contract_toolmessage_failed_test_does_not_clear_writes():
    """Real ToolMessage with a FAILED exit code → writes_since_verify stays.

    This is the full production shape: execute returns ToolMessage (not
    ExecuteResponse), middleware extracts content via _result_to_text, the
    harness parses the exit marker from that text. If deepagents changes the
    wrapper type again, this test breaks HERE instead of in production.
    """
    mw = HarnessMiddleware()
    mw.wrap_tool_call(
        _tool_call_request("write_file", {"path": "a.py", "content": "x"}),
        lambda r: "Wrote 1 chars",
    )
    assert mw.harness.state.writes_since_verify == ["a.py"]

    result = _toolmessage_result("1 failed, 1 passed\n[Command failed with exit code 1]")
    mw.wrap_tool_call(_tool_call_request("execute", {"command": "pytest -q"}), lambda r: result)

    assert mw.harness.state.writes_since_verify == ["a.py"], (
        "a FAILED test delivered as a real ToolMessage must NOT clear unverified writes"
    )


def test_contract_toolmessage_passed_test_clears_writes():
    """Real ToolMessage with exit 0 → writes cleared, verification counted."""
    mw = HarnessMiddleware()
    mw.wrap_tool_call(
        _tool_call_request("write_file", {"path": "a.py", "content": "x"}),
        lambda r: "Wrote 1 chars",
    )
    result = _toolmessage_result("2 passed\n[Command succeeded with exit code 0]")
    mw.wrap_tool_call(_tool_call_request("execute", {"command": "pytest -q"}), lambda r: result)

    assert mw.harness.state.writes_since_verify == [], (
        "a PASSED test delivered as a real ToolMessage SHOULD clear unverified writes"
    )


def test_contract_toolmessage_permission_denied_does_not_clear_writes():
    """Real ToolMessage whose content is a permission denial (no exit marker)
    → must NOT count as verification (v2 audit bypass #1, object-shape form)."""
    mw = HarnessMiddleware()
    mw.wrap_tool_call(
        _tool_call_request("write_file", {"path": "a.py", "content": "x"}),
        lambda r: "Wrote 1 chars",
    )
    result = _toolmessage_result("Permission denied: tool 'execute' blocked in plan mode.")
    mw.wrap_tool_call(_tool_call_request("execute", {"command": "pytest -q"}), lambda r: result)

    assert mw.harness.state.writes_since_verify == ["a.py"], (
        "permission-denied execute delivered as a real ToolMessage must NOT clear writes"
    )
