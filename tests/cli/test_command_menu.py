"""Integration tests for the popup slash-command menu (Claude-Code-style).

Drives the REAL CoderioTUI via run_test. Verifies the menu pops on "/", filters
live, ↑↓ navigates, Tab/Enter accepts (and Enter does NOT submit while the menu
is open — that matches Claude Code's behavior), and Esc hides it.
"""
import pytest

from coderio.cli.tui import CoderioTUI, CommandMenu
from textual.widgets import Input


@pytest.mark.asyncio
async def test_menu_pops_on_slash():
    """Typing '/' shows the menu with all command candidates."""
    app = CoderioTUI()
    async with app.run_test() as pilot:
        inp = app.query_one("#msg", Input)
        inp.value = "/"
        await pilot.pause()
        menu = app.query_one(CommandMenu)
        assert menu.visible()
        items = list(menu.query("ListItem"))
        assert len(items) >= 10  # all slash commands present


@pytest.mark.asyncio
async def test_menu_filters_as_you_type():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        inp = app.query_one("#msg", Input)
        inp.value = "/re"
        await pilot.pause()
        menu = app.query_one(CommandMenu)
        assert menu.visible()
        names = [it.name for it in menu.query("ListItem")]
        assert "/resume " in names
        # commands that don't match '/re' are filtered out
        assert all("re" in n.lower() for n in names)


@pytest.mark.asyncio
async def test_menu_hides_without_slash():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        inp = app.query_one("#msg", Input)
        inp.value = "/"
        await pilot.pause()
        menu = app.query_one(CommandMenu)
        assert menu.visible()
        inp.value = "hello world"
        await pilot.pause()
        assert not menu.visible()


@pytest.mark.asyncio
async def test_tab_accepts_selected_command():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        inp = app.query_one("#msg", Input)
        inp.value = "/he"
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert inp.value == "/help"
        assert not app.query_one(CommandMenu).visible()


@pytest.mark.asyncio
async def test_enter_accepts_does_not_submit():
    """Enter while menu is open accepts the command but does NOT submit the input.
    A second Enter (menu now closed) submits. This is Claude Code's behavior."""
    app = CoderioTUI()
    submitted = []
    app._on_input = lambda line: submitted.append(line)
    async with app.run_test() as pilot:
        inp = app.query_one("#msg", Input)
        inp.value = "/he"
        await pilot.pause()
        await pilot.press("enter")  # accept from menu
        await pilot.pause()
        assert inp.value == "/help"
        assert submitted == []  # not submitted yet
        await pilot.press("enter")  # now submit
        await pilot.pause()
        assert submitted == ["/help"]


@pytest.mark.asyncio
async def test_arrow_keys_navigate():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        inp = app.query_one("#msg", Input)
        inp.value = "/"
        await pilot.pause()
        menu = app.query_one(CommandMenu)
        lv = menu.query_one("#cmd-list")
        first = lv.index
        await pilot.press("down")
        await pilot.pause()
        second = lv.index
        assert second != first  # selection moved


@pytest.mark.asyncio
async def test_escape_hides_menu():
    app = CoderioTUI()
    async with app.run_test() as pilot:
        inp = app.query_one("#msg", Input)
        inp.value = "/he"
        await pilot.pause()
        menu = app.query_one(CommandMenu)
        assert menu.visible()
        await pilot.press("escape")
        await pilot.pause()
        assert not menu.visible()
        # input value is preserved (Esc only closes the menu)
        assert inp.value == "/he"


@pytest.mark.asyncio
async def test_mode_shows_argument_forms():
    """/mode should surface its argument completions (confirm/plan/auto)."""
    app = CoderioTUI()
    async with app.run_test() as pilot:
        inp = app.query_one("#msg", Input)
        inp.value = "/mode"
        await pilot.pause()
        names = [it.name for it in app.query_one(CommandMenu).query("ListItem")]
        assert "/mode confirm" in names
        assert "/mode plan" in names
        assert "/mode auto" in names
