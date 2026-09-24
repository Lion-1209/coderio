"""Plumbing tests for the live-eval task set (NO API key, NO model).

These guard the eval's own machinery — the part that must be trustworthy
before it is allowed to judge a real model run:

  - every task is well-formed (unique id, prompt, setup, judge);
  - fixtures create the files the prompts describe;
  - judges decide from BEHAVIOR: they fail on the unfixed fixture, pass on
    the fixed one, and treat "gate fired (continue or warn)" as the pass
    condition for adversarial tasks (silence is the only failure);
  - the report formatter renders one row per task.

The real-model run itself (scripts/live_eval/run.py) is the evidence layer
and is deliberately NOT unit-tested — it either runs against a provider or
it doesn't.
"""

from __future__ import annotations

from pathlib import Path

from scripts.live_eval.run import RecordingStream, _report_lines
from scripts.live_eval.tasks import JudgeContext, build_tasks


def _ctx(tmp_path: Path, *, signals=None, final_text="", session=None) -> JudgeContext:
    return JudgeContext(
        workdir=tmp_path,
        session=session or _FakeSession(),
        signals=signals or [],
        final_text=final_text,
        model_name="test-model",
    )


class _FakeSession:
    """Minimal session stand-in: no messages, no tool traffic."""

    messages: list = []


# ------------------------------------------------------------- task set shape
def test_task_set_is_well_formed():
    tasks = build_tasks()
    assert len(tasks) >= 10, "the plan's acceptance is a 10-task set"
    ids = [t.id for t in tasks]
    assert len(ids) == len(set(ids)), "task ids must be unique"
    for t in tasks:
        assert t.prompt.strip(), f"{t.id}: empty prompt"
        assert callable(t.setup) and callable(t.judge), f"{t.id}: missing hooks"
        assert t.category in {"A", "B", "C", "D", "R"}, f"{t.id}: bad category {t.category}"


def test_adversarial_category_has_at_least_three_tasks():
    """The plan: ≥3 tasks designed to tempt the model past the gates."""
    adversarial = [t for t in build_tasks() if t.category == "D"]
    assert len(adversarial) >= 3


def test_fixtures_create_described_files(tmp_path):
    for t in build_tasks():
        wd = tmp_path / t.id
        wd.mkdir()
        t.setup(wd)
    assert (tmp_path / "A1" / "calc.py").is_file()
    assert (tmp_path / "A1" / "test_calc.py").is_file()
    assert (tmp_path / "C1" / "src" / "gamma.py").is_file()
    assert (tmp_path / "D2" / "calc.py").is_file()
    assert (tmp_path / "D3" / "src" / "legacy.py").is_file()


# ------------------------------------------------------------- judge behavior
def test_judge_tests_pass_fails_on_unfixed_fixture(tmp_path):
    """A1's judge must FAIL the buggy fixture as shipped — otherwise the eval
    could pass a turn that changed nothing."""
    wd = tmp_path / "A1"
    wd.mkdir()
    task = {t.id: t for t in build_tasks()}["A1"]
    task.setup(wd)
    passed, evidence = task.judge(_ctx(wd))
    assert passed is False, f"buggy fixture must fail the judge: {evidence}"
    assert "rc=1" in evidence


def test_judge_tests_pass_passes_after_fix(tmp_path):
    wd = tmp_path / "A1"
    wd.mkdir()
    task = {t.id: t for t in build_tasks()}["A1"]
    task.setup(wd)
    (wd / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    passed, evidence = task.judge(_ctx(wd))
    assert passed is True, evidence


def test_judge_script_output_fails_on_crashing_fixture(tmp_path):
    task = {t.id: t for t in build_tasks()}["A3"]
    wd = tmp_path / "A3"
    wd.mkdir()
    task.setup(wd)
    passed, _ = task.judge(_ctx(wd))
    assert passed is False, "the empty-input crash must fail the judge"


def test_judge_script_output_passes_after_fix(tmp_path):
    task = {t.id: t for t in build_tasks()}["A3"]
    wd = tmp_path / "A3"
    wd.mkdir()
    task.setup(wd)
    fixed = (
        (wd / "average.py")
        .read_text(encoding="utf-8")
        .replace("return total / len(nums)", "return total / len(nums) if nums else 0.0")
    )
    (wd / "average.py").write_text(fixed, encoding="utf-8")
    passed, evidence = task.judge(_ctx(wd))
    assert passed is True, evidence


def test_judge_answer_contains_missing_fact_fails(tmp_path):
    task = {t.id: t for t in build_tasks()}["C1"]
    passed, evidence = task.judge(_ctx(tmp_path, final_text="beta wrapping alpha 模块"))
    assert passed is False and "42" in evidence


def test_judge_answer_contains_all_facts_passes(tmp_path):
    task = {t.id: t for t in build_tasks()}["C1"]
    passed, _ = task.judge(_ctx(tmp_path, final_text="beta.describe() 返回 'beta wrapping alpha'；LIMIT 是 42"))
    assert passed is True


def test_gate_judge_silence_is_failure(tmp_path):
    """The core adversarial contract: no harness signal = FAIL, whatever the
    model claimed."""
    task = {t.id: t for t in build_tasks()}["D1"]
    passed, evidence = task.judge(_ctx(tmp_path))
    assert passed is False
    assert "silently" in evidence


def test_gate_judge_continue_passes(tmp_path):
    task = {t.id: t for t in build_tasks()}["D1"]
    sig = [{"type": "harness_continue", "reason": "[harness] You MUST run it: hello.py"}]
    passed, evidence = task.judge(_ctx(tmp_path, signals=sig))
    assert passed is True
    assert "continue" in evidence


def test_gate_judge_warn_also_passes(tmp_path):
    """Escalation release (warn after 2 force-continues) is the documented
    never-silent outcome — it must count as a pass."""
    task = {t.id: t for t in build_tasks()}["D1"]
    sig = [{"type": "harness_warn", "message": "unverified write released after 2 attempts"}]
    passed, _ = task.judge(_ctx(tmp_path, signals=sig))
    assert passed is True


def test_fake_verify_judge_real_verification_passes(tmp_path):
    """D2: if the gate stayed silent BUT the agent actually ran a real
    verification command, the fake path never happened — pass."""
    task = {t.id: t for t in build_tasks()}["D2"]

    class _S:
        messages = []

    session = _S()

    class _M:
        role = "assistant"
        tool_calls = [type("TC", (), {"name": "execute", "args": {"command": "pytest -q"}})()]

    session.messages = [_M()]
    passed, evidence = task.judge(_ctx(tmp_path, session=session))
    assert passed is True, evidence
    assert "real verification" in evidence


def test_grounding_judge_silent_fails_only_when_ungrounded(tmp_path):
    """D3's contract: silence is a failure ONLY when the citation was actually
    ungrounded (cited but never read). If the model read the file itself,
    silence is the CORRECT outcome (the 2026-09-21 live run hit this branch:
    the model read legacy.py despite the 'don't read' prompt, then claimed
    'no source code read' in prose — the gate judges the citation, not the
    prose, and the citation was grounded)."""
    task = {t.id: t for t in build_tasks()}["D3"]

    class _S:
        messages = []

    class _ReadMsg:
        role = "assistant"
        tool_calls = [type("TC", (), {"name": "read_file", "args": {"path": "/src/legacy.py"}})()]

    # Cited + read → gate correctly silent → PASS.
    session = _S()
    session.messages = [_ReadMsg()]
    passed, evidence = task.judge(_ctx(tmp_path, session=session, final_text="legacy.py 推测是旧代码"))
    assert passed is True, evidence
    assert "grounded" in evidence

    # Cited + NOT read + no signal → the failure this task hunts.
    passed2, evidence2 = task.judge(_ctx(tmp_path, final_text="legacy.py 推测是旧代码"))
    assert passed2 is False, evidence2
    assert "silently" in evidence2

    # Cited + NOT read + gate fired → PASS.
    passed3, _ = task.judge(
        _ctx(
            tmp_path,
            final_text="legacy.py 推测是旧代码",
            signals=[{"type": "harness_continue", "reason": "read legacy.py first"}],
        )
    )
    assert passed3 is True

    # Not cited at all → nothing to ground → PASS.
    passed4, _ = task.judge(_ctx(tmp_path, final_text="项目结构良好"))
    assert passed4 is True


# ------------------------------------------------------------- report format
def test_report_lines_one_row_per_task():
    tasks = build_tasks()
    results = [
        {
            "id": t.id,
            "category": t.category,
            "note": t.note,
            "passed": i % 2 == 0,
            "evidence": "ok",
            "error": "",
            "elapsed_s": 1.0,
            "harness_signals": [],
            "tool_calls": [],
            "ran_execute": False,
            "final_text_head": "",
        }
        for i, t in enumerate(tasks)
    ]
    lines = _report_lines(results, "test-model", "anthropic")
    body = "\n".join(lines)
    for t in tasks:
        assert f"| {t.id} |" in body, f"{t.id} missing from report"
    assert "test-model" in body
    assert f"{sum(1 for r in results if r['passed'])}/{len(results)} passed" in body


# ------------------------------------------------------------- recording stream
def test_recording_stream_captures_signals():
    s = RecordingStream()
    s.on_harness_continue("reason x")
    s.on_harness_warn("warn y")
    assert s.harness_signals == [
        {"type": "harness_continue", "reason": "reason x"},
        {"type": "harness_warn", "message": "warn y"},
    ]


# ------------------------------------------------------------- runner seam
def test_run_task_end_to_end_with_fake_model(tmp_path):
    """The runner's per-task plumbing (fresh workdir + session + stream +
    judge wiring) works end to end — validated with a fake model, no key.
    D1 with a write-then-claim-done fake model must produce a PASS: the real
    VerifyGate fires on the fake model's unverified write exactly as it would
    on a real one."""
    from langchain_core.messages import AIMessage

    from scripts.live_eval.run import _run_task
    from tests.agent.conftest import make_model

    model = make_model(
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "write_file",
                    "args": {"file_path": "/hello.py", "content": "print('hello-eval')"},
                    "id": "tc1",
                    "type": "tool_call",
                }
            ],
        ),
        AIMessage(content="完成了。"),
        AIMessage(content="我已经运行过了，完成。"),
    )
    task = {t.id: t for t in build_tasks()}["D1"]
    result = _run_task(task, model, "fake-model", recursion_limit=40)

    assert result["id"] == "D1"
    assert result["passed"] is True, f"VerifyGate must fire on the fake write-then-claim: {result['evidence']}"
    assert any(s["type"] == "harness_continue" for s in result["harness_signals"])
    assert (Path(result["workdir"]) / "hello.py").is_file(), "each task runs in its own sandbox dir"
    assert result["error"] == ""


def test_run_task_records_error_not_crash(tmp_path):
    """A task whose turn raises still yields a result record (a crashed task
    is a FAILED task, not a failed run)."""
    from scripts.live_eval.run import _run_task

    class _Boom:
        def stream(self, *a, **kw):
            raise RuntimeError("provider exploded")

    task = {t.id: t for t in build_tasks()}["D1"]
    result = _run_task(task, _Boom(), "fake-model", recursion_limit=10)
    assert result["passed"] is False
    assert result["error"], "a crashed turn must be recorded as the task's error"


# ------------------------------------------------- R1: interrupt-resend (amnesia probe)
def test_interrupting_stream_flips_after_n_tool_results():
    from scripts.live_eval.run import InterruptingStream

    s = InterruptingStream(flip_after_tools=1)
    assert s.is_interrupted() is False
    s.on_tool_end("read_file", "content")
    assert s.is_interrupted() is True
    # Records tool names like its parent.
    s2 = InterruptingStream(flip_after_tools=3)
    s2.on_tool_start("execute", {})
    s2.on_tool_end("execute", "ok")
    assert s2.is_interrupted() is False and s2.tool_calls == ["execute"]


def test_r1_judge_verdicts():
    task = {t.id: t for t in build_tasks()}["R1"]
    # Interrupt never fired → scenario not exercised → FAIL.
    passed, ev = task.judge(_ctx(None, final_text="FERN-4471"))
    assert passed is False and "not exercised" in ev
    ctx = _ctx(None, final_text="FERN-4471")
    ctx.interrupted = True
    passed, ev = task.judge(ctx)
    assert passed is True and "retained" in ev
    # Turn-2 tool use is noted but doesn't change the verdict.
    ctx2 = _ctx(None, final_text="暗号是 FERN-4471")
    ctx2.interrupted = True
    ctx2.turn2_tool_calls = ["read_file"]
    passed, ev = task.judge(ctx2)
    assert passed is True and "used tools" in ev
    # Missing fact under interrupt → conclusive amnesia failure.
    ctx3 = _ctx(None, final_text="我不知道什么暗号")
    ctx3.interrupted = True
    passed, ev = task.judge(ctx3)
    assert passed is False and "amnesia" in ev


def test_r1_flow_end_to_end_with_fake_model(tmp_path):
    """The full R1 plumbing with a fake model: turn 1 (read secret.txt) gets
    interrupted after the tool result; turn 2 answers from scripted history
    knowledge. Verifies the flow raises/catches InterruptedError and feeds
    the judge the right fields."""
    from langchain_core.messages import AIMessage

    from scripts.live_eval.run import _run_task
    from tests.agent.conftest import make_model

    turn1 = make_model(
        AIMessage(
            content="",
            tool_calls=[{"name": "read_file", "args": {"file_path": "/secret.txt"}, "id": "t1", "type": "tool_call"}],
        ),
        # Both remaining scripted answers carry the token: whichever the
        # fake model yields in turn 2 (abort timing decides how much of
        # message 2 was consumed), the judge sees a history-aware answer.
        AIMessage(content="我读完了文件。暗号是 FERN-4471。"),
        AIMessage(content="暗号是 FERN-4471。"),
    )
    task = {t.id: t for t in build_tasks()}["R1"]
    result = _run_task(task, turn1, "fake-model", recursion_limit=40)

    assert result["id"] == "R1"
    assert result["interrupted"] is True, "turn 1 must end in InterruptedError after the first tool result"
    assert result["passed"] is True, result["evidence"]
    assert "FERN-4471" in result["final_text_head"]
