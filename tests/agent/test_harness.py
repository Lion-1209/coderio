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


def test_plan_gate_nudge_is_append_safe():
    """The nudge must start with a newline so that `result + aug` keeps the
    original tool result readable. The ReAct loop APPENDS (not replaces); if the
    nudge didn't lead with \\n the two texts would mash together on one line.
    Regression for the result=aug overwrite bug that used to discard the tool result.
    """
    h = _harness()
    aug = h.after_tool_call("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    assert aug is not None
    assert aug.startswith("\n"), "nudge must start with newline for clean append"
    # Verify append preserves the original result text
    original = "Wrote 10 chars to a.py"
    combined = original + aug
    assert original in combined
    assert "[nudge]" in combined


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


def test_verify_gate_echo_does_not_clear():
    """A bare `echo done` or `ls` must NOT clear unverified writes — that would
    let the agent bypass the gate without actually running its code."""
    h = _harness()
    h.observe("write_file", {"path": "a.py"}, "Wrote 10 chars to a.py")
    h.observe("bash", {"command": "echo done"}, "done")
    cont, inject, warn = h.check_termination("all done")
    assert cont is True, "echo should NOT satisfy VerifyGate"
    assert inject is not None


def test_verify_gate_ls_does_not_clear():
    """`ls` and `pwd` are not verification — they don't run the written code."""
    h = _harness()
    h.observe("write_file", {"path": "src/app.py"}, "Wrote 20 chars to src/app.py")
    h.observe("bash", {"command": "ls -la"}, "total 0")
    cont, inject, warn = h.check_termination("done")
    assert cont is True, "ls should NOT satisfy VerifyGate"


def test_verify_gate_pytest_clears_without_filename():
    """pytest counts as verification even without referencing the written file
    explicitly — it discovers and runs tests."""
    h = _harness()
    h.observe("write_file", {"path": "tests/test_foo.py"}, "Wrote 100 chars")
    h.observe("bash", {"command": "pytest -q"}, "3 passed")
    cont, inject, warn = h.check_termination("done")
    assert cont is False, "pytest should satisfy VerifyGate"


def test_verify_gate_python_with_filename_clears():
    """`python src/app.py` clears because the written file's basename is in the command."""
    h = _harness()
    h.observe("write_file", {"path": "src/app.py"}, "Wrote 50 chars")
    h.observe("bash", {"command": "python src/app.py --port 8080"}, "started")
    cont, inject, warn = h.check_termination("done")
    assert cont is False, "python with the file path should satisfy VerifyGate"


def test_verify_gate_git_status_does_not_clear():
    """git status is not verification."""
    h = _harness()
    h.observe("edit_file", {"path": "main.js"}, "Edited line 5")
    h.observe("bash", {"command": "git status"}, "nothing to commit")
    cont, inject, warn = h.check_termination("done")
    assert cont is True, "git status should NOT satisfy VerifyGate"


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


# ------------------------------------------------------ check_termination — GroundingGate
def test_grounding_gate_passthrough_when_no_files_cited():
    """A pure-conversation answer with no file citations is left alone."""
    h = _harness()
    cont, inject, warn = h.check_termination("答案是 42，因为这就是生命之义。")
    assert (cont, inject, warn) == (False, None, None)


def test_grounding_gate_blocks_unread_citation():
    """The core regression: model cites loader.py:81 but never read loader.py."""
    h = _harness()
    cont, inject, warn = h.check_termination(
        "分析发现 loader.py:81 已经接入了 config.harness。"
    )
    assert cont is True
    assert inject is not None and "loader.py" in inject
    assert "read_file" in inject
    assert warn is None


def test_grounding_gate_passes_when_file_was_read():
    """Cite a file you actually read this turn → no interception."""
    h = _harness()
    h.observe("read_file", {"path": "src/agent/loader.py"}, "<file contents>")
    cont, inject, warn = h.check_termination(
        "读了 loader.py:81，确认已接入。"
    )
    assert (cont, inject, warn) == (False, None, None)


def test_grounding_gate_basename_match():
    """Citing 'loop.py' counts as read if we read 'src/coderio/agent/loop.py'."""
    h = _harness()
    h.observe("read_file", {"path": "src/coderio/agent/loop.py"}, "<contents>")
    cont, inject, warn = h.check_termination("loop.py 的 _execute_turn 做了拦截。")
    assert (cont, inject, warn) == (False, None, None)


def test_grounding_gate_dir_read_does_not_cover_files():
    """A list_dir only returns filenames, not contents — it must NOT ground a
    citation about a file's internals. The model must read_file the specific
    file before claiming 'harness.py defines four gates'. This closes a bypass
    where list_dir('src/') satisfied citations for every file under src/."""
    h = _harness()
    h.observe("list_dir", {"path": "src/agent/"}, "loop.py\nharness.py")
    # Citing a file under that dir without a read_file — should be ungrounded.
    cont, inject, warn = h.check_termination("src/agent/harness.py 定义了四道门。")
    assert cont is True  # gate forces a re-read
    assert inject is not None
    assert "harness.py" in inject


def test_grounding_gate_read_file_grounds_citation():
    """A read_file of the exact cited file grounds the citation (positive case
    for the tightened check — only read_file counts, not list_dir/grep)."""
    h = _harness()
    h.observe("read_file", {"path": "src/agent/harness.py"}, "contents...")
    cont, inject, warn = h.check_termination("src/agent/harness.py 定义了四道门。")
    assert (cont, inject, warn) == (False, None, None)


def test_grounding_gate_grep_does_not_ground_citation():
    """A grep for a pattern is NOT a content read — the model only sees matching
    lines, never the full file. Citing the file after grep should be ungrounded.
    This closes the bypass where grep pattern='loop' satisfied 'loop.py:42'."""
    h = _harness()
    h.observe("grep", {"pattern": "loop", "path": "src/agent"}, "src/agent/loop.py:42: ...")
    cont, inject, warn = h.check_termination("loop.py:42 处理 ReAct 循环。")
    assert cont is True  # gate forces a real read_file
    assert inject is not None


def test_grounding_gate_escalates_to_warn_after_max():
    """After 2 forced-continues, release with a warning (never silent, never loop)."""
    h = _harness()
    text = "loader.py:81 确认接入了。"  # never read
    cont0, _, warn0 = h.check_termination(text)
    cont1, _, warn1 = h.check_termination(text)
    cont2, _, warn2 = h.check_termination(text)
    assert (cont0, warn0) == (True, None)
    assert (cont1, warn1) == (True, None)
    assert cont2 is False
    assert warn2 is not None and "loader.py" in warn2


def test_grounding_gate_attempt1_names_files():
    """Second interception names the unread files (tighter feedback)."""
    h = _harness()
    h.check_termination("config.py 的 loader 没读 harness 字段。")
    cont, inject, _ = h.check_termination("config.py 的 loader 没读 harness 字段。")
    assert cont is True and "config.py" in inject


def test_grounding_gate_partial_read():
    """Citing two files but only read one → block on the unread one."""
    h = _harness()
    h.observe("read_file", {"path": "a.py"}, "<contents>")
    cont, inject, warn = h.check_termination(
        "a.py 正确，但 b.py:10 有 bug。"
    )
    assert cont is True
    assert inject is not None and "b.py" in inject
    assert "a.py" not in inject  # don't nag about the one we read


def test_grounding_gate_does_not_false_positive_on_prose():
    """Ordinary words must not trip the citation regex.

    'step 2', 'the loader', 'go to main', 'config.harness' (no .py) should NOT
    be treated as file citations. Only dotted code extensions count."""
    from coderio.agent.harness import _cited_files
    assert _cited_files("请跳到 step 2，然后看 the loader 的逻辑") == []
    assert _cited_files("config.harness 字段在 ModelsConfig 里") == []
    # but a real file path IS caught (with its :line citation)
    cited = _cited_files("看 loader.py:81")
    assert len(cited) == 1 and cited[0].startswith("loader.py")


def test_was_read_is_case_and_slash_insensitive():
    """REGRESSION: reading 'Loop.py' must satisfy a citation of 'loop.py:81'.

    On case-insensitive filesystems (NTFS, APFS default) these are the same
    file, so the GroundingGate must treat them as already-read. Without
    normalization, the model reads 'src\\coderio\\agent\\Loop.py' and then
    cites 'loop.py' — the gate would force a re-read every time the model's
    case drifts. Observed in real sessions: 'Loop.py' read 5x in one turn.
    """
    from coderio.agent.harness import _norm_path
    h = _harness()
    # observe a read with mixed case + backslashes
    h.observe("read_file", {"path": "src\\coderio\\agent\\Loop.py"}, "1	contents")
    # cite the same file in different forms — all should match
    assert h._was_read("loop.py") is True
    assert h._was_read("loop.py:81") is True
    assert h._was_read("src/coderio/agent/loop.py") is True
    assert h._was_read("Src\\Coderio\\Agent\\LOOP.PY") is True
    # and a truly unread file still doesn't match
    assert h._was_read("other.py") is False

    # _norm_path direct checks
    assert _norm_path("a/b.py") == "a/b.py"
    assert _norm_path("a\\b.py") == "a/b.py"
    assert _norm_path("A\\B.py") == "a/b.py"
    assert _norm_path("./a/b.py") == "a/b.py"
    assert _norm_path("a/./b/../c.py") == "a/c.py"
    assert _norm_path("") == ""


def test_grounding_gate_catches_multiple_citations():
    h = _harness()
    cont, inject, warn = h.check_termination(
        "loop.py 处理 turn，harness.py 定义 gate，prompts.py 路由意图。"
    )
    assert cont is True
    assert inject is not None
    for f in ("loop.py", "harness.py", "prompts.py"):
        assert f in inject


def test_grounding_gate_regex_ignores_code_attributes():
    """Code snippets like `self._live = None` or `obj.attr.py` must NOT be
    treated as file citations. The regex's left-boundary now excludes a
    preceding dot, so `_live.py` after `self.` doesn't match.

    Regression: a real session had the model's analysis mention `_live.py`
    (from `self._live = None` in stream.py's code), which the GroundingGate
    flagged as an unread file, forcing a spurious re-read."""
    from coderio.agent.harness import _cited_files
    # Code context — should NOT match the attribute portion
    assert _cited_files("stream.py 里 self._live = None") == ["stream.py"]
    assert _cited_files("the obj._attr.py pattern") == []
    assert _cited_files("config._base.py") == []
    # A genuine standalone citation (preceded by space) — SHOULD match
    assert "loop.py" in _cited_files("看 loop.py 的实现")
    # self._live.py (dot-prefixed) must not match, but a real stream.py ref does
    cited = _cited_files("在 stream.py 中，self._live 被初始化")
    assert "stream.py" in cited
    assert "_live.py" not in cited


# ----------------------------------------------- phase tracking (AgentStateTracker)

class _CapturingStream:
    """Minimal StreamHandler stand-in that records on_phase_change calls."""
    def __init__(self):
        self.calls: list[tuple[str, int, str]] = []
    def on_phase_change(self, state: str, step: int, hint: str) -> None:
        self.calls.append((state, step, hint))


def _harness_with_tracker():
    from coderio.agent.state import AgentStateTracker
    stream = _CapturingStream()
    tracker = AgentStateTracker()
    h = Harness(state=HarnessState(), todos=TodoStore(),
                state_tracker=tracker, stream=stream)
    return h, tracker, stream


def test_phase_tracks_explore_on_read():
    """observe(read_file) transitions to EXPLORE and fires on_phase_change."""
    h, tracker, stream = _harness_with_tracker()
    h.observe("read_file", {"path": "a.py"}, "contents")
    assert tracker.current.value == "explore"
    assert any(s == "explore" for s, _, _ in stream.calls)


def test_phase_tracks_implement_on_write_with_todos():
    """observe(write_file) with a todo list → IMPLEMENT phase."""
    h, tracker, stream = _harness_with_tracker()
    _add_todo(h.todos)
    h.observe("write_file", {"path": "a.py"}, "Wrote 10 chars")
    assert tracker.current.value == "implement"
    assert any(s == "implement" for s, _, _ in stream.calls)


def test_phase_tracks_plan_on_write_without_todos():
    """observe(write_file) with NO todos → PLAN phase (PlanGate signal)."""
    h, tracker, stream = _harness_with_tracker()
    h.observe("write_file", {"path": "a.py"}, "Wrote 10 chars")
    assert tracker.current.value == "plan"


def test_phase_tracks_verify_on_test_run():
    """observe(bash) that verifies written code → VERIFY phase."""
    h, tracker, stream = _harness_with_tracker()
    _add_todo(h.todos)
    # Write first (→ IMPLEMENT), then run a verifying bash (→ VERIFY)
    h.observe("write_file", {"path": "test_a.py"}, "Wrote")
    assert tracker.current.value == "implement"
    h.observe("bash", {"command": "pytest test_a.py"}, "1 passed")
    assert tracker.current.value == "verify"


def test_phase_tracking_noop_without_tracker():
    """A Harness without state_tracker (back-compat) doesn't crash on observe."""
    h = _harness()  # no state_tracker, no stream
    h.observe("read_file", {"path": "a.py"}, "contents")  # must not raise
    h.observe("write_file", {"path": "b.py"}, "Wrote")    # must not raise


def test_phase_debounce_across_repeated_reads():
    """10 read_file calls produce ONE explore transition, not 10."""
    h, tracker, stream = _harness_with_tracker()
    for i in range(10):
        h.observe("read_file", {"path": f"f{i}.py"}, "contents")
    explore_calls = [c for c in stream.calls if c[0] == "explore"]
    assert len(explore_calls) == 1  # debounced
