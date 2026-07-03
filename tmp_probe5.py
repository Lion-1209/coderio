"""Reproduce WITH a SLOW model to see if the status bar updates during the wait.

The previous probe's mock returned instantly, so the agent finished before the
main thread could refresh. Real models have network latency (the screenshot
showed 25s frozen). This probe injects a 2s delay before each model response to
see if _tick / call_from_thread actually refreshes the bar during the wait.
"""
import asyncio
import time
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage
from coderio.cli.tui import CoderioTUI
from textual.widgets import Static, Input


def _slow_model():
    """Model with a 2s delay — simulates network/TTFT latency."""
    model = MagicMock()
    model.bind_tools = MagicMock(return_value=model)
    seq = [
        AIMessage(content="", tool_calls=[
            {"name": "read_file", "args": {"path": "a.py"}, "id": "1", "type": "tool_call"},
        ]),
        AIMessage(content="done", tool_calls=[]),
    ]
    calls = {"i": 0}
    def _stream(_msgs):
        i = calls["i"]
        calls["i"] += 1
        time.sleep(2.0)  # simulate 2s network wait (BLOCKS the agent thread)
        yield seq[min(i, len(seq)-1)]
    model.stream = MagicMock(side_effect=_stream)
    return model


async def main():
    import tempfile
    from pathlib import Path
    from coderio.agent.loop import run_agent
    from coderio.agent.prompts import ActiveSkills
    from coderio.config import Config
    from coderio.session.store import Session
    from coderio.skills.store import SkillStore
    from coderio.tools import build_default_tools
    from coderio.tools.permission import PermissionGate

    tmp = Path(tempfile.mkdtemp())
    (tmp / "a.py").write_text("# x\n", encoding="utf-8")

    app = CoderioTUI()
    cfg = Config()
    store = SkillStore()
    active = ActiveSkills()
    session = Session.create(tmp / "sess", {"model": "test"})
    tools = build_default_tools()

    def on_input(line):
        run_agent(
            user_input=line, model=_slow_model(),
            tools=tools, gate=PermissionGate("auto"),
            skill_store=store, active_skills=active,
            session=session, stream=app, max_rounds=10,
            harness_enabled=False,
        )
    app._on_input = on_input

    async with app.run_test() as pilot:
        bar = app.query_one("#status-bar", Static)
        inp = app.query_one("#msg", Input)
        inp.value = "read it"
        await pilot.press("enter")

        # poll during the 2s wait
        for i in range(10):
            await asyncio.sleep(0.3)
            await pilot.pause()
            print(f"[+{(i+1)*0.3:.1f}s] phase={app._phase} 状态栏={bar.content!r}")

asyncio.run(main())
