"""Regression tests for three UX improvements:
1. File-modification visual feedback (write tools get yellow display)
2. /mode picker (no-arg opens ModePickerScreen)
3. Error recovery (exception shows red panel + refills input)
"""

import pytest
from textual.geometry import Size
from textual.widgets import Static

from coderio.cli.tui import CoderioTUI
from coderio.cli.tui_screens import ModePickerScreen


def _all_static_texts(app) -> list[str]:
    """Collect text from all Static widgets in the history pane."""
    texts = []
    for w in app.query_one("#history").query(Static):
        c = str(getattr(w, "content", ""))
        if c:
            texts.append(c)
    return texts


# --- Feature 1: write tool prominent display ---


@pytest.mark.asyncio
async def test_write_tool_shown_in_yellow():
    """write_file result must produce a yellow (not dim) render instruction."""
    app = CoderioTUI()
    async with app.run_test(size=Size(80, 24)) as pilot:
        await pilot.pause()
        app.on_tool_end("write_file", "Wrote 50 chars to src/foo.py")
        app._drain_render_queue()
        await pilot.pause()
        texts = _all_static_texts(app)
        assert any("foo.py" in t or "Wrote" in t for t in texts), f"write result not shown: {texts}"


@pytest.mark.asyncio
async def test_todo_update_scroll_category_matches_pre_p23_semantics():
    """REGRESSION (adversarial review, P2-3): the todo render category is
    dynamic, not the table's hardcoded "final" — an EMPTY list and an
    in-place update must report "none" (no multi-stage final scroll); only a
    fresh mount counts as "final". The old _h_todo_update had exactly this
    dynamic behavior; drain_ui's return-value override preserves it."""
    app = CoderioTUI()
    async with app.run_test(size=Size(80, 24)) as pilot:
        await pilot.pause()
        todos = [{"content": "task", "status": "pending"}]

        # Empty list → "none": nothing queued anyway, but the drain must not
        # report a final scroll.
        app.on_todos_update([])
        did_streaming, did_final = app._stream.drain_ui()
        assert (did_streaming, did_final) == (False, False)

        # First real update → fresh mount → "final".
        app.on_todos_update(todos)
        did_streaming, did_final = app._stream.drain_ui()
        assert did_final is True

        await pilot.pause()
        # In-place update (widget attached) → "none", not "final".
        todos[0]["status"] = "completed"
        app.on_todos_update(todos)
        did_streaming, did_final = app._stream.drain_ui()
        assert did_final is False, "in-place todo update must not schedule the final scroll"


@pytest.mark.asyncio
async def test_static_one_element_tuple_still_renders():
    """REGRESSION (adversarial review, P2-3): ("static", text) without a style
    element must render unstyled — the old _h_static tolerated it, and a
    drain-side unpack TypeError would silently drop the line."""
    app = CoderioTUI()
    async with app.run_test(size=Size(80, 24)) as pilot:
        await pilot.pause()
        app._stream.render_q.append(("static", "bare line no style"))
        did_streaming, did_final = app._stream.drain_ui()
        assert (did_streaming, did_final) == (False, True), "1-element static must render, not be dropped"
        await pilot.pause()
        texts = _all_static_texts(app)
        assert any("bare line no style" in t for t in texts), f"line lost: {texts}"


@pytest.mark.asyncio
async def test_turn_end_summary_shows_modified_files():
    """on_turn_end with writes renders a summary panel."""
    app = CoderioTUI()
    async with app.run_test(size=Size(80, 24)) as pilot:
        await pilot.pause()
        # Drain any pending items first, then call on_turn_end and check
        # the queue has exactly one "panel" action.
        app._drain_render_queue()
        app.on_turn_end(["src/foo.py (write_file)", "src/bar.py (edit_file)"])
        # Pop the queued item and verify it's a panel action.
        assert len(app._stream.render_q) >= 1
        action, *args = app._stream.render_q.popleft()
        assert action == "panel", f"expected panel, got {action}"
        from rich.panel import Panel

        assert isinstance(args[0], Panel)


@pytest.mark.asyncio
async def test_turn_end_empty_writes_no_panel():
    """on_turn_end with empty writes list does NOT render a panel."""
    app = CoderioTUI()
    async with app.run_test(size=Size(80, 24)) as pilot:
        await pilot.pause()
        initial_count = len(list(app.query_one("#history").children))
        app.on_turn_end([])
        app._drain_render_queue()
        await pilot.pause()
        assert len(list(app.query_one("#history").children)) == initial_count


# --- Feature 2: /mode picker ---


@pytest.mark.asyncio
async def test_mode_picker_shows_four_modes():
    """ModePickerScreen lists the four permission tiers."""
    app = CoderioTUI()
    async with app.run_test(size=Size(80, 24)) as pilot:
        await pilot.pause()
        app.push_screen(ModePickerScreen(active_mode="full"))
        await pilot.pause()
        from textual.widgets import ListView

        lv = app.screen.query_one("#mode-list", ListView)
        assert len(lv.children) == 4


@pytest.mark.asyncio
async def test_mode_picker_marks_active():
    """The active mode is marked with star."""
    app = CoderioTUI()
    async with app.run_test(size=Size(80, 24)) as pilot:
        await pilot.pause()
        app.push_screen(ModePickerScreen(active_mode="plan"))
        await pilot.pause()
        from textual.widgets import ListView

        items = app.screen.query_one("#mode-list", ListView).children
        texts = [str(c.query_one(Static).content) for c in items]
        plan_item = [t for t in texts if "plan" in t]
        assert plan_item, f"plan not found in {texts}"
        assert "★" in plan_item[0], f"plan not marked active: {plan_item[0]}"


# --- Feature 3: error recovery ---


@pytest.mark.asyncio
async def test_empty_response_shows_red_panel():
    """_empty_response in on_tool_end should render a panel (not dim text)."""
    app = CoderioTUI()
    async with app.run_test(size=Size(80, 24)) as pilot:
        await pilot.pause()
        app._drain_render_queue()
        app.on_tool_end("_empty_response", "(模型连续返回空响应，已重试 2 次仍无输出。)")
        # Should push at least one "panel" action, not a dim "static".
        actions = []
        while app._stream.render_q:
            actions.append(app._stream.render_q.popleft())
        panel_count = sum(1 for a in actions if a[0] == "panel")
        dim_static_count = sum(1 for a in actions if a[0] == "static" and len(a) > 2 and "dim" in (a[2] or ""))
        assert panel_count >= 1, f"expected panel for empty response, actions: {actions}"
        assert dim_static_count == 0, f"should NOT be dim static: {actions}"


@pytest.mark.asyncio
async def test_second_submit_rejected_while_turn_running():
    """P1-2 regression (2026-09-02 audit, finding 3): a turn in flight must
    not accept a second engine submission — run_worker(exclusive=True) only
    cancels Textual's worker wrapper, the Python thread would keep running
    and two engines would interleave writes to the same session jsonl."""
    from textual.widgets import Input

    app = CoderioTUI()
    async with app.run_test(size=Size(80, 24)) as pilot:
        await pilot.pause()
        submits: list[str] = []
        app._on_input = lambda line: submits.append(line)

        # First submit (idle) → spawns normally, flips _is_running via _spawn_turn.
        inp = app.query_one("#msg", Input)
        inp.value = "first prompt"
        await pilot.press("enter")
        await pilot.pause()
        assert len(submits) == 1, "idle submit must reach the engine"

        # Second submit while the turn is "running" → rejected, input kept.
        app._is_running = True  # simulate the engine still working
        inp.value = "second prompt"
        await pilot.press("enter")
        await pilot.pause()
        assert len(submits) == 1, "second submit while running must NOT spawn another turn"
        assert inp.value == "second prompt", "input must be kept so the user can resubmit after Esc"


@pytest.mark.asyncio
async def test_send_button_submit_goes_through_worker_guard():
    """Audit 2026-09-02 P1: the ➤ button called _on_input DIRECTLY on the main
    thread — UI froze for the whole turn, _is_running never flipped (button
    stayed ➤, Esc dead, concurrency guard bypassed). It must go through
    _spawn_turn like Enter, and the running guard must apply to it too."""
    from textual.widgets import Input

    app = CoderioTUI()
    async with app.run_test(size=Size(80, 24)) as pilot:
        await pilot.pause()
        submits: list[str] = []
        seen_flags: list[bool] = []

        def _engine(line):
            submits.append(line)
            # sampled INSIDE the worker: _spawn_turn must have flipped the
            # flag synchronously (the old code flipped it from the worker,
            # too late to gate a rapid resubmit — TOCTOU)
            seen_flags.append(app._is_running)

        app._on_input = _engine

        inp = app.query_one("#msg", Input)
        inp.value = "via button"
        app._submit_current_input()
        await pilot.pause()
        assert len(submits) == 1, "idle button submit must spawn the worker"
        assert seen_flags == [True], "_spawn_turn must flip the flag synchronously (worker samples it True)"
        assert inp.value == "", "input cleared on accepted submit"

        # While running, a second button submit must be REJECTED (not run on
        # the main thread), input kept.
        app._is_running = True  # simulate the engine still working
        inp.value = "second via button"
        app._submit_current_input()
        await pilot.pause()
        assert len(submits) == 1, "button submit while running must NOT spawn another turn"
        assert inp.value == "second via button", "input kept for resubmit"


@pytest.mark.asyncio
async def test_send_button_noop_on_empty_input():
    from textual.widgets import Input

    app = CoderioTUI()
    async with app.run_test(size=Size(80, 24)) as pilot:
        await pilot.pause()
        submits: list[str] = []
        app._on_input = lambda line: submits.append(line)
        inp = app.query_one("#msg", Input)
        inp.value = "   "
        app._submit_current_input()
        await pilot.pause()
        assert submits == [], "blank input must not spawn a turn"
