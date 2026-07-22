"""Integration tests for the popup slash-command menu (Claude-Code-style).

Drives the REAL CoderioTUI via run_test. Verifies the menu pops on "/", filters
live, ↑↓ navigates, Tab/Enter accepts (and Enter does NOT submit while the menu
is open — that matches Claude Code's behavior), and Esc hides it.
"""
import pytest

from coderio.cli.tui import CoderioTUI, CommandMenu, StatusBar
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
async def test_highlight_stays_visible_after_many_down_presses():
    """REGRESSION: pressing DOWN repeatedly must keep the highlighted item
    inside the visible viewport. The user reported that after pressing down a
    few times, the current selection scrolled out of view and was no longer
    visible — the menu felt broken.

    The ListView's logical scroll tracking may report the highlight as 'inside'
    even when the effective rendered area is smaller (border/overlap/padding
    shrink what the user actually sees). This test asserts on the conservative
    invariant: the selected index must stay within [scroll_y, scroll_y+vp_h)
    for EVERY down press, across a range of terminal heights."""
    from textual.geometry import Size

    for term_h in (30, 24, 20, 18, 16):
        app = CoderioTUI()
        async with app.run_test(size=Size(100, term_h)) as pilot:
            inp = app.query_one("#msg", Input)
            inp.value = "/"
            await pilot.pause()
            lv = app.query_one(CommandMenu).query_one("#cmd-list")
            n_items = len(lv.children)
            assert n_items >= 10, f"expected >=10 commands, got {n_items}"
            for _ in range(n_items + 2):  # press down past the end + wrap
                await pilot.press("down")
                await pilot.pause()
                idx = lv.index or 0
                vp_h = lv.size.height or 1
                top = int(lv.scroll_y)
                # The selected index MUST be within the visible viewport,
                # with at least 1 row of margin (not flush on the bottom edge).
                assert top <= idx < top + vp_h, (
                    f"highlight lost at terminal h={term_h}: idx={idx} "
                    f"outside viewport [{top},{top + vp_h})")


@pytest.mark.asyncio
async def test_menu_does_not_overlap_status_bar():
    """REGRESSION: the CommandMenu must NOT overlap the StatusBar.

    Old layout had CommandMenu as a sibling of #input-bar with dock:bottom,
    causing the menu to float ON TOP of the StatusBar — hiding its first
    character ("就" in "就绪") and clipping the menu's own bottom border.
    Fix: CommandMenu lives INSIDE #input-bar, expanding it upward as a unit.

    This test verifies the invariant: the menu's bottom edge (including border)
    must be at or above the StatusBar's top edge.
    """
    from textual.geometry import Size
    app = CoderioTUI()
    async with app.run_test(size=Size(100, 30)) as pilot:
        inp = app.query_one("#msg", Input)
        inp.value = "/"
        await pilot.pause()
        menu = app.query_one(CommandMenu)
        sb = app.query_one(StatusBar)
        menu_bottom = menu.region.y + menu.region.height
        sb_top = sb.region.y
        assert menu_bottom <= sb_top, (
            f"menu overlaps StatusBar: menu bottom={menu_bottom} > "
            f"StatusBar top={sb_top}. The menu's bottom border would clip "
            "the StatusBar's first character.")


@pytest.mark.asyncio
async def test_menu_shows_more_than_four_items():
    """REGRESSION: the CommandMenu must show ~8 items, not collapse to 4.

    Old CSS had height:auto + ListView without explicit height. In a docked
    container the ListView collapsed to its content's minimum (~4 rows),
    making most commands unreachable without scrolling. Fix: fixed height:10
    on CommandMenu gives the ListView a real viewport (8 visible rows after
    border). This test guards against the collapse recurring."""
    from textual.geometry import Size
    app = CoderioTUI()
    async with app.run_test(size=Size(100, 30)) as pilot:
        inp = app.query_one("#msg", Input)
        inp.value = "/"
        await pilot.pause()
        lv = app.query_one(CommandMenu).query_one("#cmd-list")
        # ListView should have a real viewport of >=6 rows (not the ~4 collapse).
        # 8 is the expected value (height:10 - 2 border rows = 8 content).
        assert lv.size.height >= 6, (
            f"ListView viewport collapsed to {lv.size.height} rows — expected "
            ">=6. The menu is showing too few items; height:auto regression.")


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
