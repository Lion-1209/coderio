"""Basic tests for the three TUI widgets (CommandMenu, ConfirmMenu, StatusBar).

These are self-contained widgets from tui_widgets.py with no top-level coupling
to the rest of coderio. They're driven through Textual's run_test pilot by
mounting them in a minimal host App (not the full CoderioTUI), which keeps the
tests fast and decoupled from agent-side changes.

CommandMenu: create, bind_input, refresh_for (show/hide + filtering).
ConfirmMenu: show, move (up/down navigation + wrap), accept (returns choice),
             visible, hide.
StatusBar:   create, set_phase (writes attributes), render idle state.
"""

from __future__ import annotations

from typing import Any

import pytest
from textual.app import App, ComposeResult
from textual.containers import Container
from textual.widgets import Button, Input, ListView

from coderio.cli.tui import CoderioTUI
from coderio.cli.tui_widgets import CommandMenu, ConfirmMenu, StatusBar

# A representative set of slash completions for CommandMenu.
COMPLETIONS = [
    "/help",
    "/resume",
    "/reset",
    "/profile",
    "/mode confirm",
    "/mode plan",
    "/mode auto",
    "/clear",
    "/skills",
    "/think",
]


# ---------------------------------------------------------- minimal host app
def _host_app(widget: Any, extra: list[Any] | None = None) -> App:
    """Build a bare App that mounts `widget` (plus any extra siblings).

    Wrapping the widgets in a Container avoids layout issues with mounting a
    Vertical/Widget directly as the screen root. The widget is queryable by
    type after mount.
    """

    class _Host(App):
        def __init__(self) -> None:
            super().__init__()
            self._widget = widget
            self._extra = extra or []

        def compose(self) -> ComposeResult:
            with Container():
                yield self._widget
                yield from self._extra

    return _Host()


# ============================================================ CommandMenu
class TestCommandMenu:
    @pytest.mark.asyncio
    async def test_creates_hidden(self) -> None:
        """A freshly created CommandMenu is not visible (no "/" typed yet)."""
        menu = CommandMenu(COMPLETIONS)
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not menu.visible()

    @pytest.mark.asyncio
    async def test_bind_input_stores_reference(self) -> None:
        """bind_input wires the menu to an Input so accept() can fill it."""
        menu = CommandMenu(COMPLETIONS)
        inp = Input(id="msg")
        app = _host_app(menu, [inp])
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.bind_input(inp)
            assert menu._input is inp

    @pytest.mark.asyncio
    async def test_refresh_for_slash_shows_menu(self) -> None:
        """A value starting with "/" shows the menu with all matches."""
        menu = CommandMenu(COMPLETIONS)
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.refresh_for("/")
            await pilot.pause()
            assert menu.visible()
            lv = menu.query_one("#cmd-list", ListView)
            assert len(lv.children) == len(COMPLETIONS)

    @pytest.mark.asyncio
    async def test_refresh_for_prefix_filters(self) -> None:
        """Typing more than "/" narrows to matching commands (exact prefix first)."""
        menu = CommandMenu(COMPLETIONS)
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.refresh_for("/mode")
            await pilot.pause()
            assert menu.visible()
            lv = menu.query_one("#cmd-list", ListView)
            names = [item.name for item in lv.children]
            assert "/mode confirm" in names
            assert "/mode plan" in names
            assert "/mode auto" in names
            # Non-matching commands are filtered out.
            assert "/help" not in names
            assert "/resume" not in names

    @pytest.mark.asyncio
    async def test_refresh_for_non_slash_hides(self) -> None:
        """A value without a leading "/" hides the menu."""
        menu = CommandMenu(COMPLETIONS)
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.refresh_for("/")
            await pilot.pause()
            assert menu.visible()
            menu.refresh_for("hello world")
            await pilot.pause()
            assert not menu.visible()

    @pytest.mark.asyncio
    async def test_refresh_for_no_matches_hides(self) -> None:
        """A "/" prefix that matches nothing hides the menu."""
        menu = CommandMenu(COMPLETIONS)
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.refresh_for("/zzzz-no-such-command")
            await pilot.pause()
            assert not menu.visible()

    @pytest.mark.asyncio
    async def test_refresh_for_auto_selects_first_match(self) -> None:
        """After filtering, the first row is selected so Enter is meaningful."""
        menu = CommandMenu(COMPLETIONS)
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.refresh_for("/mode")
            await pilot.pause()
            lv = menu.query_one("#cmd-list", ListView)
            assert lv.index == 0

    @pytest.mark.asyncio
    async def test_move_navigates_and_wraps(self) -> None:
        """move() advances the selection and wraps around at the ends.

        Wrap math is (idx + delta) % n: from index 0, move(-1) → (0-1) % n = n-1."""
        menu = CommandMenu(COMPLETIONS)
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.refresh_for("/")
            await pilot.pause()
            lv = menu.query_one("#cmd-list", ListView)
            n = len(lv.children)
            assert lv.index == 0
            menu.move(1)  # down → index 1
            assert lv.index == 1
            menu.move(-1)  # up from index 1 → index 0 (no wrap yet)
            assert lv.index == 0
            menu.move(-1)  # up from index 0 → wraps to last
            assert lv.index == n - 1
            menu.move(1)  # down from last → wraps to first
            assert lv.index == 0

    @pytest.mark.asyncio
    async def test_move_noop_when_hidden(self) -> None:
        """move() must be a no-op when the menu is not visible."""
        menu = CommandMenu(COMPLETIONS)
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            # menu hidden, no ListView index to corrupt
            menu.move(1)
            menu.move(-1)
            assert not menu.visible()

    @pytest.mark.asyncio
    async def test_accept_fills_bound_input(self) -> None:
        """accept() writes the selected command into the bound Input and hides."""
        menu = CommandMenu(COMPLETIONS)
        inp = Input(id="msg")
        app = _host_app(menu, [inp])
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.bind_input(inp)
            menu.refresh_for("/he")
            await pilot.pause()
            accepted = menu.accept()
            assert accepted is True
            assert inp.value == "/help"
            assert not menu.visible()

    @pytest.mark.asyncio
    async def test_accept_returns_false_when_hidden(self) -> None:
        """accept() on a hidden menu returns False without touching the Input.

        The Input's value is set INSIDE the app context — setting it at construction
        time triggers a reactive watcher that needs an active app."""
        menu = CommandMenu(COMPLETIONS)
        inp = Input(id="msg")
        app = _host_app(menu, [inp])
        async with app.run_test() as pilot:
            await pilot.pause()
            inp.value = "keep me"
            menu.bind_input(inp)
            accepted = menu.accept()
            assert accepted is False
            assert inp.value == "keep me"

    @pytest.mark.asyncio
    async def test_accept_returns_false_without_bound_input(self) -> None:
        """accept() returns False if no Input was ever bound."""
        menu = CommandMenu(COMPLETIONS)
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.refresh_for("/")
            await pilot.pause()
            # No bind_input call — _input is None.
            assert menu.accept() is False

    @pytest.mark.asyncio
    async def test_hide_clears_visible_class(self) -> None:
        menu = CommandMenu(COMPLETIONS)
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.refresh_for("/")
            await pilot.pause()
            assert menu.visible()
            menu.hide()
            assert not menu.visible()

    @pytest.mark.asyncio
    async def test_accepted_value_guard_prevents_reopen(self) -> None:
        """After accept(), refresh_for with the same value must NOT reopen the
        menu (the value was set programmatically, not typed)."""
        menu = CommandMenu(COMPLETIONS)
        inp = Input(id="msg")
        app = _host_app(menu, [inp])
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.bind_input(inp)
            menu.refresh_for("/he")
            await pilot.pause()
            menu.accept()
            assert not menu.visible()
            # The Input now holds "/help" — refresh_for must skip reopening.
            menu.refresh_for("/help")
            await pilot.pause()
            assert not menu.visible()


# ============================================================ ConfirmMenu
class TestConfirmMenu:
    @pytest.mark.asyncio
    async def test_creates_hidden(self) -> None:
        menu = ConfirmMenu()
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert not menu.visible()

    @pytest.mark.asyncio
    async def test_show_makes_visible_and_populates(self) -> None:
        menu = ConfirmMenu()
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.show("bash", "command='rm -rf /'")
            await pilot.pause()
            assert menu.visible()
            lv = menu.query_one("#confirm-list", ListView)
            assert len(lv.children) == 3
            names = [item.name for item in lv.children]
            assert names == [ConfirmMenu.ALLOW, ConfirmMenu.DENY, ConfirmMenu.CUSTOM]

    @pytest.mark.asyncio
    async def test_show_defaults_to_allow(self) -> None:
        """show() pre-selects the first option (allow) so Enter permits by default."""
        menu = ConfirmMenu()
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.show("write_file", "path=x.py")
            await pilot.pause()
            lv = menu.query_one("#confirm-list", ListView)
            assert lv.index == 0

    @pytest.mark.asyncio
    async def test_move_navigates_down_and_wraps(self) -> None:
        menu = ConfirmMenu()
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.show("bash", "command='ls'")
            await pilot.pause()
            lv = menu.query_one("#confirm-list", ListView)
            assert lv.index == 0
            menu.move(1)  # → deny
            assert lv.index == 1
            menu.move(1)  # → custom
            assert lv.index == 2
            menu.move(1)  # wrap → allow
            assert lv.index == 0

    @pytest.mark.asyncio
    async def test_move_up_wraps_to_last(self) -> None:
        menu = ConfirmMenu()
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.show("bash", "command='ls'")
            await pilot.pause()
            lv = menu.query_one("#confirm-list", ListView)
            assert lv.index == 0
            menu.move(-1)  # up from first → wrap to last (custom)
            assert lv.index == 2

    @pytest.mark.asyncio
    async def test_move_noop_when_hidden(self) -> None:
        menu = ConfirmMenu()
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.move(1)
            menu.move(-1)
            assert not menu.visible()

    @pytest.mark.asyncio
    async def test_accept_returns_selected_choice(self) -> None:
        menu = ConfirmMenu()
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.show("bash", "command='ls'")
            await pilot.pause()
            menu.move(1)  # highlight deny
            assert menu.accept() == ConfirmMenu.DENY

    @pytest.mark.asyncio
    async def test_accept_returns_allow_by_default(self) -> None:
        menu = ConfirmMenu()
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.show("bash", "command='ls'")
            await pilot.pause()
            assert menu.accept() == ConfirmMenu.ALLOW

    @pytest.mark.asyncio
    async def test_accept_returns_none_when_hidden(self) -> None:
        menu = ConfirmMenu()
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert menu.accept() is None

    @pytest.mark.asyncio
    async def test_hide_clears_visibility_and_list(self) -> None:
        menu = ConfirmMenu()
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.show("bash", "command='ls'")
            await pilot.pause()
            assert menu.visible()
            menu.hide()
            await pilot.pause()
            assert not menu.visible()
            lv = menu.query_one("#confirm-list", ListView)
            assert len(lv.children) == 0

    @pytest.mark.asyncio
    async def test_visible_reflects_state(self) -> None:
        """visible() is the single source of truth for show/hide."""
        menu = ConfirmMenu()
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert menu.visible() is False
            menu.show("bash", "command='ls'")
            await pilot.pause()
            assert menu.visible() is True
            menu.hide()
            await pilot.pause()
            assert menu.visible() is False


# ============================================================ StatusBar
class TestStatusBar:
    @pytest.mark.asyncio
    async def test_creates_idle(self) -> None:
        """A fresh StatusBar defaults to the idle phase."""
        bar = StatusBar()
        app = _host_app(bar)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert bar.phase == "idle"

    @pytest.mark.asyncio
    async def test_render_idle_shows_ready_label(self) -> None:
        """render() in idle state produces the '(就绪)' label."""
        bar = StatusBar()
        app = _host_app(bar)
        async with app.run_test() as pilot:
            await pilot.pause()
            rendered = bar.render()
            text = str(rendered.plain if hasattr(rendered, "plain") else rendered)
            assert "就绪" in text

    @pytest.mark.asyncio
    async def test_set_phase_updates_attributes(self) -> None:
        """set_phase writes the plain attributes read by render() (GIL-safe)."""
        bar = StatusBar()
        app = _host_app(bar)
        async with app.run_test() as pilot:
            await pilot.pause()
            bar.set_phase("thinking", step=2)
            assert bar.phase == "thinking"
            assert bar.step == 2
            assert bar.phase_start > 0.0  # non-idle arms the timer

    @pytest.mark.asyncio
    async def test_set_phase_idle_resets_timer(self) -> None:
        """Returning to idle zeroes phase_start (timer stops accumulating)."""
        bar = StatusBar()
        app = _host_app(bar)
        async with app.run_test() as pilot:
            await pilot.pause()
            bar.set_phase("thinking", step=1)
            assert bar.phase_start > 0.0
            bar.set_phase("idle")
            assert bar.phase == "idle"
            assert bar.phase_start == 0.0

    @pytest.mark.asyncio
    async def test_set_phase_records_tool_batch_info(self) -> None:
        """set_phase with tool_total records tool_index/tool_total for the
        'read_file(2/3)' style display."""
        bar = StatusBar()
        app = _host_app(bar)
        async with app.run_test() as pilot:
            await pilot.pause()
            bar.set_phase("tool", tool_name="read_file", step=1, tool_index=1, tool_total=3)
            assert bar.phase == "tool"
            assert bar.tool_name == "read_file"
            assert bar.tool_index == 1
            assert bar.tool_total == 3

    @pytest.mark.asyncio
    async def test_render_thinking_shows_label(self) -> None:
        bar = StatusBar()
        app = _host_app(bar)
        async with app.run_test() as pilot:
            await pilot.pause()
            bar.set_phase("thinking", step=1)
            await pilot.pause()
            rendered = bar.render()
            text = str(rendered.plain if hasattr(rendered, "plain") else rendered)
            assert "思考" in text
            assert "步骤1" in text


# ============================================================ descriptions column


class TestCommandMenuDescriptions:
    """2026-08-27 live TUI audit: the autocomplete menu listed bare command
    names — no hint what a command does before selecting it. Rows now carry a
    dim description; repeated subcommand forms suppress the parent's text."""

    @pytest.mark.asyncio
    async def test_description_shown_for_bare_command(self) -> None:
        menu = CommandMenu(["/help", "/undo"], {"/help": "show this help", "/undo": "revert last write"})
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.refresh_for("/h")
            await pilot.pause()
            lv = menu.query_one("#cmd-list", ListView)
            label = str(lv.children[0].children[0].render())
            assert "show this help" in label, "bare command row must show its description"

    @pytest.mark.asyncio
    async def test_subcommand_form_suppresses_duplicate_description(self) -> None:
        descs = {"/mode": "change permission mode", "/mode plan": "change permission mode"}
        menu = CommandMenu(["/mode", "/mode plan"], descs)
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.refresh_for("/")
            await pilot.pause()
            lv = menu.query_one("#cmd-list", ListView)
            labels = [str(item.children[0].render()) for item in lv.children]
            bare_row = next(x for x in labels if x == "/mode" or x.startswith("/mode  "))
            sub_row = next(x for x in labels if x.startswith("/mode plan"))
            assert "permission mode" in bare_row, "bare /mode keeps its description"
            assert "permission mode" not in sub_row, "'/mode plan' must not repeat the parent text"

    @pytest.mark.asyncio
    async def test_missing_description_falls_back_to_bare_name(self) -> None:
        menu = CommandMenu(["/zzz"], {})
        app = _host_app(menu)
        async with app.run_test() as pilot:
            await pilot.pause()
            menu.refresh_for("/")
            await pilot.pause()
            lv = menu.query_one("#cmd-list", ListView)
            label = str(lv.children[0].children[0].render())
            assert label.strip() == "/zzz"


# ============================================================ pause button


class TestSendStopButton:
    """ONE morphing control (zcode pattern — one concept, one button):
    idle ➤ submits, running ⏹ interrupts (click dispatches action_interrupt,
    same as Esc). No separate pause concept."""

    @pytest.mark.asyncio
    async def test_send_slot_morphs_through_turn_lifecycle(self) -> None:
        app = CoderioTUI()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            send = app.query_one("#send-btn", Button)
            assert str(send.label).startswith("➤"), "idle send slot shows submit"
            assert "running" not in send.classes
            assert send.region.width > 0, "send button must always occupy layout space"

            # Simulate a running turn (what on_input_submitted's worker does).
            app._is_running = True
            app._show_interrupt_btn(True)
            await pilot.pause()
            assert str(send.label).startswith("⏹"), "running: slot IS the stop button"
            assert "running" in send.classes
            assert send.region.width > 0, "stop button must occupy real layout space"

            # Interrupt while running → turn end path restores the slot.
            app._is_running = False
            app._show_interrupt_btn(False)
            await pilot.pause()
            assert str(send.label).startswith("➤"), "turn end restores the submit slot"
            assert "running" not in send.classes

    @pytest.mark.asyncio
    async def test_send_button_submits_input_when_idle(self) -> None:
        """Clicking ➤ must dispatch through the SAME path as Enter
        (_spawn_turn → _on_input), so both submission routes can never
        drift apart."""
        app = CoderioTUI()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            seen: list[str] = []
            app._on_input = seen.append  # no real engine behind the test
            inp = app.query_one("#msg", Input)
            inp.value = "hello via send button"
            await pilot.pause()
            await pilot.click("#send-btn")
            await pilot.pause()
            assert inp.value == "", "clicking ➤ must clear the input"
            assert seen == ["hello via send button"], f"➤ click must reach the engine dispatch path: {seen}"

    @pytest.mark.asyncio
    async def test_send_button_interrupts_when_running(self) -> None:
        """Clicking ⏹ while running must request an interrupt (the same flag
        Esc sets) — that is the ONE stop concept."""
        app = CoderioTUI()
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            app._is_running = True
            app._show_interrupt_btn(True)
            await pilot.pause()
            assert app._interrupted is False
            await pilot.click("#send-btn")
            await pilot.pause()
            assert app._interrupted is True, "⏹ click must raise the interrupt flag"
