"""Unit tests for the harness gates (spec §7 matrix, first 9 rows).

These test Harness in isolation — no loop, no model. The loop-integration tests
(those that check the harness actually intercepts run_agent) live in test_loop.py.
"""
from coderio.agent.harness import Harness, HarnessState
from coderio.tools.todo import TodoStore, Todo


# ----------------------------------------------------------------- helpers
def _harness(todos=None, enabled=True):
    return Harness(state=HarnessState(), todos=todos or TodoStore(), enabled=enabled)


def _add_todo(store, content="do thing", status="pending"):
    store.todos.append(Todo(content=content, status=status))
    return store


# --------------------------------------------------------------- observe()
def test_observe_records_successful_write():
    h = _harness()
    h.observe("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    assert h.state.writes_since_verify == ["a.py"]


def test_observe_records_edit_and_multi_edit():
    h = _harness()
    h.observe("edit_file", {"path": "b.py"}, "Edited b.py: replaced 1 occurrence(s)")
    h.observe("multi_edit", {"path": "c.py"}, "Edited c.py: applied 2 edit(s)")
    assert h.state.writes_since_verify == ["b.py", "c.py"]


def test_observe_ignores_failed_write():
    h = _harness()
    h.observe("write_file", {"path": "a.py"}, "Error: something broke")
    assert h.state.writes_since_verify == []


def test_observe_dedupes_path():
    h = _harness()
    h.observe("write_file", {"path": "a.py"}, "Wrote 1 chars to a.py")
    h.observe("edit_file", {"path": "a.py"}, "Edited a.py: replaced 1 occurrence(s)")
    assert h.state.writes_since_verify == ["a.py"]


def test_observe_bash_clears_writes_and_resets_attempts():
    h = _harness()
    h.observe("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    h.state.verify_attempts = 1
    h.observe("bash", {"command": "python a.py"}, "ran ok")  # even a success
    assert h.state.writes_since_verify == []
    assert h.state.verify_attempts == 0


def test_observe_failed_bash_still_counts_as_verification_attempt():
    """A failing bash run means the agent DID try to run its code — we stop nagging.
    This prevents the 'it errored, keep trying forever' loop."""
    h = _harness()
    h.observe("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    h.observe("bash", {"command": "python a.py"}, "Error: exit code 1")
    assert h.state.writes_since_verify == []
    assert h.state.verify_attempts == 0


def test_observe_disabled_is_noop():
    h = _harness(enabled=False)
    h.observe("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    assert h.state.writes_since_verify == []


# ------------------------------------------------------- after_tool_call (PlanGate)
def test_plan_gate_nudges_on_write_with_no_todos():
    h = _harness()
    aug = h.after_tool_call("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    assert aug is not None and "[nudge]" in aug


def test_plan_gate_no_nudge_when_todos_exist():
    h = _harness(_add_todo(TodoStore()))
    aug = h.after_tool_call("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    assert aug is None


def test_plan_gate_nudges_at_most_once_per_turn():
    h = _harness()
    first = h.after_tool_call("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    second = h.after_tool_call("write_file", {"path": "b.py"}, "Wrote 5 chars to b.py")
    assert first is not None
    assert second is None


def test_plan_gate_only_for_write_tools():
    h = _harness()
    assert h.after_tool_call("read_file", {"path": "a.py"}, "contents") is None
    assert h.after_tool_call("bash", {"command": "ls"}, "output") is None


def test_plan_gate_disabled_returns_none():
    h = _harness(enabled=False)
    assert h.after_tool_call("write_file", {"path": "a.py"}, "Wrote 10 chars") is None


# ------------------------------------------------------ check_termination — VerifyGate
def test_verify_gate_blocks_unverified_done():
    h = _harness()
    h.observe("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    cont, inject, warn = h.check_termination("all done")
    assert cont is True
    assert inject is not None and "bash" in inject
    assert warn is None


def test_verify_gate_passes_after_bash():
    h = _harness()
    h.observe("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    h.observe("bash", {"command": "python a.py"}, "ok")
    cont, inject, warn = h.check_termination("all done")
    assert cont is False and inject is None and warn is None


def test_verify_gate_passes_when_nothing_written():
    h = _harness()
    cont, inject, warn = h.check_termination("here's your answer")
    assert cont is False and inject is None and warn is None


def test_verify_gate_escalates_to_warn_after_max_attempts():
    h = _harness()
    h.observe("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    # attempt 0 -> continue, attempt 1 -> continue, attempt 2 -> release + warn
    cont0, _, warn0 = h.check_termination("done")
    cont1, _, warn1 = h.check_termination("done")
    cont2, _, warn2 = h.check_termination("done")
    assert (cont0, warn0) == (True, None)
    assert (cont1, warn1) == (True, None)
    assert cont2 is False
    assert warn2 is not None and "UNVERIFIED" in warn2


def test_verify_gate_attempt1_names_files():
    h = _harness()
    h.observe("write_file", {"path": "game.html"}, "Wrote 5000 chars to game.html")
    h.check_termination("done")  # attempt 0
    cont, inject, _ = h.check_termination("done")  # attempt 1
    assert cont is True and "game.html" in inject


def test_verify_gate_resets_on_bash_then_rewrite():
    """write -> bash (clears) -> write again -> 'done' must re-trigger the gate."""
    h = _harness()
    h.observe("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    h.observe("bash", {"command": "python a.py"}, "ok")
    # verified now; a new write re-arms the gate
    h.observe("write_file", {"path": "b.py"}, "Wrote 5 chars to b.py")
    cont, inject, warn = h.check_termination("done")
    assert cont is True and inject is not None


# ----------------------------------------------- check_termination — CompletionGate
def test_completion_gate_blocks_pending_todos():
    store = TodoStore()
    _add_todo(store, "task one", status="pending")
    h = _harness(store)  # no writes -> verify gate passes through
    cont, inject, warn = h.check_termination("done")
    assert cont is True
    assert inject is not None and "unfinished" in inject


def test_completion_gate_skipped_when_no_todos():
    """Trivial-task exemption: no plan -> no completion gate."""
    h = _harness()  # empty todos, no writes
    cont, inject, warn = h.check_termination("here's your answer")
    assert cont is False and inject is None and warn is None


def test_completion_gate_passes_all_completed():
    store = TodoStore()
    _add_todo(store, "task one", status="completed")
    h = _harness(store)
    cont, inject, warn = h.check_termination("done")
    assert cont is False and inject is None and warn is None


def test_completion_gate_escalates_to_warn():
    store = TodoStore()
    _add_todo(store, "task one", status="pending")
    h = _harness(store)
    h.check_termination("done")  # 0
    h.check_termination("done")  # 1
    cont, _, warn = h.check_termination("done")  # 2 -> release
    assert cont is False
    assert warn is not None and "unfinished todo" in warn


def test_verify_gate_takes_priority_over_completion():
    """When both fire, verify wins (code-not-run is the worse failure)."""
    store = TodoStore()
    _add_todo(store, "task one", status="pending")
    h = _harness(store)
    h.observe("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    cont, inject, _ = h.check_termination("done")
    assert cont is True and "bash" in inject  # verify message, not todo message


# ---------------------------------------------------------------- disabled
def test_disabled_harness_check_termination_passthrough():
    h = _harness(enabled=False)
    h.observe("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    cont, inject, warn = h.check_termination("done")
    assert (cont, inject, warn) == (False, None, None)
