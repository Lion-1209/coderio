"""Headless TUI startup smoke tests.

Verifies the TUI can start, compose its widget tree, render the status bar,
and handle input — without a real terminal or model. Uses Textual's run_test.
"""

from __future__ import annotations

import pytest
from textual.widgets import Button, Input


@pytest.mark.asyncio
async def test_tui_starts_and_widgets_exist():
    """All critical widgets mount successfully on startup."""
    from coderio.cli.tui import CoderioTUI

    app = CoderioTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.3)
        # Every widget that compose() yields must be findable.
        app.query_one("#msg", Input)
        app.query_one("#history")
        app.query_one("#interrupt-btn", Button)
        app.query_one("StatusBar")
        app.query_one("CommandMenu")
        app.query_one("ConfirmMenu")


@pytest.mark.asyncio
async def test_status_bar_renders_idle():
    """StatusBar renders '(就绪)' when idle."""
    from coderio.cli.tui import CoderioTUI

    app = CoderioTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.3)
        bar = app.query_one("StatusBar")
        rendered = str(bar.render())
        assert "就绪" in rendered, f"expected '就绪' in status bar, got: {rendered}"


@pytest.mark.asyncio
async def test_input_has_placeholder():
    """The main input has the expected placeholder text."""
    from coderio.cli.tui import CoderioTUI

    app = CoderioTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.3)
        inp = app.query_one("#msg", Input)
        assert "输入消息" in inp.placeholder


@pytest.mark.asyncio
async def test_interrupt_btn_hidden_on_idle():
    """Interrupt button is hidden when no agent is running."""
    from coderio.cli.tui import CoderioTUI

    app = CoderioTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.3)
        btn = app.query_one("#interrupt-btn", Button)
        assert btn.styles.display == "none", "interrupt button should be hidden when idle"


@pytest.mark.asyncio
async def test_todos_render_as_dynamic_checklist():
    """Todos render as a dynamic checklist that updates in-place (Claude Code style).

    First write_todos mounts the checklist. Second write_todos updates the SAME
    widget (no duplicate). on_finish resets the widget reference.
    """
    from textual.containers import VerticalScroll

    from coderio.cli.tui import CoderioTUI

    app = CoderioTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.3)

        # First write_todos: 1/3 done.
        app.on_todos_update(
            [
                {"content": "fix typo", "status": "completed"},
                {"content": "add tests", "status": "in_progress"},
                {"content": "update docs", "status": "pending"},
            ]
        )
        await pilot.pause(0.5)
        history = app.query_one("#history", VerticalScroll)
        count_after_first = len(list(history.children))
        assert count_after_first >= 1, "history should have the todo checklist"
        assert app._todo_widget is not None, "todo widget should be tracked"

        # Second write_todos: 2/3 done — should UPDATE, not add new widget.
        app.on_todos_update(
            [
                {"content": "fix typo", "status": "completed"},
                {"content": "add tests", "status": "completed"},
                {"content": "update docs", "status": "in_progress"},
            ]
        )
        await pilot.pause(0.5)
        count_after_second = len(list(history.children))
        assert count_after_second == count_after_first, "second update should not add a new widget"

        # on_finish resets the widget reference.
        app.on_finish()
        assert app._todo_widget is None, "todo widget should be reset on finish"


@pytest.mark.asyncio
async def test_dialog_flow_renders_output():
    """Simulate a full agent turn: on_step_start → on_token → on_finish.

    Verifies the TUI's stream protocol correctly renders a response:
    - tokens accumulate in the live output widget
    - on_finish mounts the final Markdown Panel in history
    - status bar returns to idle
    """
    from textual.containers import VerticalScroll

    from coderio.cli.tui import CoderioTUI

    app = CoderioTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.3)

        # Simulate the agent's stream callbacks (what run_deep_agent would call).
        app.on_step_start(step=1)
        await pilot.pause(0.1)
        app.on_token("你好！")
        await pilot.pause(0.1)
        app.on_token("我是 coderio。")
        await pilot.pause(0.1)
        app.on_finish()
        await pilot.pause(0.5)

        # The finalize instruction should have mounted a final Panel.
        history = app.query_one("#history", VerticalScroll)
        assert len(list(history.children)) >= 1, "history should have at least the final panel"

        # Status bar should be back to idle.
        bar = app.query_one("StatusBar")
        assert bar.phase == "idle", f"expected idle after finish, got {bar.phase}"


@pytest.mark.asyncio
async def test_tool_call_renders_in_history():
    """Simulate a tool call: on_tool_start → on_tool_end → on_token → on_finish.

    Verifies tool events are rendered (the green ⏺ line + result).
    """
    from textual.containers import VerticalScroll

    from coderio.cli.tui import CoderioTUI

    app = CoderioTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.3)

        app.on_step_start(step=1)
        await pilot.pause(0.1)
        app.on_tool_start("read_file", {"path": "/README.md"})
        await pilot.pause(0.1)
        app.on_tool_end("read_file", "file content here")
        await pilot.pause(0.1)
        app.on_token("分析完成。")
        await pilot.pause(0.1)
        app.on_finish()
        await pilot.pause(0.5)

        history = app.query_one("#history", VerticalScroll)
        widgets = list(history.children)
        assert len(widgets) >= 1, "history should have rendered content"

        bar = app.query_one("StatusBar")
        assert bar.phase == "idle"


@pytest.mark.asyncio
async def test_token_count_displays_during_turn():
    """Turn token count shows in status bar during a turn, hides after."""
    from coderio.cli.tui import CoderioTUI

    app = CoderioTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.3)
        bar = app.query_one("StatusBar")

        app.on_step_start(step=1)
        app.add_usage({"input_tokens": 1000, "output_tokens": 200})
        await pilot.pause(0.2)
        assert bar.turn_tokens == 1200, f"expected 1200, got {bar.turn_tokens}"

        app.on_finish()
        await pilot.pause(0.2)
        assert bar.turn_tokens == 0, "tokens should reset to 0 after finish"


@pytest.mark.asyncio
async def test_harness_continue_renders_notice():
    """Harness force-continue shows a dim notice + flushes buffer as intermediate."""
    from textual.containers import VerticalScroll

    from coderio.cli.tui import CoderioTUI

    app = CoderioTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.3)

        app.on_step_start(step=1)
        app.on_token("这是第一段分析。")
        await pilot.pause(0.1)
        app.on_harness_continue("[harness] You cited loop.py but did not read it.")
        await pilot.pause(0.3)

        history = app.query_one("#history", VerticalScroll)
        widgets = list(history.children)
        # The buffer should be flushed as intermediate + a dim notice rendered.
        assert len(widgets) >= 1, "should have flushed intermediate output"
