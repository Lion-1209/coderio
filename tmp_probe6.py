"""Reproduce the REAL failure: model streams ONLY tool_calls, no text/thinking,
for a LONG time (25s). Does _tick keep refreshing?

This mimics the screenshot: model deciding which files to read, generating
tool_call chunks over many seconds with zero text tokens and zero thinking.
"""
import asyncio
import time
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage, AIMessageChunk
from coderio.cli.tui import CoderioTUI
from textual.widgets import Static, Input


def _long_toolcall_model():
    """Model that takes 3s then returns tool_calls with NO text/thinking."""
    model = MagicMock()
    model.bind_tools = MagicMock(return_value=model)
    calls = {"i": 0}
    def _stream(_msgs):
        calls["i"] += 1
        # 3s of NOTHING — no chunks yielded, just blocking (simulates a provider
        # that holds the connection open computing tool calls, sending nothing)
        time.sleep(3.0)
        if calls["i"] == 1:
            yield AIMessage(content="", tool_calls=[
                {"name": "read_file", "args": {"path": "a.py"}, "id": "1", "type": "tool_call"},
            ])
        else:
            yield AIMessage(content="done", tool_calls=[])
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
    active = ActiveSkills()
    session = Session.create(tmp / "sess", {"model": "test"})
    tools = build_default_tools()

    def on_input(line):
        run_agent(
            user_input=line, model=_long_toolcall_model(),
            tools=tools, gate=PermissionGate("auto"),
            skill_store=SkillStore(), active_skills=active,
            session=session, stream=app, max_rounds=10,
            harness_enabled=False,
        )
    app._on_input = on_input

    async with app.run_test() as pilot:
        bar = app.query_one("#status-bar", Static)
        inp = app.query_one("#msg", Input)
        inp.value = "read"
        await pilot.press("enter")
        for i in range(12):
            await asyncio.sleep(0.3)
            await pilot.pause()
            print(f"[+{(i+1)*0.3:.1f}s] phase={app._phase} 状态栏={bar.content!r}")

asyncio.run(main())
