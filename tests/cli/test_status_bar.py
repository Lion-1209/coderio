"""Integration tests for the live status bar (phase indicator + real-time timer).

Drives the REAL CoderioTUI via run_test. Verifies the status bar reflects the
four lifecycle phases (idle/thinking/tool/responding) and that the elapsed timer
ticks in real time — the three user-reported bugs were: silent freeze during
network wait, thinking rendered only after the fact, and the timer freezing
during tool execution.
"""
import asyncio
import pytest

from coderio.cli.tui import CoderioTUI
from textual.widgets import Static


def _bar(app) -> str:
    return str(app.query_one("#status-bar", Static).content)


@pytest.mark.asyncio
async def test_status_bar_idle_on_start():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "就绪" in _bar(app)


@pytest.mark.asyncio
async def test_status_bar_shows_thinking_on_step_start():
    """on_step_start (a model call begins) must immediately show 'thinking', so
    the network-wait for the first token is visibly accounted for."""
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        assert "思考" in _bar(app)


@pytest.mark.asyncio
async def test_status_bar_timer_ticks_in_real_time():
    """The elapsed counter must advance over wall-clock time, not freeze. This is
    the core regression: previously there was no timer, so the screen looked hung."""
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        before = _bar(app)
        await asyncio.sleep(0.3)
        await pilot.pause()
        after = _bar(app)
        assert "思考" in before and "思考" in after
        # the elapsed number must have grown
        assert _elapsed(after) > _elapsed(before)


@pytest.mark.asyncio
async def test_status_bar_shows_tool_name():
    """on_tool_start must switch the bar to show the tool name (e.g. 'bash'),
    not a frozen 'thinking' label — the tool-execution freeze bug."""
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_tool_start("bash", {"command": "ls"})
        await pilot.pause()
        assert "执行" in _bar(app)
        assert "bash" in _bar(app)


@pytest.mark.asyncio
async def test_status_bar_responding_on_first_token():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        app.on_token("hello")  # first visible token
        await pilot.pause()
        assert "输出" in _bar(app)


@pytest.mark.asyncio
async def test_status_bar_returns_to_idle_on_finish():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        app.on_token("x")
        await pilot.pause()
        app.on_finish()
        await pilot.pause()
        assert "就绪" in _bar(app)


def _elapsed(text: str) -> float:
    """Extract the seconds number from a status-bar string like '⠋ 思考中 · 1.2s'."""
    import re
    m = re.search(r"([\d.]+)s", text)
    return float(m.group(1)) if m else 0.0
