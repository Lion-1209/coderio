"""Integration tests for the live status bar (phase indicator + real-time timer).

Drives the REAL CoderioTUI via run_test. The StatusBar is a custom Widget with
its own render() + set_interval heartbeat (refresh layout=False), rather than a
Static.update — this avoids a full layout recompute on every tick, which could
stall in a real terminal with a large history pane.
"""
import asyncio
import re
import pytest

from coderio.cli.tui import CoderioTUI, StatusBar


def _bar(app) -> StatusBar:
    return app.query_one(StatusBar)


def _render(app) -> str:
    """Render the StatusBar widget and return its text."""
    bar = _bar(app)
    r = bar.render()
    # rich Text -> str
    return str(r.plain if hasattr(r, "plain") else r)


@pytest.mark.asyncio
async def test_status_bar_idle_on_start():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "就绪" in _render(app)


@pytest.mark.asyncio
async def test_status_bar_shows_thinking_on_step_start():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        assert "思考" in _render(app)


@pytest.mark.asyncio
async def test_status_bar_timer_ticks_in_real_time():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        before = _render(app)
        await asyncio.sleep(0.3)
        await pilot.pause()
        after = _render(app)
        assert "思考" in before and "思考" in after
        assert _elapsed(after) > _elapsed(before)


@pytest.mark.asyncio
async def test_status_bar_shows_tool_name():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_tool_start("bash", {"command": "ls"})
        await pilot.pause()
        text = _render(app)
        assert "执行" in text
        assert "bash" in text


@pytest.mark.asyncio
async def test_status_bar_responding_on_first_token():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        app.on_step_start()
        await pilot.pause()
        app.on_token("hello")
        await pilot.pause()
        assert "输出" in _render(app)


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
        assert "就绪" in _render(app)


@pytest.mark.asyncio
async def test_status_bar_phase_state_on_widget():
    """The StatusBar widget itself tracks phase state (read by render())."""
    app = CoderioTUI()
    async with app.run_test() as pilot:
        await pilot.pause()
        bar = _bar(app)
        assert bar.phase == "idle"
        app.on_step_start()
        await pilot.pause()
        assert bar.phase == "thinking"
        app.on_tool_start("read_file", {"path": "x"})
        await pilot.pause()
        assert bar.phase == "tool"
        assert bar.tool_name == "read_file"


def _elapsed(text: str) -> float:
    """Extract the seconds number from a status-bar string like '⠋ 思考中 · 1.2s'."""
    m = re.search(r"([\d.]+)s", text)
    return float(m.group(1)) if m else 0.0
