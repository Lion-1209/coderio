"""Regression tests for the long-output truncation bug.

ROOT CAUSE (final, confirmed via systematic bisect):
The `Screen { layers: base above }` CSS definition in CoderioTUI changed how
Textual computes the scrollable region's rendering. With layers active, the
bottom rows of content scrolled to the end stopped rendering (rendered as blank)
— the Panel's bottom border and the final content lines disappeared, looking
like truncation at ~95-99%. This was NOT a Panel/Markdown/height/Conpty issue.

FIX: removed the `Screen { layers }` definition. CommandMenu uses display:none
and dock:bottom for positioning (no layers needed). With layers gone, Static
(Panel(Markdown)) renders and scrolls correctly.

These tests verify: after scroll_end, BOTH the Panel's bottom border AND the
final content line are visible, on wide and narrow terminals, for long content.
"""

import asyncio

import pytest

from rich.markdown import Markdown
from rich.panel import Panel
from textual.geometry import Size
from textual.containers import VerticalScroll
from textual.widgets import Static

from coderio.cli.tui import CoderioTUI


LONG_BODY = (
    "# 最终回答\n\n"
    + "\n".join(
        f"第 {i} 行内容：这是测试行的内容，用来撑满超过一屏，描述细节和实现要点。"
        for i in range(1, 41)
    )
    + "\n\n## 测试覆盖\n这里是被截断的内容，应该可见。"
    + "\n\n## 最后\n这里是真正的结尾，最后一句应该可见。"
)
TAIL_SENTINEL = "这里是真正的结尾"


async def _mount_panel_and_scroll(app, body: str = LONG_BODY):
    """Mount Static(Panel(Markdown)) and scroll to bottom."""
    history = app.query_one("#history", VerticalScroll)
    widget = Static(Panel(Markdown(body), border_style="blue", title="coderio"))
    history.mount(widget)
    await app.pilot.pause()
    await asyncio.sleep(0.5)
    await app.pilot.pause()
    history.scroll_end(animate=False)
    await app.pilot.pause()
    await asyncio.sleep(0.3)
    await app.pilot.pause()


def _screen_text(app) -> str:
    strips = app.screen._compositor.render_strips(Size(app.size.width, app.size.height))
    return "".join(seg.text for strip in strips for seg in strip)


@pytest.mark.asyncio
async def test_bottom_border_and_tail_visible_wide():
    """WIDE terminal: after scroll, border + final line visible."""
    app = CoderioTUI()
    async with app.run_test(size=(214, 50)) as pilot:
        app.pilot = pilot
        await pilot.pause()
        await _mount_panel_and_scroll(app)
        screen = _screen_text(app)
        assert "╰" in screen or "└" in screen, "bottom border clipped"
        assert TAIL_SENTINEL in screen, "final content not visible"


@pytest.mark.asyncio
async def test_bottom_border_and_tail_visible_narrow():
    """NARROW terminal (where the bug was most severe)."""
    app = CoderioTUI()
    async with app.run_test(size=(80, 24)) as pilot:
        app.pilot = pilot
        await pilot.pause()
        await _mount_panel_and_scroll(app)
        screen = _screen_text(app)
        assert "╰" in screen or "└" in screen, "bottom border clipped"
        assert TAIL_SENTINEL in screen, "final content not visible"


@pytest.mark.asyncio
async def test_no_layers_in_css():
    """The Screen must NOT define layers (the root cause). This guard prevents
    re-introducing the layers CSS that breaks scrolled-content rendering."""
    # The CSS string should not contain a layers definition for Screen.
    assert "layers: base above" not in CoderioTUI.CSS, (
        "Screen layers definition re-introduced — this causes the truncation bug"
    )


@pytest.mark.asyncio
async def test_short_output_border_visible():
    """Short output: border visible (not clipped)."""
    short = "# 短回答\n\n这是一段短内容。"
    app = CoderioTUI()
    async with app.run_test(size=(214, 50)) as pilot:
        app.pilot = pilot
        await pilot.pause()
        await _mount_panel_and_scroll(app, short)
        screen = _screen_text(app)
        assert "╰" in screen or "└" in screen, "short output border clipped"


@pytest.mark.asyncio
async def test_streaming_output_appends_and_scrolls():
    """Streaming text (as on_token does) should append deltas to a RichLog and
    keep the last line visible after scroll. Mirrors on_token: accumulate into
    a buffer, push the FULL buffer each time (the delta is extracted internally)."""
    app = CoderioTUI()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        buffer = ""
        for i in range(50):
            buffer += f"line {i}\n"
            app._render_q.append(("text", buffer))
            await asyncio.sleep(0.07)  # just past the 60ms drain interval
            await pilot.pause()
        await pilot.pause()
        await asyncio.sleep(0.2)
        await pilot.pause()
        screen = _screen_text(app)
        assert "line 49" in screen, (
            f"streaming output didn't keep up — last line not visible"
        )
        assert app._live_out_widget is not None
