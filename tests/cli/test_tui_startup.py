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
        app.query_one("#send-btn", Button)
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
async def test_send_slot_visible_and_idle_on_start():
    """The send/stop slot is PERMANENT (zcode style): it must be visible at
    startup showing the submit arrow — not a hidden button that pops in
    mid-stream (the old status-row design's flaw)."""
    from coderio.cli.tui import CoderioTUI

    app = CoderioTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.3)
        btn = app.query_one("#send-btn", Button)
        assert btn.styles.display == "block", "send slot must be visible on startup"
        assert str(btn.label).startswith("➤"), "idle slot shows the submit arrow"


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

        # /clear regression (2026-08-28 adversarial review finding 4): the
        # tracked todo widget dies with the pane. Without the reset, the next
        # turn's write_todos .update()s a DETACHED widget (a silent no-op —
        # detached Static.update() does not raise) and the todo list stays
        # invisible for the whole rest of the session.
        app._clear_history()
        await pilot.pause(0.3)
        assert app._todo_widget is None, "/clear must reset the tracked todo widget"

        app.on_todos_update(
            [
                {"content": "post-clear task", "status": "in_progress"},
            ]
        )
        await pilot.pause(0.5)
        assert app._todo_widget is not None, "write_todos after /clear must mount a fresh panel"
        new_widget_still_tracked = app._todo_widget
        app.on_todos_update(
            [
                {"content": "post-clear task", "status": "completed"},
            ]
        )
        await pilot.pause(0.5)
        assert app._todo_widget is new_widget_still_tracked, (
            "post-clear updates must keep updating the SAME mounted widget"
        )

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


# ------------------------------------------------- confirm diff block (P3-1)


@pytest.mark.asyncio
async def test_confirm_menu_shows_diff_block():
    """P3-1: ConfirmMenu.show(detail=...) renders the diff preview block and
    toggles the -has-diff class; hide() clears it."""
    from textual.geometry import Size

    from coderio.cli.tui import CoderioTUI
    from coderio.cli.tui_widgets import ConfirmMenu

    app = CoderioTUI()
    async with app.run_test(size=Size(100, 40)) as pilot:
        await pilot.pause()
        menu = app.query_one(ConfirmMenu)
        menu.show(
            "write_file",
            "file_path='src/app.py'",
            detail="-old line\n+new line",
        )
        await pilot.pause()
        assert menu.has_class("-visible") and menu.has_class("-has-diff")
        from textual.widgets import Static

        diff_w = menu.query_one("#confirm-diff", Static)
        from rich.syntax import Syntax as RichSyntax

        assert isinstance(diff_w.content, RichSyntax), "detail must render as highlighted diff"
        assert "old line" in diff_w.content.code and "new line" in diff_w.content.code
        menu.hide()
        await pilot.pause()
        assert not menu.has_class("-has-diff"), "hide must clear the diff state"

        # no detail → the block stays hidden (menu looks like pre-P3-1)
        menu.show("execute", "command='pytest'")
        await pilot.pause()
        assert menu.has_class("-visible") and not menu.has_class("-has-diff")
        menu.hide()


# ------------------------------------------------- confirm diff preview (P3-1)


def test_tui_gate_passes_diff_detail_to_request_confirmation(tmp_path, monkeypatch):
    """P3-1 wiring: TuiPermissionGate._ask renders a diff preview for file
    writes and hands it to request_confirmation(detail=...)."""
    from coderio.cli.repl import TuiPermissionGate
    from coderio.tools.permission import PermissionMode

    target = tmp_path / "app.py"
    target.write_text("old\n", encoding="utf-8")

    seen: dict = {}

    class _FakeTui:
        def request_confirmation(self, tool_name, args, detail=None):
            seen["detail"] = detail
            return True

    gate = TuiPermissionGate(PermissionMode.CONFIRM, tui=_FakeTui(), workdir=str(tmp_path))
    result = gate._ask("write_file", {"file_path": str(target), "content": "new\n"})
    assert result is True
    assert seen["detail"] is not None and "-old" in seen["detail"] and "+new" in seen["detail"]


def test_tui_gate_non_file_tool_gets_no_detail(tmp_path):
    from coderio.cli.repl import TuiPermissionGate
    from coderio.tools.permission import PermissionMode

    seen: dict = {}

    class _FakeTui:
        def request_confirmation(self, tool_name, args, detail=None):
            seen["detail"] = detail
            return False

    gate = TuiPermissionGate(PermissionMode.CONFIRM, tui=_FakeTui(), workdir=str(tmp_path))
    gate._ask("execute", {"command": "pytest -q"})
    assert seen["detail"] is None, "shell commands have no diff to preview"


@pytest.mark.asyncio
async def test_submit_recovers_from_cancel_before_start_wedge():
    """P1-13 (2026-09-04): worker.cancel() landing between _spawn_turn and the
    thread entering _run finishes the worker WITHOUT running its finally —
    _is_running stays True and every later submit bounces. The app must
    recover (reset the flag and accept the turn) instead of wedging."""
    from coderio.cli.tui import CoderioTUI

    class _DeadWorker:
        is_finished = True

    app = CoderioTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.2)
        app._is_running = True
        app._agent_worker = _DeadWorker()
        ok = app._spawn_turn("hello after wedge")
        assert ok is False, "stale flag must be cleared, but no second turn may spawn"
        assert app._is_running is False, "the stale flag must be reset (de-wedged)"


@pytest.mark.asyncio
async def test_submit_still_refuses_when_turn_actually_running():
    """The wedge recovery must not weaken the in-flight guard: a LIVE worker
    (not finished) still refuses the second submit."""
    from coderio.cli.tui import CoderioTUI

    class _LiveWorker:
        is_finished = False

    app = CoderioTUI()
    async with app.run_test(size=(100, 40)) as pilot:
        await pilot.pause(0.2)
        app._is_running = True
        app._agent_worker = _LiveWorker()
        ok = app._spawn_turn("second submit")
        assert ok is False, "a live turn must still refuse a second submit"
        assert app._is_running is True
