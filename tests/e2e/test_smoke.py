from pathlib import Path
from unittest.mock import MagicMock

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
