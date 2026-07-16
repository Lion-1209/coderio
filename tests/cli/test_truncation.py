"""Regression tests for the long-output truncation bug.

ROOT CAUSE: Static's height auto-measurement (get_height) runs during an early
layout pass at a NARROW (default ~76-col / unset) container width. CJK-heavy
content wraps to ~2x as many lines at 76 cols vs the final full width. The
inflated height is cached and NOT recomputed when the container reaches its real
width — virtual_size says e.g. 130 rows while only 65 render. scroll_end lands
in the phantom empty region, hiding the Panel's bottom border + tail.

FIX: _mount_final_panel mounts the Static EMPTY first; after one layout pass its
width has settled to the real container width; THEN Panel(Markdown) content is
set via update(), so height:auto measures at the correct width.

These tests pin the invariant: on a wide terminal, the Static's measured height
must approximately match the line count of the content rendered at that width
(within ±2, allowing for trailing-newline differences) — NOT 2x.
"""
import asyncio
import io

import pytest

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from textual.containers import VerticalScroll
from textual.widgets import Static

from coderio.cli.tui import CoderioTUI


# Long CJK body (the kind that triggers width-dependent height inflation).
LONG_BODY = (
    "# 最终回答\n\n"
    + "\n".join(
        f"第 {i} 行内容：这是测试行的内容，用来撑满超过一屏，描述细节和实现要点。"
        for i in range(1, 41))
    + "\n\n## 测试覆盖\n这里是被截断的内容，应该可见。"
    + "\n\n## 最后\n这里是真正的结尾，最后一句应该可见。"
)


def _expected_lines_at_width(body: str, width: int) -> int:
    """How many lines the Panel(Markdown(body)) renders to at `width`."""
    c = Console(width=width, force_terminal=False, file=io.StringIO(), record=True)
    c.print(Panel(Markdown(body), border_style="blue", title="coderio"))
    return len(c.export_text().rstrip("\n").split("\n"))


@pytest.mark.asyncio
async def test_height_matches_content_at_real_width_wide():
    """On a WIDE terminal (where the bug manifested), the Static's measured
    height must match the content rendered at that width — NOT 2x (the old
    inflated value from a narrow-width measurement)."""
    app = CoderioTUI()
    async with app.run_test(size=(214, 50)) as pilot:
        await pilot.pause()
        history = app.query_one("#history", VerticalScroll)
        # Mirror the fix: mount empty, settle, then set content.
        widget = Static("")
        history.mount(widget)
        await pilot.pause()  # width settles
        widget.update(Panel(Markdown(LONG_BODY), border_style="blue", title="coderio"))
        await pilot.pause()
        await asyncio.sleep(0.5)
        await pilot.pause()

        expected = _expected_lines_at_width(LONG_BODY, widget.size.width)
        vh = widget.virtual_size.height
        # Allow ±2 for trailing-newline / border rounding differences, but NOT 2x.
        assert abs(vh - expected) <= 2, (
            f"Static height {vh} != expected {expected} at width {widget.size.width} "
            f"— height was measured at the wrong (narrow) width, the truncation bug"
        )
        # Hard guard: must not be anywhere near 2x.
        assert vh < expected * 1.5, f"height {vh} is inflated vs {expected}"


@pytest.mark.asyncio
async def test_height_matches_content_at_real_width_narrow():
    """Same invariant on a narrow terminal."""
    app = CoderioTUI()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        history = app.query_one("#history", VerticalScroll)
        widget = Static("")
        history.mount(widget)
        await pilot.pause()
        widget.update(Panel(Markdown(LONG_BODY), border_style="blue", title="coderio"))
        await pilot.pause()
        await asyncio.sleep(0.5)
        await pilot.pause()
        expected = _expected_lines_at_width(LONG_BODY, widget.size.width)
        vh = widget.virtual_size.height
        assert abs(vh - expected) <= 2, (
            f"Static height {vh} != expected {expected} at width {widget.size.width}"
        )


@pytest.mark.asyncio
async def test_scroll_lands_on_real_content_bottom():
    """With correct height, scroll_end lands on the actual content bottom."""
    app = CoderioTUI()
    async with app.run_test(size=(214, 50)) as pilot:
        await pilot.pause()
        history = app.query_one("#history", VerticalScroll)
        widget = Static("")
        history.mount(widget)
        await pilot.pause()
        widget.update(Panel(Markdown(LONG_BODY), border_style="blue", title="coderio"))
        await pilot.pause()
        await asyncio.sleep(0.5)
        await pilot.pause()
        history.scroll_end(animate=False)
        await pilot.pause()
        await asyncio.sleep(0.3)
        await pilot.pause()
        assert history.scroll_y == pytest.approx(history.max_scroll_y)


@pytest.mark.asyncio
async def test_short_output_not_inflated():
    """Short CJK output: height must match its few lines (guards the
    self.refresh() regression that broke shorts)."""
    short = "# 短回答\n\n这是一段短内容，不需要滚动。"
    app = CoderioTUI()
    async with app.run_test(size=(214, 50)) as pilot:
        await pilot.pause()
        history = app.query_one("#history", VerticalScroll)
        widget = Static("")
        history.mount(widget)
        await pilot.pause()
        widget.update(Panel(Markdown(short), border_style="blue", title="coderio"))
        await pilot.pause()
        await asyncio.sleep(0.3)
        await pilot.pause()
        expected = _expected_lines_at_width(short, widget.size.width)
        assert abs(widget.virtual_size.height - expected) <= 2
        assert expected < 15  # short content stays short
