from coderio.crew.state import (
    ProjectState, CrewState, crew_state_to_project_state,
    _overwrite, _append_list,
)


def test_default_state():
    s = ProjectState(request="build X")
    assert s.request == "build X"
    assert s.current_stage == "clarification"
    assert s.clarification == ""
    assert s.spec == ""
    assert s.task_list == ""
    assert s.implementation == ""
    assert s.verification == ""
    assert s.commit_message == ""
    assert s.user_clarification_answer == ""
    assert s.user_spec_approval == ""
    assert s.errors == []


def test_set_and_get_artifact():
    s = ProjectState(request="x")
    s.clarification = "Q: tech? A: html"
    s.spec = "# Spec\n..."
    assert s.clarification == "Q: tech? A: html"
    assert s.spec == "# Spec\n..."


def test_get_artifact_by_stage_name():
    s = ProjectState(request="x")
    s.spec = "S"
    assert getattr(s, "spec") == "S"
    s.implementation = "I"
    assert getattr(s, "implementation") == "I"


def test_errors_list_is_independent():
    a = ProjectState(request="x")
    b = ProjectState(request="y")
    a.errors.append("e1")
    assert b.errors == []


# --- CrewState (LangGraph) reducers + converter ---

def test_overwrite_reducer_replaces():
    """A role's write replaces the field's prior value (not append)."""
    assert _overwrite("old impl", "new impl") == "new impl"
    assert _overwrite("", "first") == "first"
    assert _overwrite("x", "y") == "y"


def test_append_list_reducer_accumulates():
    """errors accumulate across stages."""
    assert _append_list(["e1"], ["e2"]) == ["e1", "e2"]
    assert _append_list(None, ["e1"]) == ["e1"]
    assert _append_list(["e1"], None) == ["e1"]


def test_converter_round_trip():
    """CrewState dict → ProjectState preserves all fields."""
    d = {
        "request": "build X",
        "clarification": "Q: stack?",
        "spec": "# spec",
        "task_list": "1. foo\n2. bar",
        "implementation": "code here",
        "verification": "PASS",
        "commit_message": "feat: X",
        "fix_feedback": "",
        "fix_attempts": 1,
        "errors": ["warn1"],
    }
    ps = crew_state_to_project_state(d)
    assert ps.request == "build X"
    assert ps.clarification == "Q: stack?"
    assert ps.spec == "# spec"
    assert ps.task_list == "1. foo\n2. bar"
    assert ps.implementation == "code here"
    assert ps.verification == "PASS"
    assert ps.commit_message == "feat: X"
    assert ps.fix_attempts == 1
    assert ps.errors == ["warn1"]


def test_converter_defaults_missing_keys():
    """Missing dict keys fall back to ProjectState defaults."""
    ps = crew_state_to_project_state({"request": "only request"})
    assert ps.request == "only request"
    assert ps.spec == ""
    assert ps.fix_attempts == 0
    assert ps.errors == []


def test_converter_errors_returns_independent_list():
    """The errors list from the converter must not alias the input dict's list."""
    src = {"request": "x", "errors": ["e1"]}
    ps = crew_state_to_project_state(src)
    ps.errors.append("e2")
    assert src["errors"] == ["e1"], "mutating ProjectState.errors must not leak back"
