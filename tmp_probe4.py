"""Reproduce: model generates tool_calls with NO token stream in between.
This is the 'blind file reading' scenario — status bar should still show phases.

Simulates what the screenshot showed: read_file calls stacking up while the TUI
shows a frozen 'thinking 25.1s'. We drive run_agent with a mock model that yields
tool-call messages (no text tokens) to see if on_step_start/on_tool_start fire
and whether the status bar updates across the tool-execution gap.
"""
import asyncio
import time
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage
from coderio.cli.tui import CoderioTUI
from textual.widgets import Static, Input


def _tc(name, args, mid):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": mid, "type": "tool_call"}])


def _model_with_tool_calls_then_answer():
    """Model that issues 3 read_file calls (no text), then answers."""
    from langchain_core.messages import AIMessageChunk
    model = MagicMock()
    model.bind_tools = MagicMock(return_value=model)
    seq = [
        AIMessage(content="", tool_calls=[
            {"name": "read_file", "args": {"path": "a.py"}, "id": "1", "type": "tool_call"},
            {"name": "read_file", "args": {"path": "b.py"}, "id": "2", "type": "tool_call"},
            {"name": "read_file", "args": {"path": "c.py"}, "id": "3", "type": "tool_call"},
        ]),
        AIMessage(content="done reading all files", tool_calls=[]),
    ]
    calls = {"i": 0}
    def _stream(_msgs):
        i = calls["i"]
        calls["i"] += 1
        yield seq[min(i, len(seq)-1)]
    model.stream = MagicMock(side_effect=_stream)
    return model


async def main():
    import tempfile, os
    from pathlib import Path
    from coderio.agent.loop import run_agent
    from coderio.agent.prompts import ActiveSkills
    from coderio.config import Config
    from coderio.session.store import Session
    from coderio.skills.store import SkillStore
    from coderio.tools import build_default_tools
    from coderio.tools.permission import PermissionGate

    tmp = Path(tempfile.mkdtemp())
    for f in ["a.py", "b.py", "c.py"]:
        (tmp / f).write_text("# content\n", encoding="utf-8")

    app = CoderioTUI()
    phase_log = []
    orig_set = app._set_phase
    def log_phase(p, tool_name=""):
        phase_log.append((p, round(time.monotonic(), 2)))
        orig_set(p, tool_name)
    app._set_phase = log_phase

    # wire on_input to run_agent
    cfg = Config()
    store = SkillStore()
    active = ActiveSkills()
    session = Session.create(tmp / "sess", {"model": "test"})
    tools = build_default_tools()

    def on_input(line):
        run_agent(
            user_input=line, model=_model_with_tool_calls_then_answer(),
            tools=tools, gate=PermissionGate("auto"),
            skill_store=store, active_skills=active,
            session=session, stream=app, max_rounds=10,
            harness_enabled=False,
        )
    app._on_input = on_input

    async with app.run_test() as pilot:
        bar = app.query_one("#status-bar", Static)
        inp = app.query_one("#msg", Input)
        print(f"[初始] 状态栏={bar.content!r}")

        # submit a task
        inp.value = "read the files"
        await pilot.press("enter")

        # poll the status bar over time to see if it changes
        for i in range(8):
            await asyncio.sleep(0.3)
            await pilot.pause()
            print(f"[+{(i+1)*0.3:.1f}s] 状态栏={bar.content!r}")

        await pilot.pause()
        print(f"[最终] 状态栏={bar.content!r}")
        print(f"[phase序列] {phase_log}")

asyncio.run(main())
