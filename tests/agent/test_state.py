"""Tests for the explicit agent state machine (AgentState + AgentStateTracker).

Phase 1 of the harness architecture improvements: observability for execution
phases, derived from harness ground truth.
"""

from coderio.agent.state import AgentState, AgentStateTracker


# --- AgentStateTracker: phase derivation ---


def test_derive_phase_explore_default():
    """No writes, no todos, no verification → EXPLORE."""
    t = AgentStateTracker()
    assert (
        t.derive_phase([], todos_exist=False, just_verified=False) == AgentState.EXPLORE
    )


def test_derive_phase_plan_when_write_without_todos():
    """A write with no todo list → PLAN (PlanGate signal)."""
    t = AgentStateTracker()
    phase = t.derive_phase(["a.py"], todos_exist=False, just_verified=False)
    assert phase == AgentState.PLAN


def test_derive_phase_implement_when_write_with_todos():
    """Writes with a todo list → IMPLEMENT."""
    t = AgentStateTracker()
    phase = t.derive_phase(["a.py"], todos_exist=True, just_verified=False)
    assert phase == AgentState.IMPLEMENT


def test_derive_phase_verify_takes_priority():
    """just_verified=True wins over writes → VERIFY."""
    t = AgentStateTracker()
    phase = t.derive_phase(["a.py"], todos_exist=True, just_verified=True)
    assert phase == AgentState.VERIFY


# --- AgentStateTracker: transition debounce ---


def test_transition_records_only_on_change():
    """Repeated same-phase transitions don't bloat the timeline (debounce)."""
    t = AgentStateTracker()
    t.transition(AgentState.EXPLORE, step=1, hint="read_file")
    t.transition(AgentState.EXPLORE, step=1, hint="grep")
    t.transition(AgentState.EXPLORE, step=2, hint="list_dir")
    assert len(t.timeline) == 1  # all EXPLORE, only first recorded
    assert t.current == AgentState.EXPLORE


def test_transition_records_changes():
    """Distinct phases are each recorded."""
    t = AgentStateTracker()
    t.transition(AgentState.EXPLORE, step=1)
    t.transition(AgentState.PLAN, step=1)
    t.transition(AgentState.IMPLEMENT, step=2)
    t.transition(AgentState.VERIFY, step=3)
    assert len(t.timeline) == 4
    assert [s.state for s in t.timeline] == [
        AgentState.EXPLORE,
        AgentState.PLAN,
        AgentState.IMPLEMENT,
        AgentState.VERIFY,
    ]


def test_finish_always_records():
    """finish() records a COMPLETE snapshot even if already complete (captures
    the final step number for timeline replay)."""
    t = AgentStateTracker()
    t.transition(AgentState.IMPLEMENT, step=5)
    t.finish(step=5, hint="done")
    assert t.timeline[-1].state == AgentState.COMPLETE
    assert t.current == AgentState.COMPLETE


def test_finish_after_idle_records_first_snapshot():
    """finish() on a fresh tracker (no prior transitions) still records COMPLETE."""
    t = AgentStateTracker()
    t.finish(step=1)
    assert len(t.timeline) == 1
    assert t.timeline[0].state == AgentState.COMPLETE


# --- AgentStateTracker: serialization ---


def test_round_trip_serialization():
    """Timeline survives to_payload → from_payload (for jsonl persistence)."""
    t = AgentStateTracker()
    t.transition(AgentState.EXPLORE, step=1, hint="read_file")
    t.transition(AgentState.IMPLEMENT, step=3, hint="write_file")
    t.finish(step=5, hint="verified")
    payload = t.to_payload()

    t2 = AgentStateTracker.from_payload(payload)
    assert len(t2.timeline) == len(t.timeline)
    assert [s.state for s in t2.timeline] == [s.state for s in t.timeline]
    assert t2.current == AgentState.COMPLETE


def test_summary_string():
    """summary() produces a readable phase progression."""
    t = AgentStateTracker()
    t.transition(AgentState.EXPLORE, step=1)
    t.transition(AgentState.IMPLEMENT, step=2)
    s = t.summary()
    assert "explore" in s
    assert "implement" in s
    assert "2 transitions" in s
