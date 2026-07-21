from pathlib import Path
from unittest.mock import MagicMock
import subprocess
import sys

from langchain_core.messages import AIMessage

from coderio.agent.loop import run_agent
from coderio.agent.prompts import ActiveSkills
from coderio.agent.stream import NullStream
from coderio.config import Config
from coderio.session.store import Session
from coderio.skills.store import SkillStore
from coderio.skills.triggers import detect_stage
from coderio.tools import build_default_tools
from coderio.tools.permission import PermissionGate


def _model_returning(*ai_messages):
    """Mock model whose .stream() yields the given AIMessages in sequence per call."""
    model = MagicMock()
    calls = {"i": 0}

    def _stream(_msgs):
        i = calls["i"]
        calls["i"] += 1
        msg = ai_messages[min(i, len(ai_messages) - 1)]
        yield msg

    model.bind_tools.return_value.stream.side_effect = _stream
    return model


def _tc(name, args, mid="c1", content=""):
    return AIMessage(
        content=content,
        tool_calls=[{"name": name, "args": args, "id": mid, "type": "tool_call"}],
    )


def test_e2e_read_file_and_resume(tmp_path):
    f = tmp_path / "note.md"
    f.write_text("# Plan\nDo the thing.\n", encoding="utf-8")
    model = _model_returning(
        _tc("read_file", {"path": str(f)}),
        AIMessage(content="The note is a plan titled 'Plan'.", tool_calls=[]),
    )
    cfg = Config()
    tools = build_default_tools(cfg.tools.bash_shell)
    gate = PermissionGate("auto")
    store = SkillStore()
    active = ActiveSkills()
    session = Session.create(tmp_path / "sessions", {"model": "test"})
    answer = run_agent(
        user_input="Summarize note.md",
        model=model, tools=tools, gate=gate,
        skill_store=store, active_skills=active,
        session=session, stream=NullStream(), max_rounds=10,
    )
    assert "Plan" in answer
    assert any(m.role == "tool" for m in session.messages)

    resumed = Session.load_by_id(tmp_path / "sessions", session.id)
    assert resumed.messages[0].role == "user"


def test_e2e_stage_injection_on_implement(tmp_path):
    assert detect_stage("ok let's start implementing now") == "implement"


def test_e2e_confirm_gate_for_bash():
    class _Ask(PermissionGate):
        def __init__(self):
            super().__init__("confirm")
            self.asked = []

        def _ask(self, n, a):
            self.asked.append((n, a))
            return True

    g = _Ask()
    # destructive bash requires _ask in confirm mode; _ask returns True -> allowed
    assert g.check("bash", {"command": "ls"}) is True
    assert g.asked == [("bash", {"command": "ls"})]
    # read_file is not destructive -> always allowed without asking
    assert g.check("read_file", {"path": "x"}) is True


# ----------------------------------------------- CLI entry-point smoke tests
# These spawn the REAL interpreter running coderio.cli.app as a subprocess — they
# catch regressions that unit tests (which mock the model) structurally cannot:
# a bad flag, a broken import, a typo in the typer wiring all surface here. This
# is the layer that was missing when --tui was removed but kept erroring for the
# user: no test actually invoked the entry point.

def test_cli_help_exits_zero():
    """`coderio --help` must succeed. Guards the whole import + typer wiring."""
    r = subprocess.run(
        [sys.executable, "-m", "coderio.cli.app", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode == 0, f"--help failed:\n{r.stderr}"
    assert "coderio" in r.stdout.lower()


def test_cli_has_no_tui_flag():
    """The TUI is the sole entry point; --tui/--no-tui must not exist as options.

    Regression guard: removing the TUI-vs-REPL split once broke the user's launch
    flow because the removed --tui flag still error'd at the entry point.
    """
    r = subprocess.run(
        [sys.executable, "-m", "coderio.cli.app", "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert "--tui" not in r.stdout
    assert "--no-tui" not in r.stdout


def test_cli_unknown_flag_errors():
    """Sanity: an unknown flag must exit non-zero (typer rejects it)."""
    r = subprocess.run(
        [sys.executable, "-m", "coderio.cli.app", "--definitely-not-real"],
        capture_output=True, text=True, timeout=30,
    )
    assert r.returncode != 0


# ----------------------------------------------- state machine + compaction integration
# These verify the phase-1/2/3 architecture features work end-to-end through
# run_agent (not just in isolation): the phase timeline is persisted, and the
# context config flows through to _execute_turn.

def test_e2e_phase_timeline_persisted_to_session(tmp_path):
    """A turn that does read→write→bash leaves a phase_timeline system message
    in the session jsonl, recording the explore→implement→verify progression."""
    f = tmp_path / "target.py"
    f.write_text("print('hello')\n", encoding="utf-8")
    # Sequence: read_file → write_file → bash(pytest) → final text
    model = _model_returning(
        _tc("read_file", {"path": str(f)}),
        _tc("write_file", {"path": str(f)}, content=""),
        _tc("bash", {"command": "pytest"}, content=""),
        AIMessage(content="Done. All tests pass.", tool_calls=[]),
    )
    cfg = Config()
    tools = build_default_tools(cfg.tools.bash_shell)
    gate = PermissionGate("auto")
    store = SkillStore()
    active = ActiveSkills()
    session = Session.create(tmp_path / "sessions", {"model": "test"})
    run_agent(
        user_input="read target.py, update it, run tests",
        model=model, tools=tools, gate=gate,
        skill_store=store, active_skills=active,
        session=session, stream=NullStream(), max_rounds=10,
    )
    # The phase_timeline system message should be in the session.
    sys_msgs = [m for m in session.messages if m.role == "system" and m.kind == "phase_timeline"]
    assert len(sys_msgs) >= 1, "expected a phase_timeline system message"
    import json
    timeline = json.loads(sys_msgs[-1].content)
    states = [entry["state"] for entry in timeline]
    # The mock model writes without creating a todo first, so the write phase
    # is PLAN (write + no todos), not IMPLEMENT (write + todos). Either is valid
    # ground-truth derivation; what matters is the progression is captured.
    assert "explore" in states, f"explore missing from {states}"
    assert "plan" in states or "implement" in states, f"write phase missing from {states}"
    assert "verify" in states, f"verify missing from {states}"
    assert "complete" in states, f"complete missing from {states}"


def test_e2e_context_config_flows_through(tmp_path):
    """context_cfg=None (default) means no compaction — run_agent still works."""
    f = tmp_path / "x.py"
    f.write_text("x = 1\n", encoding="utf-8")
    model = _model_returning(
        _tc("read_file", {"path": str(f)}),
        AIMessage(content="Read it.", tool_calls=[]),
    )
    cfg = Config()
    tools = build_default_tools(cfg.tools.bash_shell)
    gate = PermissionGate("auto")
    store = SkillStore()
    active = ActiveSkills()
    session = Session.create(tmp_path / "sessions", {"model": "test"})
    # No context_cfg passed — defaults to None, compaction disabled.
    answer = run_agent(
        user_input="read x.py",
        model=model, tools=tools, gate=gate,
        skill_store=store, active_skills=active,
        session=session, stream=NullStream(), max_rounds=5,
    )
    assert "Read it" in answer


def test_e2e_disabled_context_config_does_not_crash(tmp_path):
    """A ContextConfig with enabled=False should behave like compaction off."""
    from coderio.config import ContextConfig
    f = tmp_path / "y.py"
    f.write_text("y = 2\n", encoding="utf-8")
    model = _model_returning(
        _tc("read_file", {"path": str(f)}),
        AIMessage(content="ok", tool_calls=[]),
    )
    cfg = Config()
    tools = build_default_tools(cfg.tools.bash_shell)
    session = Session.create(tmp_path / "sessions", {"model": "test"})
    answer = run_agent(
        user_input="read y.py",
        model=model, tools=tools, gate=PermissionGate("auto"),
        skill_store=SkillStore(), active_skills=ActiveSkills(),
        session=session, stream=NullStream(), max_rounds=5,
        context_cfg=ContextConfig(enabled=False),
    )
    assert "ok" in answer
