"""Integration tests for the /resume session picker (Textual app level).

These drive the REAL CoderioTUI via Textual's run_test pilot — the only way to
catch regressions like the 'no current event loop in thread' crash that happened
when push_screen was called from the agent's background thread.
"""
import pytest

from coderio.cli.tui import SessionPickerScreen


SUMMARIES = [
    {"id": "20260703-093941-b9f7", "first_user": "帮我修登录bug",
     "message_count": 5, "model": "glm-5.2", "mtime": "2026-07-03 09:39"},
    {"id": "20260702-164237-4xzk", "first_user": "分析项目架构",
     "message_count": 12, "model": "glm-5.2", "mtime": "2026-07-02 16:42"},
]


@pytest.mark.asyncio
async def test_picker_mounts_and_shows_summaries():
    """The picker must mount and render each session's first-user summary (so the
    user recognizes conversations by what they asked, not by opaque ids)."""
    from coderio.cli.tui import CoderioTUI
    from textual.widgets import Static
    app = CoderioTUI()
    async with app.run_test() as pilot:
        app.push_screen(SessionPickerScreen(SUMMARIES))
        await pilot.pause()
        texts = [str(w.content) for w in app.screen.walk_children(Static)
                 if getattr(w, "content", None) is not None]
        joined = " ".join(texts)
        assert "帮我修登录bug" in joined
        assert "分析项目架构" in joined


@pytest.mark.asyncio
async def test_picker_cancel_returns_none():
    """Esc dismisses the picker with None (no session selected)."""
    from coderio.cli.tui import CoderioTUI
    app = CoderioTUI()
    result = {}
    def _on_pick(sid):
        result["sid"] = sid
    async with app.run_test() as pilot:
        app.push_screen(SessionPickerScreen(SUMMARIES), _on_pick)
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
    assert result.get("sid") is None


@pytest.mark.asyncio
async def test_picker_filter_narrows_list():
    """Setting the filter input hides non-matching rows."""
    from coderio.cli.tui import CoderioTUI
    from textual.widgets import Input, Static
    app = CoderioTUI()
    async with app.run_test() as pilot:
        app.push_screen(SessionPickerScreen(SUMMARIES))
        await pilot.pause()
        # Set the filter directly (pilot.press with CJK chars doesn't reliably
        # register as an Input.Changed event). A keyword matching only session 1.
        app.screen.query_one("#picker-filter", Input).value = "登录"
        await pilot.pause()
        texts = [str(w.content) for w in app.screen.walk_children(Static)
                 if getattr(w, "content", None) is not None]
        joined = " ".join(texts)
        assert "帮我修登录bug" in joined
        assert "分析项目架构" not in joined
