from pathlib import Path
from unittest.mock import MagicMock

from langchain_core.messages import AIMessage

from coderio.crew.orchestrator import CrewOrchestrator
from coderio.crew.state import ProjectState
from coderio.tools.permission import AutoPermissionGate
from coderio.agent.stream import NullStream


def _model_returning_per_call(*responses):
    """Model mock: each .stream() call yields the next response (an AIMessage iterator)."""
    model = MagicMock()

    def _stream(_msgs):
        i = min(model._call_idx, len(responses) - 1)
        model._call_idx = getattr(model, "_call_idx", 0) + 1
        msg = responses[i]
        return iter([msg])

    model._call_idx = 0
    model.bind_tools.return_value.stream.side_effect = _stream
    return model


def _store_with_skills():
    from coderio.skills.store import load_skill_store
    bundled = Path(__file__).resolve().parents[2] / "skills"
    return load_skill_store(bundled, 0, None)


def test_orchestrator_runs_six_stages_in_order():
    model = _model_returning_per_call(
        AIMessage(content="clarification done", tool_calls=[]),
        AIMessage(content="spec done", tool_calls=[]),
        AIMessage(content="task list done", tool_calls=[]),
        AIMessage(content="implementation done", tool_calls=[]),
        AIMessage(content="[CREW_VERIFY] PASS — all checks ok", tool_calls=[]),
        AIMessage(content="commit msg done", tool_calls=[]),
    )
    orch = CrewOrchestrator(
        model=model, store=_store_with_skills(), gate=AutoPermissionGate(),
        stream=NullStream(), auto_mode=True,
    )
    state = orch.run("build a snake game")
    assert state.clarification == "clarification done"
    assert state.spec == "spec done"
    assert state.task_list == "task list done"
    assert state.implementation == "implementation done"
    assert "PASS" in state.verification
    assert state.commit_message == "commit msg done"
    assert state.status == "success"


def test_orchestrator_calls_pause_callback_at_clarify_and_spec():
    pauses = []

    def on_pause(stage, output):
        pauses.append(stage)
        return "user answer for " + stage

    model = _model_returning_per_call(
        AIMessage(content="x", tool_calls=[]),
        AIMessage(content="x", tool_calls=[]),
        AIMessage(content="x", tool_calls=[]),
        AIMessage(content="x", tool_calls=[]),
        AIMessage(content="x", tool_calls=[]),
        AIMessage(content="x", tool_calls=[]),
    )
    orch = CrewOrchestrator(
        model=model, store=_store_with_skills(), gate=AutoPermissionGate(),
        stream=NullStream(), auto_mode=False, on_pause=on_pause,
    )
    state = orch.run("req")
    assert pauses == ["clarify", "spec"]
    assert state.user_clarification_answer == "user answer for clarify"
    assert state.user_spec_approval == "user answer for spec"


def test_auto_mode_skips_pauses():
    pauses = []

    model = _model_returning_per_call(
        AIMessage(content="x", tool_calls=[]),
        AIMessage(content="x", tool_calls=[]),
        AIMessage(content="x", tool_calls=[]),
        AIMessage(content="x", tool_calls=[]),
        AIMessage(content="x", tool_calls=[]),
        AIMessage(content="x", tool_calls=[]),
    )
    orch = CrewOrchestrator(
        model=model, store=_store_with_skills(), gate=AutoPermissionGate(),
        stream=NullStream(), auto_mode=True, on_pause=lambda s, o: pauses.append(s),
    )
    state = orch.run("req")
    assert pauses == []


def test_read_context_injected_into_prompt():
    """A spec role's prompt must contain the values of its `reads` fields."""
    model = _model_returning_per_call(
        AIMessage(content="prior clar", tool_calls=[]),
        AIMessage(content="spec based on prior clar", tool_calls=[]),
        AIMessage(content="x", tool_calls=[]),
        AIMessage(content="x", tool_calls=[]),
    )
    orch = CrewOrchestrator(
        model=model, store=_store_with_skills(), gate=AutoPermissionGate(),
        stream=NullStream(), auto_mode=True,
    )
    state = orch.run("req")
    role_prompt = orch._build_prompt(next(r for r in orch.roles if r.stage == "spec"), state)
    assert "prior clar" in role_prompt


def test_verification_passed_heuristic():
    orch = CrewOrchestrator(
        model=MagicMock(), store=_store_with_skills(), gate=AutoPermissionGate(),
        stream=NullStream(), auto_mode=True,
    )
    assert orch._verification_passed("验证结果：PASS，满足要求")
    assert orch._verification_passed("通过")
    assert not orch._verification_passed("FAIL：缺少测试")
    assert not orch._verification_passed("未通过：功能缺失")


def test_verification_structured_marker_wins_over_keywords():
    """The [CREW_VERIFY] marker must override the keyword fallback.

    Regression: 'failed to reproduce any bug' used to trip the 'fail' keyword and
    wrongly trigger a fix loop. With the structured marker, the verdict is read
    explicitly and the misleading 'failed' in the prose is ignored.
    """
    orch = CrewOrchestrator(
        model=MagicMock(), store=_store_with_skills(), gate=AutoPermissionGate(),
        stream=NullStream(), auto_mode=True,
    )
    # PASS verdict despite 'failed' appearing in the prose (the classic false positive).
    assert orch._verification_passed(
        "Ran the full suite; failed to reproduce any bug.\n[CREW_VERIFY] PASS"
    )
    # FAIL verdict explicitly.
    assert not orch._verification_passed(
        "2 tests still red.\n[CREW_VERIFY] FAIL"
    )
    # Marker present but verdict unreadable → FAIL-CLOSED (no longer defaults to
    # pass). The old fail-open behavior let an unreadable verdict count as pass,
    # masking real verification failures.
    assert not orch._verification_passed("[CREW_VERIFY] ???")
    # No marker at all → keyword fallback; only a CLEAR pass token counts as pass.
    assert not orch._verification_passed("测试未通过")
    # Empty verification → fail-closed (not a silent pass).
    assert not orch._verification_passed("")
    # Keyword fallback with a clear pass token.
    assert orch._verification_passed("all tests passed, looks good")


def test_verify_fail_triggers_fix_loop():
    """When verify reports FAIL, implementer re-runs with fix_feedback, then verify again."""
    responses = [
        AIMessage(content="clar", tool_calls=[]),
        AIMessage(content="spec", tool_calls=[]),
        AIMessage(content="tasks", tool_calls=[]),
        AIMessage(content="impl v1", tool_calls=[]),
        AIMessage(content="FAIL: 测试缺失", tool_calls=[]),
        AIMessage(content="impl v2 fixed", tool_calls=[]),
        AIMessage(content="PASS: 全部通过", tool_calls=[]),
        AIMessage(content="commit msg", tool_calls=[]),
    ]
    model = _model_returning_per_call(*responses)
    orch = CrewOrchestrator(
        model=model, store=_store_with_skills(), gate=AutoPermissionGate(),
        stream=NullStream(), auto_mode=True, max_fix_loops=2,
    )
    state = orch.run("req")
    assert state.fix_attempts == 1
    assert state.fix_feedback
    assert "v2" in state.implementation
    assert state.commit_message == "commit msg"


def test_fix_loop_respects_max():
    """Exhausting max_fix_loops proceeds to commit even if still failing — but
    status is marked 'partial' so the CLI can warn (REGRESSION: was always
    'success' under the old fail-open logic)."""
    responses = [
        AIMessage(content="clar", tool_calls=[]),
        AIMessage(content="spec", tool_calls=[]),
        AIMessage(content="tasks", tool_calls=[]),
        AIMessage(content="impl", tool_calls=[]),
        AIMessage(content="FAIL: broken", tool_calls=[]),
        AIMessage(content="impl fixed", tool_calls=[]),
        AIMessage(content="FAIL: still broken", tool_calls=[]),
        AIMessage(content="commit", tool_calls=[]),
    ]
    model = _model_returning_per_call(*responses)
    orch = CrewOrchestrator(
        model=model, store=_store_with_skills(), gate=AutoPermissionGate(),
        stream=NullStream(), auto_mode=True, max_fix_loops=1,
    )
    state = orch.run("req")
    assert state.fix_attempts == 1
    assert state.commit_message == "commit"
    assert state.status == "partial", (
        "budget-exhausted + verification still failing must be 'partial', not "
        "'success' — the old fail-open logic masked this as a clean success")


def test_passing_verify_yields_success_status():
    """Happy path: verify passes -> status is 'success'."""
    responses = [
        AIMessage(content="clar", tool_calls=[]),
        AIMessage(content="spec", tool_calls=[]),
        AIMessage(content="tasks", tool_calls=[]),
        AIMessage(content="impl", tool_calls=[]),
        AIMessage(content="[CREW_VERIFY] PASS", tool_calls=[]),
        AIMessage(content="commit msg", tool_calls=[]),
    ]
    model = _model_returning_per_call(*responses)
    orch = CrewOrchestrator(
        model=model, store=_store_with_skills(), gate=AutoPermissionGate(),
        stream=NullStream(), auto_mode=True,
    )
    state = orch.run("req")
    assert state.status == "success"


def test_fix_feedback_injected_into_implementer_prompt():
    """On a fix retry, the implementer's prompt must contain the verifier's feedback."""
    state = ProjectState(request="req")
    state.fix_feedback = "FAIL: 测试缺失，需补测试"
    orch = CrewOrchestrator(
        model=MagicMock(), store=_store_with_skills(), gate=AutoPermissionGate(),
        stream=NullStream(), auto_mode=True,
    )
    impl_role = next(r for r in orch.roles if r.stage == "execute")
    prompt = orch._build_prompt(impl_role, state)
    assert "测试缺失" in prompt
    assert "修复" in prompt
