"""LangGraph-specific tests for the crew StateGraph (structure, routing, HITL).

These verify the graph itself, complementing test_orchestrator.py (which tests
behavior through the public run() interface).
"""

from unittest.mock import MagicMock

from coderio.agent.stream import NullStream
from coderio.crew.orchestrator import CrewOrchestrator
from coderio.crew.state import CrewState
from coderio.tools.permission import AutoPermissionGate
from tests.crew.test_orchestrator import _store_with_skills


def _orch(**kw):
    """Build an orchestrator with sane defaults for graph-structure tests."""
    model = kw.pop("model", MagicMock())
    kw.setdefault("auto_mode", True)
    return CrewOrchestrator(
        model=model,
        store=_store_with_skills(),
        gate=AutoPermissionGate(),
        stream=NullStream(),
        **kw,
    )


def test_graph_has_role_and_pause_nodes():
    """The compiled graph must contain all 6 role nodes + 2 HITL pause nodes."""
    orch = _orch()
    graph = orch._graph.get_graph()
    node_ids = {n.id for n in graph.nodes.values() if hasattr(n, "id")}
    for role in ("clarify", "spec", "task", "execute", "verify", "commit"):
        assert role in node_ids, f"role node {role!r} missing from graph"
    for pause in ("clarify_pause", "spec_pause"):
        assert pause in node_ids, f"pause node {pause!r} missing from graph"


def test_verify_router_commit_on_pass():
    """verify_router returns 'commit' when verification passes."""
    orch = _orch()
    state: CrewState = {"verification": "PASS: 全部通过", "fix_attempts": 0}
    assert orch.verify_router(state) == "commit"


def test_verify_router_execute_on_fail_under_budget():
    """verify_router loops back to 'execute' when verification fails and budget remains."""
    orch = _orch(max_fix_loops=2)
    state: CrewState = {"verification": "FAIL: 测试缺失", "fix_attempts": 0}
    assert orch.verify_router(state) == "execute"
    state2: CrewState = {"verification": "FAIL: 测试缺失", "fix_attempts": 1}
    assert orch.verify_router(state2) == "execute"  # still under budget (1 < 2)


def test_verify_router_commit_when_budget_exhausted():
    """verify_router proceeds to 'commit' when fix budget is exhausted."""
    orch = _orch(max_fix_loops=1)
    state: CrewState = {"verification": "FAIL: still broken", "fix_attempts": 1}
    assert orch.verify_router(state) == "commit"  # 1 >= max_fix_loops(1)


def test_verify_router_loops_on_ambiguous_output():
    """REGRESSION (fail-closed): ambiguous output (no explicit PASS signal) is
    treated as a FAILURE, not a silent pass. The old fail-open behavior let
    '看起来完成了' (no pass/fail token) count as passed, masking real issues.
    Now it triggers the fix loop (or exhausts to partial-commit)."""
    orch = _orch()
    state: CrewState = {"verification": "看起来完成了", "fix_attempts": 0}
    assert orch.verify_router(state) == "execute"  # ambiguous -> fail -> fix loop


def test_auto_mode_pause_nodes_are_noop():
    """In auto mode, pause nodes must return {} (no interrupt)."""
    orch = _orch(auto_mode=True)
    clarify_pause = orch._make_pause_node("clarify")
    spec_pause = orch._make_pause_node("spec")
    assert clarify_pause({"clarification": "x"}) == {}
    assert spec_pause({"spec": "y"}) == {}


def test_interrupt_fires_in_non_auto_pause_node():
    """In non-auto mode, a pause node must call interrupt() (raises in test via
    the graph's interrupt mechanism). We verify by checking that invoking the
    node directly triggers the langgraph interrupt sentinel."""
    import pytest

    orch = _orch(auto_mode=False)
    pause = orch._make_pause_node("clarify")
    # interrupt() outside a graph run raises a special exception/sentinel;
    # we just confirm it does NOT return {} (the auto-mode path).
    with pytest.raises(Exception):
        pause({"clarification": "needs user input"})


def test_build_prompt_accepts_dict_state():
    """_build_prompt must accept a CrewState dict (LangGraph) in addition to a
    ProjectState — graph nodes pass dicts, tests may pass either."""
    orch = _orch()
    role = orch._by_stage["spec"]
    # dict input (LangGraph state shape)
    prompt = orch._build_prompt(role, {"request": "req", "clarification": "prior clar"})
    assert "prior clar" in prompt
    assert "req" in prompt
