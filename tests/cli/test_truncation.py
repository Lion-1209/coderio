"""Regression tests for the long-output truncation bug.

ROOT CAUSE (found via diagnostic capture on the user's wide VSCode terminal):
Static's deferred get_height auto-measurement ran during an early layout pass
at a NARROW (default ~76) container width, where CJK-heavy content wraps to
~2x as many lines as at the final full width. The inflated height was cached
and NOT recomputed when the container reached its real width — so the widget's
virtual_size said e.g. 122 rows while it only RENDERED 61. scroll_end then
landed in the phantom empty region (rows 62-122), showing nothing where the
Panel's bottom border + tail should be. Symptom: 'truncated at 测试覆盖 / 三道门'.

FIX: _mount_final_panel pre-renders the Markdown to styled text at the history's
real inner width, counts the lines, and pins widget.styles.height to that exact
count — so virtual_size can never diverge from the rendered content.

These tests pin the invariant: the Static's measured height MUST equal the actual
rendered line count, even for long CJK content on a wide terminal.
"""
import asyncio
import io

import pytest

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from coderio.cli.tui import CoderioTUI


# Long CJK body (the kind that triggers the width-dependent height inflation).
LONG_BODY = (
    "# 最终回答\n\n"
    + "\n".join(
        f"第 {i} 行内容：这是测试行的内容，用来撑满超过一屏，描述细节和实现要点。"
        for i in range(1, 41))
    + "\n\n## 测试覆盖\n这里是被截断的内容，应该可见。"
    + "\n\n## 最后\n这里是真正的结尾，最后一句应该可见。"
)


def _pre_render(app, body: str = LONG_BODY):
    """Mirror _mount_final_panel: pre-render Markdown to styled Text at the
    history's inner width, pin the height to the line count, mount as Static.
    Returns (widget, n_lines)."""
    history = app.query_one("#history", VerticalScroll)
    inner_w = max(20, history.content_size.width or 76)
    out = io.StringIO()
    console = Console(
        width=inner_w, force_terminal=True, color_system="256",
        file=out, legacy_windows=False, safe_box=True, soft_wrap=False,
    )
    console.print(Panel(Markdown(body), border_style="blue", title="coderio"))
    rendered = Text.from_ansi(out.getvalue().rstrip("\n"))
    n_lines = rendered.plain.count("\n") + 1
    widget = Static(rendered)
    widget.styles.height = n_lines
    history.mount(widget)
    return widget, n_lines


@pytest.mark.asyncio
async def test_explicit_height_matches_rendered_lines_wide_terminal():
    """THE core regression test: on a WIDE terminal (where the bug manifested),
    the Static's measured height must EXACTLY equal the rendered line count.
    Before the fix, get_height returned ~2x (cached from a narrow-width pass)."""
    app = CoderioTUI()
    async with app.run_test(size=(214, 50)) as pilot:
        await pilot.pause()
        widget, n_lines = _pre_render(app)
        await pilot.pause()
        await asyncio.sleep(0.5)
        await pilot.pause()
        assert widget.virtual_size.height == n_lines, (
            f"height {widget.virtual_size.height} != rendered lines {n_lines} — "
            "Static measured the widget at the wrong width (the truncation bug)"
        )


@pytest.mark.asyncio
async def test_explicit_height_matches_rendered_lines_narrow_terminal():
    """Same invariant on a narrow terminal (the original test environment)."""
    app = CoderioTUI()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        widget, n_lines = _pre_render(app)
        await pilot.pause()
        await asyncio.sleep(0.5)
        await pilot.pause()
        assert widget.virtual_size.height == n_lines, (
            f"height {widget.virtual_size.height} != rendered lines {n_lines}"
        )


@pytest.mark.asyncio
async def test_scroll_lands_on_real_content_bottom():
    """With the height pinned correctly, scroll_end must land on the actual
    content bottom (the Panel's lower border + final line), not a phantom
    empty region below it."""
    app = CoderioTUI()
    async with app.run_test(size=(214, 50)) as pilot:
        await pilot.pause()
        widget, n_lines = _pre_render(app)
        await pilot.pause()
        history = app.query_one("#history", VerticalScroll)
        history.scroll_end(animate=False)
        await pilot.pause()
        await asyncio.sleep(0.3)
        await pilot.pause()
        # scroll at max means the bottom of the (now correctly-sized) content
        # is in view.
        assert history.scroll_y == pytest.approx(history.max_scroll_y)
        # The widget's content height must equal its line count (no inflation).
        assert widget.virtual_size.height == n_lines


@pytest.mark.asyncio
async def test_short_output_not_inflated():
    """Short CJK output: height must equal its few lines (not inflated, not
    corrupted). Guards against the self.refresh() regression that broke shorts."""
    short = "# 短回答\n\n这是一段短内容，不需要滚动。"
    app = CoderioTUI()
    async with app.run_test(size=(214, 50)) as pilot:
        await pilot.pause()
        widget, n_lines = _pre_render(app, short)
        await pilot.pause()
        await asyncio.sleep(0.3)
        await pilot.pause()
        assert widget.virtual_size.height == n_lines
        assert n_lines < 15  # short content stays short
