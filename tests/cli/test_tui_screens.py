"""Basic tests for the three TUI picker screens (ProfilePickerScreen,
ModePickerScreen, SessionPickerScreen).

These are ModalScreen subclasses from tui_screens.py. They must:
  - construct without arguments beyond their data,
  - mount inside a real Textual app via run_test,
  - populate their ListView from the supplied data,
  - navigate with arrow keys,
  - dismiss with the chosen value (Enter) or None (Esc).

Driven through Textual's run_test pilot (the only way to exercise mount/compose/
on_mount for a ModalScreen). The screens are pushed onto a minimal host App so
the test does not depend on the full CoderioTUI widget tree.
"""

from __future__ import annotations

from typing import Any

import pytest
from textual.app import App, ComposeResult
from textual.widgets import ListView, Static

from coderio.cli.tui_screens import (
    ModePickerScreen,
    ProfilePickerScreen,
    SessionPickerScreen,
)
from coderio.config.models import Profile

# ----------------------------------------------------------------------- fixtures
SESSION_SUMMARIES = [
    {
        "id": "20260803-093941-aaaa",
        "first_user": "帮我修登录bug",
        "message_count": 5,
        "model": "glm-5.2",
        "mtime": "2026-08-03 09:39",
    },
    {
        "id": "20260802-164237-bbbb",
        "first_user": "分析项目架构",
        "message_count": 12,
        "model": "glm-5.2",
        "mtime": "2026-08-02 16:42",
    },
    {
        "id": "20260801-100000-cccc",
        "first_user": "(空会话)",
        "message_count": 0,
        "model": "",
        "mtime": "2026-08-01 10:00",
    },
]

PROFILES = [
    Profile(name="default", provider_id="openai_compatible", model="glm-4.5"),
    Profile(name="claude", provider_id="anthropic", model="claude-3.5-sonnet"),
]


# ---------------------------------------------------------- minimal host app
class _HostApp(App):
    """Bare App used only to mount a ModalScreen under run_test.

    CoderioTUI pulls in slash_completions, the agent worker, etc. — none of that
    is needed to test the screens themselves, and avoiding it keeps these tests
    fast and decoupled from agent-side changes. push_screen(screen) with a
    result callback captures the dismiss value.
    """

    def __init__(self, screen: Any, on_result: Any) -> None:
        super().__init__()
        self._screen = screen
        self._on_result = on_result

    def compose(self) -> ComposeResult:
        yield Static("host")

    def on_mount(self) -> None:
        self.push_screen(self._screen, self._on_result)


# ============================================================ SessionPickerScreen
class TestSessionPickerScreen:
    @pytest.mark.asyncio
    async def test_mounts_and_populates_list(self) -> None:
        """The picker must mount and render one row per supplied session."""
        app = _HostApp(SessionPickerScreen(SESSION_SUMMARIES), lambda v: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            lv = app.screen.query_one("#picker-list", ListView)
            assert len(lv.children) == len(SESSION_SUMMARIES)

    @pytest.mark.asyncio
    async def test_shows_first_user_messages(self) -> None:
        """Each row must surface the first-user text so the user recognizes
        conversations by what they asked (not opaque ids)."""
        app = _HostApp(SessionPickerScreen(SESSION_SUMMARIES), lambda v: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            texts = [
                str(w.content) for w in app.screen.walk_children(Static) if getattr(w, "content", None) is not None
            ]
            joined = " ".join(texts)
            assert "帮我修登录bug" in joined
            assert "分析项目架构" in joined

    @pytest.mark.asyncio
    async def test_default_selection_is_first_row(self) -> None:
        """on_mount sets lv.index = 0 so Enter is immediately meaningful."""
        app = _HostApp(SessionPickerScreen(SESSION_SUMMARIES), lambda v: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            lv = app.screen.query_one("#picker-list", ListView)
            assert lv.index == 0

    @pytest.mark.asyncio
    async def test_arrow_keys_navigate(self) -> None:
        """Down moves the highlight to the second row; Up wraps back."""
        app = _HostApp(SessionPickerScreen(SESSION_SUMMARIES), lambda v: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            lv = app.screen.query_one("#picker-list", ListView)
            assert lv.index == 0
            await pilot.press("down")
            await pilot.pause()
            assert lv.index == 1
            await pilot.press("up")
            await pilot.pause()
            assert lv.index == 0

    @pytest.mark.asyncio
    async def test_enter_dismisses_with_session_id(self) -> None:
        """Enter on the highlighted row dismisses with that session's id."""
        result: dict = {}

        def _on_pick(sid: str | None) -> None:
            result["sid"] = sid

        app = _HostApp(SessionPickerScreen(SESSION_SUMMARIES), _on_pick)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
        assert result.get("sid") == SESSION_SUMMARIES[0]["id"]

    @pytest.mark.asyncio
    async def test_escape_dismisses_none(self) -> None:
        """Esc cancels and dismisses with None."""
        result: dict = {}

        def _on_pick(sid: str | None) -> None:
            result["sid"] = sid

        app = _HostApp(SessionPickerScreen(SESSION_SUMMARIES), _on_pick)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
        assert result.get("sid") is None

    @pytest.mark.asyncio
    async def test_filter_narrows_list(self) -> None:
        """Setting the filter input rebuilds the list with only matching rows."""
        app = _HostApp(SessionPickerScreen(SESSION_SUMMARIES), lambda v: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            inp = app.screen.query_one("#picker-filter")
            inp.value = "登录"
            await pilot.pause()
            lv = app.screen.query_one("#picker-list", ListView)
            # Only the first session matches '登录'.
            assert len(lv.children) == 1

    @pytest.mark.asyncio
    async def test_populate_handles_empty_summaries(self) -> None:
        """An empty summaries list must not crash on_mount/_populate."""
        app = _HostApp(SessionPickerScreen([]), lambda v: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            lv = app.screen.query_one("#picker-list", ListView)
            assert len(lv.children) == 0


# ============================================================ ProfilePickerScreen
class TestProfilePickerScreen:
    @pytest.mark.asyncio
    async def test_mounts_and_populates(self) -> None:
        """One row per profile, with the provider/model subtitle."""
        app = _HostApp(ProfilePickerScreen(PROFILES), lambda v: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            lv = app.screen.query_one("#profile-list", ListView)
            assert len(lv.children) == len(PROFILES)

    @pytest.mark.asyncio
    async def test_active_profile_marked_with_star(self) -> None:
        """The active profile row starts with ★ (the others get two spaces)."""
        app = _HostApp(ProfilePickerScreen(PROFILES, active_name="claude"), lambda v: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            texts = [
                str(w.content) for w in app.screen.walk_children(Static) if getattr(w, "content", None) is not None
            ]
            joined = " ".join(texts)
            # The claude row is starred; the default row is not.
            assert "★ claude" in joined
            assert "default" in joined

    @pytest.mark.asyncio
    async def test_enter_dismisses_with_profile_name(self) -> None:
        result: dict = {}

        def _on_pick(name: str | None) -> None:
            result["name"] = name

        app = _HostApp(ProfilePickerScreen(PROFILES), _on_pick)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
        assert result.get("name") == "default"

    @pytest.mark.asyncio
    async def test_escape_dismisses_none(self) -> None:
        result: dict = {}

        def _on_pick(name: str | None) -> None:
            result["name"] = name

        app = _HostApp(ProfilePickerScreen(PROFILES), _on_pick)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
        assert result.get("name") is None


# ============================================================ ModePickerScreen
class TestModePickerScreen:
    @pytest.mark.asyncio
    async def test_mounts_and_populates_all_modes(self) -> None:
        """All four permission modes are listed."""
        app = _HostApp(ModePickerScreen(), lambda v: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            lv = app.screen.query_one("#mode-list", ListView)
            assert len(lv.children) == 4
            names = [item.name for item in lv.children]
            assert set(names) == {"plan", "confirm", "auto_edit", "full"}

    @pytest.mark.asyncio
    async def test_active_mode_marked_with_star(self) -> None:
        app = _HostApp(ModePickerScreen(active_mode="confirm"), lambda v: None)
        async with app.run_test() as pilot:
            await pilot.pause()
            texts = [
                str(w.content) for w in app.screen.walk_children(Static) if getattr(w, "content", None) is not None
            ]
            joined = " ".join(texts)
            assert "★ confirm" in joined

    @pytest.mark.asyncio
    async def test_enter_dismisses_with_mode_name(self) -> None:
        result: dict = {}

        def _on_pick(mode: str | None) -> None:
            result["mode"] = mode

        app = _HostApp(ModePickerScreen(), _on_pick)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("down")  # second row = confirm
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
        assert result.get("mode") == "confirm"

    @pytest.mark.asyncio
    async def test_escape_dismisses_none(self) -> None:
        result: dict = {}

        def _on_pick(mode: str | None) -> None:
            result["mode"] = mode

        app = _HostApp(ModePickerScreen(), _on_pick)
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
        assert result.get("mode") is None
