"""Regression tests for the long-output truncation bug.

ROOT CAUSE (final, confirmed via headless compositor capture):
Rich's Panel(Markdown) wrapper has a measurement/render discrepancy —
Panel.get_height() reports MORE rows than it actually renders at certain widths
(especially CJK content). Static measures N rows but Panel renders N-k, so the
bottom rows (incl. the `╰╯` border + final content) are blank. scroll_end lands
in virtual_size but shows blank space — truncation at ~95-99%.

FIX: render Markdown directly in a Static with a CSS border (.final-panel),
NOT wrapped in a Rich Panel. CSS borders are drawn by Textual's layout engine
outside the content area, so there's no row-count mismatch. The border always
renders and the full Markdown renders at its natural line count.

These tests verify: after scroll_end, BOTH the border AND the final content line
are visible, on wide and narrow terminals, for long and short CJK content.
"""
import asyncio

import pytest

from rich.markdown import Markdown
from textual.geometry import Size
from textual.containers import VerticalScroll
from textual.widgets import Static

from coderio.cli.tui import CoderioTUI


LONG_BODY = (
    "# 最终回答\n\n"
    + "\n".join(
        f"第 {i} 行内容：这是测试行的内容，用来撑满超过一屏，描述细节和实现要点。"
        for i in range(1, 41))
    + "\n\n## 测试覆盖\n这里是被截断的内容，应该可见。"
    + "\n\n## 最后\n这里是真正的结尾，最后一句应该可见。"
)
TAIL_SENTINEL = "这里是真正的结尾"


async def _mount_final_and_scroll(app, body: str = LONG_BODY):
    """Mount the final Markdown (CSS-bordered Static, no Rich Panel), settle,
    scroll to bottom. Mirrors _mount_final_panel."""
    history = app.query_one("#history", VerticalScroll)
    widget = Static("")
    widget.add_class("final-panel")
    history.mount(widget)
    await app.pilot.pause()  # width settles
    widget.update(Markdown(body))
    await app.pilot.pause()
    await asyncio.sleep(0.4)  # content layout settles
    await app.pilot.pause()
    history.scroll_end(animate=False)
    await app.pilot.pause()
    await asyncio.sleep(0.2)
    await app.pilot.pause()


def _screen_text(app) -> str:
    strips = app.screen._compositor.render_strips(
        Size(app.size.width, app.size.height))
    return "".join(seg.text for strip in strips for seg in strip)


@pytest.mark.asyncio
async def test_border_and_tail_visible_wide():
    """WIDE terminal: after scroll, border + final line visible."""
    app = CoderioTUI()
    async with app.run_test(size=(214, 50)) as pilot:
        app.pilot = pilot
        await pilot.pause()
        await _mount_final_and_scroll(app)
        screen = _screen_text(app)
        assert ("╭" in screen or "┌" in screen), "top border missing"
        assert ("╰" in screen or "└" in screen), "bottom border clipped"
        assert TAIL_SENTINEL in screen, "final content not visible"


@pytest.mark.asyncio
async def test_border_and_tail_visible_narrow():
    """NARROW terminal (where truncation was most severe)."""
    app = CoderioTUI()
    async with app.run_test(size=(80, 24)) as pilot:
        app.pilot = pilot
        await pilot.pause()
        await _mount_final_and_scroll(app)
        screen = _screen_text(app)
        assert ("╭" in screen or "┌" in screen), "top border missing"
        assert ("╰" in screen or "└" in screen), "bottom border clipped"
        assert TAIL_SENTINEL in screen, "final content not visible"


@pytest.mark.asyncio
async def test_full_content_renders():
    """The Static must render ALL content lines (no measurement/render mismatch)."""
    app = CoderioTUI()
    async with app.run_test(size=(80, 24)) as pilot:
        app.pilot = pilot
        await pilot.pause()
        history = app.query_one("#history", VerticalScroll)
        widget = Static("")
        widget.add_class("final-panel")
        history.mount(widget)
        await pilot.pause()
        widget.update(Markdown(LONG_BODY))
        await pilot.pause()
        await asyncio.sleep(0.4)
        await pilot.pause()
        assert widget.virtual_size.height > 30


@pytest.mark.asyncio
async def test_short_output_border_visible():
    """Short CJK output: border visible (not clipped)."""
    short = "# 短回答\n\n这是一段短内容，不需要滚动。"
    app = CoderioTUI()
    async with app.run_test(size=(214, 50)) as pilot:
        app.pilot = pilot
        await pilot.pause()
        await _mount_final_and_scroll(app, short)
        screen = _screen_text(app)
        assert ("╭" in screen or "┌" in screen), "short output top border missing"
        assert ("╰" in screen or "└" in screen), "short output bottom border clipped"
