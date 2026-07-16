"""Regression tests for the long-output truncation bug.

The symptom: when the agent's final answer is long (taller than the screen), the
TUI mounts a Markdown Panel into the history VerticalScroll and scrolls to the
bottom. On some terminals (VSCode integrated terminal / Conpty) the bottom rows
didn't repaint until the next input — the 'content appears on the next message'
bug. The fix is _conpty_repaint_nudge: a single deferred layout refresh scoped
to #history, fired AFTER the scroll timers settle.

These tests verify the invariant that matters: after a long Panel is mounted and
the full scroll+nudge sequence runs, (1) scroll_y is pinned to max_scroll (the
bottom is actually in view), and (2) the Panel's full height is measured (not
clamped to viewport). Both hold in headless mode; the nudge is belt-and-suspenders
for real-terminal Conpty rendering that can't be reproduced headless.
"""
import asyncio

import pytest

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from coderio.cli.tui import CoderioTUI


# A body that renders to well over 24 rows (taller than the test viewport).
LONG_BODY = (
    "# 最终回答\n\n"
    + "\n".join(f"第 {i} 行内容：这是测试行的内容，用来撑满超过一屏。" for i in range(1, 41))
    + "\n\n## 最后\n这里是真正的结尾，最后一句应该可见。"
)


def _mount_panel(app, body: str = LONG_BODY) -> Static:
    """Mount a Markdown Panel into the history, mirroring _mount_final_panel."""
    history = app.query_one("#history", VerticalScroll)
    widget = Static(Panel(Markdown(body), border_style="blue", title="coderio"))
    history.mount(widget)
    return widget


@pytest.mark.asyncio
async def test_long_panel_measures_full_height():
    """A Panel taller than the viewport must be measured at its REAL height,
    not clamped to the viewport. (The original truncation root cause was Static
    widgets defaulting to viewport height.)"""
    app = CoderioTUI()
    async with app.run_test(size=(80, 24)) as pilot:
        widget = _mount_panel(app)
        await pilot.pause()
        await asyncio.sleep(0.3)
        await pilot.pause()
        # LONG_BODY renders to ~48 rows; must be far taller than the 24-row screen.
        assert widget.virtual_size.height > 30, (
            f"Panel height {widget.virtual_size.height} clamped — expected >30"
        )


@pytest.mark.asyncio
async def test_scroll_pinned_to_bottom_after_nudge():
    """After mount + scroll + the Conpty repaint nudge, scroll_y must equal
    max_scroll_y (the bottom of the content is in view). If the nudge corrupted
    scroll state, this fails."""
    app = CoderioTUI()
    async with app.run_test(size=(80, 24)) as pilot:
        widget = _mount_panel(app)
        await pilot.pause()
        # Drive the same deferred-scroll sequence as _mount_final_panel.
        history = app.query_one("#history", VerticalScroll)
        history.scroll_end(animate=False)
        await pilot.pause()
        await asyncio.sleep(0.1)
        # Fire the Conpty nudge directly (it would normally run on a 0.6s timer).
        app._conpty_repaint_nudge()
        await pilot.pause()
        await asyncio.sleep(0.1)
        await pilot.pause()
        assert history.scroll_y == pytest.approx(history.max_scroll_y), (
            f"scroll_y={history.scroll_y} != max={history.max_scroll_y} — "
            "bottom not in view after nudge"
        )
        # And the Panel height must remain correct (nudge didn't shrink it).
        assert widget.virtual_size.height > 30


@pytest.mark.asyncio
async def test_short_panel_not_corrupted_by_nudge():
    """The previous self.refresh() at mount time broke SHORT outputs. Verify the
    new scoped _conpty_repaint_nudge leaves a short Panel intact and visible."""
    short_body = "# 短回答\n\n这是一段短内容，不需要滚动。"
    app = CoderioTUI()
    async with app.run_test(size=(80, 24)) as pilot:
        widget = _mount_panel(app, short_body)
        await pilot.pause()
        history = app.query_one("#history", VerticalScroll)
        history.scroll_end(animate=False)
        await pilot.pause()
        app._conpty_repaint_nudge()
        await pilot.pause()
        await asyncio.sleep(0.1)
        # Short content: no scrolling needed, scroll stays at 0.
        assert history.max_scroll_y == 0
        assert history.scroll_y == 0
        # And the content is short (sanity — wasn't inflated/corrupted).
        assert widget.virtual_size.height < 20
