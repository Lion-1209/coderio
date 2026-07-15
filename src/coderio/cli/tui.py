"""Textual-based TUI for coderio — foldable thinking, scrollable history.

Implements the StreamHandler protocol (so agent/tools/harness call it
identically to the Rich version). Each round's thinking is rendered as a
Collapsible widget — collapsed by default, expandable via Ctrl+O (toggles the
most recent) or mouse click on the title. This gives true fold/unfold, which
RichLog (append-only) could not.

The agent runs in a background thread; UI updates dispatch via call_from_thread.
"""
from __future__ import annotations

import time
from typing import Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from rich.console import RenderableType
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Collapsible, Input, ListItem, ListView, Static


class CommandMenu(Vertical):
    """Popup slash-command menu (Claude-Code-style autocomplete).

    Shown when the input starts with "/". Lists matching commands, filtered live
    as the user types. ↑↓ navigates, Tab/Enter fills the chosen command into the
    input, Esc or clearing "/" hides it. Unlike SuggestFromList (which shows a
    single inline grey suggestion), this is a visible, browsable menu — the user
    can see all candidates and pick one with the keyboard.
    """

    DEFAULT_CSS = """
    CommandMenu {
        display: none;          /* hidden until input starts with "/" */
        dock: bottom;
        height: auto; max-height: 12;
        layer: above;
        background: $surface;
        border: round $accent;
        padding: 0;
        margin: 0 1;
    }
    CommandMenu.-visible { display: block; }
    CommandMenu ListView { background: $surface; }
    CommandMenu ListItem { padding: 0 1; }
    CommandMenu ListItem > Widget :hover { background: $boost; }
    """

    def __init__(self, completions: list[str]) -> None:
        super().__init__()
        self._all = completions
        self._input: Input | None = None  # the Input this menu feeds
        # Value last accepted by Tab/Enter. refresh_for skips reopening while the
        # input still equals this — otherwise setting .value in accept() retriggers
        # on_input_changed and the menu pops right back open.
        self._accepted_value: str | None = None

    def compose(self) -> ComposeResult:
        yield ListView(id="cmd-list")

    def bind_input(self, inp: Input) -> None:
        self._input = inp

    def _matches(self, prefix: str) -> list[str]:
        if not prefix:
            return []
        p = prefix.lower()
        # rank: exact prefix match first, then substring contains.
        exact = [c for c in self._all if c.lower().startswith(p)]
        sub = [c for c in self._all if p in c.lower() and c not in exact]
        return exact + sub

    def refresh_for(self, value: str) -> None:
        """Re-filter and show/hide based on the current input value."""
        # If the user just accepted a command (Tab/Enter), the value now equals
        # the chosen command. Don't reopen the menu for that exact value — it
        # was set programmatically by accept(), not typed. Once the user edits
        # further (value differs), normal filtering resumes.
        if value == self._accepted_value:
            return
        self._accepted_value = None  # value changed -> clear the guard
        if not value.startswith("/"):
            self.remove_class("-visible")
            return
        matches = self._matches(value)
        if not matches:
            self.remove_class("-visible")
            return
        lv = self.query_one("#cmd-list", ListView)
        lv.clear()
        for c in matches:
            lv.append(ListItem(Static(c), name=c))
        self.add_class("-visible")
        # auto-select the first (top) match so Enter is immediately meaningful
        try:
            lv.index = 0
        except Exception:
            pass

    def visible(self) -> bool:
        return self.has_class("-visible")

    def move(self, delta: int) -> None:
        """Move the selection by delta (+1 down, -1 up); wraps around."""
        if not self.visible():
            return
        lv = self.query_one("#cmd-list", ListView)
        if not lv.children:
            return
        n = len(lv.children)
        idx = lv.index or 0
        lv.index = (idx + delta) % n

    def accept(self) -> bool:
        """Fill the selected command into the bound Input. Returns True if accepted."""
        if not self.visible() or self._input is None:
            return False
        lv = self.query_one("#cmd-list", ListView)
        if not lv.children or lv.index is None:
            return False
        chosen = lv.children[lv.index].name or ""
        if not chosen:
            return False
        # Record the accepted value so the resulting on_input_changed doesn't
        # reopen the menu (setting .value fires changed, value still starts "/").
        self._accepted_value = chosen
        self._input.value = chosen
        self.remove_class("-visible")
        self._input.focus()
        return True

    def hide(self) -> None:
        self.remove_class("-visible")


class StatusBar(Widget):
    """Live status bar: animated spinner + phase + step + elapsed timer.

    Modeled on Claude Code's bottom indicator: a braille-dot spinner that
    ANIMATES while the agent is working (cycling ⠋⠙⠹⠸⠼⠴⠦⠧ at ~12fps), followed by
    a concrete phase label ("步骤1 · 执行 read_file(1/3)") and a live elapsed
    timer. When idle the spinner stops and shows a static "(就绪)".

    Owns its own background-thread heartbeat (~80ms) that drives BOTH the spinner
    animation and the timer refresh via call_from_thread(refresh, layout=False).
    The phase/tool/step attributes are written by the agent thread (plain
    attribute writes, GIL-safe); render() reads them on the main thread.
    """

    # Claude Code's spinner frames (reverse-engineered, ~80ms each).
    _SPINNER = "⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏".split()
    _BEAT_MS = 0.08  # ~12.5fps — smooth animation, matches Claude Code

    DEFAULT_CSS = """
    StatusBar {
        height: 1; padding: 0 1;
        background: $boost; color: $text-muted;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self.phase: str = "idle"
        self.phase_start: float = 0.0
        self.tool_name: str = ""
        self.step: int = 0
        self.tool_index: int = 0
        self.tool_total: int = 0
        self._app = None
        self._spin_frame = 0  # cycles through _SPINNER each heartbeat while active

    def on_mount(self) -> None:
        self._app = self.app
        import threading
        self._beat_stop = threading.Event()
        t = threading.Thread(target=self._heartbeat_loop, daemon=True)
        t.start()

    def _heartbeat_loop(self) -> None:
        """Background thread: wake ~12x/sec, advance the spinner frame, and force
        a repaint. This drives the ANIMATION (the spinner visibly cycles) and the
        elapsed timer — both update in lockstep. Runs off the main thread; the
        only main-thread touch is call_from_thread(refresh)."""
        while not self._beat_stop.wait(self._BEAT_MS):
            try:
                if self._app is None or not self._app.is_running:
                    break
                if self.phase != "idle":
                    self._spin_frame = (self._spin_frame + 1) % len(self._SPINNER)
                self._app.call_from_thread(self.refresh, layout=False)
            except Exception:
                break

    def on_unmount(self) -> None:
        self._beat_stop.set()

    def set_phase(self, phase: str, tool_name: str = "", step: int = 0,
                  tool_index: int = 0, tool_total: int = 0) -> None:
        """Update the displayed phase.

        Safe to call from ANY thread: only mutates plain attributes (GIL-safe).
        Deliberately does NOT call refresh() here — that's a widget-state mutation
        that Textual requires on the main thread. The background heartbeat picks
        up the new phase within 100ms. (Calling refresh from the agent thread was
        the previous bug — it raced Textual's internal dirty flags.)
        """
        self.phase = phase
        self.phase_start = time.monotonic() if phase != "idle" else 0.0
        if tool_name:
            self.tool_name = tool_name
        if step:
            self.step = step
        if tool_total:
            self.tool_index = tool_index
            self.tool_total = tool_total

    def render(self) -> RenderableType:
        # Build a phase label that shows WHERE in the task the agent is, so the
        # user can distinguish "still working, step 3" from "frozen". The step
        # number + tool index give concrete progress, not just a vague spinner.
        step_tag = f"步骤{self.step}" if self.step else ""
        if self.phase == "tool" and self.tool_total > 1:
            tool_tag = f"{self.tool_name}({self.tool_index+1}/{self.tool_total})"
        elif self.phase == "tool":
            tool_tag = self.tool_name or "工具"
        else:
            tool_tag = ""
        labels = {
            "idle": "(就绪)",
            "thinking": "思考中",
            "responding": "输出中",
            "tool": f"执行 {tool_tag}",
        }
        # Diagnostic log (only when CODERIO_DEBUG is set) — confirms render() is
        # being called and what phase it sees. Set CODERIO_DEBUG=1 and check
        # ~/.coderio/statusbar.log to verify the heartbeat is alive.
        import os
        if os.environ.get("CODERIO_DEBUG"):
            try:
                from pathlib import Path as _P
                with open(_P.home() / ".coderio" / "statusbar.log", "a", encoding="utf-8") as f:
                    f.write(f"{time.monotonic():.2f} render phase={self.phase} step={self.step}\n")
            except Exception:
                pass
        if self.phase == "idle":
            return Text(labels["idle"], style="dim")
        elapsed = time.monotonic() - self.phase_start if self.phase_start else 0.0
        label = labels.get(self.phase, self.phase)
        # The spinner ANIMATES: each heartbeat advances _spin_frame.
        spin = self._SPINNER[self._spin_frame]
        # Build the Text by APPENDING separate spans. The braille spinner char and
        # the CJK text are in different segments so Textual computes cell widths
        # independently — mixing them in one f-string caused the terminal to
        # miscalculate width (braille + CJK adjacency), intermittently eating the
        # '步' character. Separate spans + overflow='ellipsis' + no_wrap fixes it.
        parts = []
        if step_tag:
            parts.append(step_tag)
        parts.append(label)
        parts.append(f"{elapsed:.1f}s")
        body = " · ".join(parts)
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(spin + " ", style="bold cyan")
        t.append(body)
        return t


class SessionPickerScreen(ModalScreen[str | None]):
    """Interactive session picker (Claude-Code-style /resume).

    Shows recent sessions as a scrollable list — each row has the first user
    message (so the user recognizes the conversation by what they asked, not by
    an opaque id), the message count, and the time. ↑↓ navigates, Enter resumes,
    Esc cancels. Typing filters the list by the summary text. Dismisses with the
    chosen session id (string) or None (cancelled).
    """

    CSS = """
    SessionPickerScreen {
        align: center middle;
    }
    #picker-box {
        width: 80%; height: 70%; border: thick $accent; background: $surface;
        padding: 1 2;
    }
    #picker-title { text-align: center; color: $accent; margin-bottom: 1; }
    #picker-filter {
        dock: bottom; margin-top: 1; border: round $accent 50%;
    }
    #picker-filter:focus { border: round $accent; }
    #picker-list { height: 1fr; border: none; }
    .picker-row { padding: 0 1; }
    .picker-row:first-child { margin-top: 0; }
    .picker-summary { color: $text; }
    .picker-meta { color: $text-muted; }
    """

    BINDINGS = [
        Binding("escape", "cancel", "取消", show=True),
    ]

    def __init__(self, summaries: list[dict]) -> None:
        super().__init__()
        self._all = summaries  # full list; filtered view derived on typing
        self._filter = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="picker-box"):
            yield Static("[bold]恢复会话[/bold]  ↑↓ 选择 · Enter 恢复 · Esc 取消 · 输入过滤",
                         id="picker-title")
            yield ListView(id="picker-list")
            yield Input(placeholder="输入关键字过滤（首条消息 / 时间）", id="picker-filter")

    def on_mount(self) -> None:
        self._populate()
        self.query_one("#picker-filter", Input).focus()

    def _populate(self) -> None:
        """Rebuild the list from the (filtered) summaries."""
        lv = self.query_one("#picker-list", ListView)
        lv.clear()
        f = self._filter.lower()
        for s in self._all:
            label = s["first_user"] or "(空会话)"
            if f and f not in label.lower() and f not in s["mtime"].lower() and f not in s["id"].lower():
                continue
            row = Static(
                f"[{s['mtime']}] {label}\n"
                f"  [dim]{s['message_count']} 条消息 · {s.get('model') or '?'} · {s['id']}[/dim]",
                classes="picker-row")
            lv.append(ListItem(row, name=s["id"]))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "picker-filter":
            self._filter = event.value
            self._populate()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Enter on a row → resume that session."""
        self.dismiss(event.item.name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class CoderioTUI(App):
    """coderio's Textual app. Implements StreamHandler (duck-typed)."""

    CSS = """
    Screen { layout: vertical; }
    #history { border: round $accent; height: 1fr; min-height: 10; padding: 0 1; }
    #input-bar { height: auto; dock: bottom; border-top: solid $accent; }
    #input-bar Input { border: none; }
    /* Collapsible thinking blocks */
    Collapsible { border: round $boost 50%; margin: 0 0 0 0; }
    Collapsible > .collapsible__title { color: $text-muted; }
    /* Command menu sits above the input bar; input bar must allow overlay */
    Screen { layers: base above; }
    #input-bar { layer: base; }
    """

    BINDINGS = [
        Binding("ctrl+o", "toggle_thinking", "展开/收起思考", show=True),
        Binding("ctrl+c", "quit", "退出", show=False),
    ]

    name = "textual_tui"

    def __init__(self, on_input=None, show_tool_output: bool = True,
                 banner: str | None = None) -> None:
        super().__init__()
        self._on_input = on_input
        self.show_tool_output = show_tool_output
        self._banner = banner
        # StreamHandler state
        self.buffer = ""
        self.usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        self._round_thinking = ""
        self._round_think_start = 0.0
        self._last_collapsible = None  # the most recent thinking Collapsible widget
        # Live thinking: the Static body of the IN-PROGRESS thinking block. While
        # non-None, on_thinking appends to it in real time (the user sees thinking
        # stream live, not dumped all at once when it ends). Set to None when a
        # round's thinking is flushed/folded.
        self._live_think_body: Static | None = None
        self._live_think_chars = 0  # chars shown so far (to append only the delta)
        # Live output: the Static widget for IN-PROGRESS visible text. While
        # non-None, on_token appends to it in real time (user sees the answer grow
        # as it streams, like ChatGPT/Claude). Previously tokens accumulated in
        # self.buffer with NO rendering until on_finish, then a giant Markdown
        # Panel was mounted at once — long outputs (2000+ chars) truncated or
        # failed to scroll into view. Streaming into a live widget avoids both.
        self._live_output: Static | None = None
        self._live_output_chars = 0
        self._live_output_last_flush: float = 0.0  # throttle: only flush >=80ms apart
        # Cached StatusBar reference: query_one() is a DOM query that Textual
        # requires on the main thread, but _set_phase is called from the agent's
        # BACKGROUND thread (StreamHandler callbacks). Calling query_one there
        # raised and was silently swallowed — so phase NEVER reached the widget
        # (statusbar.log showed phase=idle for the entire run). Cache the widget
        # once (on_mount, main thread) and use the plain attribute thereafter
        # (GIL-safe read from any thread).
        self._status_bar: StatusBar | None = None

    # ----------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        from coderio.cli.commands import slash_completions
        yield VerticalScroll(id="history")
        # The command menu (popup, hidden by default; shown when input starts "/").
        # Layered above the input bar so it overlays the history pane.
        yield CommandMenu(slash_completions())
        # input-bar holds BOTH the StatusBar and the Input — StatusBar sits above
        # the input inside the same docked container. (Previously StatusBar and
        # input-bar BOTH docked to bottom independently, which made them overlap —
        # the StatusBar was invisible behind the input bar.)
        with Vertical(id="input-bar"):
            yield StatusBar()
            yield Input(
                placeholder="输入消息, /help 看命令, Ctrl+O 展开思考",
                id="msg",
            )

    def on_mount(self) -> None:
        self.title = "coderio"
        self.sub_title = "skill-driven coding agent"
        if self._banner:
            self._add_static(Panel(self._banner, title="[bold magenta]coderio[/bold magenta]", border_style="magenta"))
        inp = self.query_one("#msg", Input)
        # Wire the popup command menu to the input.
        self.query_one(CommandMenu).bind_input(inp)
        # Cache the StatusBar reference NOW (main thread) — _set_phase is called
        # from the agent's background thread where query_one() can't run.
        self._status_bar = self.query_one(StatusBar)
        inp.focus()

    # ----------------------------------------------------- status bar (phase routing)
    def _set_phase(self, phase: str, tool_name: str = "", step: int = 0,
                   tool_index: int = 0, tool_total: int = 0) -> None:
        """Forward a phase change to the StatusBar widget.

        Called from the agent's BACKGROUND thread (StreamHandler callbacks). Do
        NOT use query_one here — it's a DOM query that Textual requires on the
        main thread; calling it from the agent thread raised and was swallowed,
        so phase never reached the widget (the statusbar.log showed phase=idle
        for an entire run). Use the cached reference instead (plain attribute
        read, GIL-safe). StatusBar.set_phase only writes plain attributes too.
        """
        bar = self._status_bar
        if bar is None:
            return  # not mounted yet
        bar.set_phase(phase, tool_name, step=step, tool_index=tool_index, tool_total=tool_total)

    # ----------------------------------------------------- command menu (autocomplete)
    def on_input_changed(self, event: Input.Changed) -> None:
        """Live-filter the command menu as the user types in the main input."""
        if event.input.id != "msg":
            return
        self.query_one(CommandMenu).refresh_for(event.value)

    def on_key(self, event) -> None:
        """Handle command-menu navigation (↑↓/Tab/Enter/Esc) when it's visible."""
        menu = self.query_one(CommandMenu)
        if not menu.visible():
            return
        if event.key == "up":
            menu.move(-1)
            event.prevent_default()
        elif event.key == "down":
            menu.move(1)
            event.prevent_default()
        elif event.key in ("tab", "enter"):
            if menu.accept():
                event.prevent_default()
        elif event.key == "escape":
            menu.hide()
            event.prevent_default()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        line = event.value.strip()
        if not line:
            return
        event.input.value = ""
        self._add_text(f"▸ you {line}", style="bold cyan")
        if self._on_input:
            import threading
            def _run():
                try:
                    self._on_input(line)
                except SystemExit:
                    self.call_from_thread(self.exit)
                except Exception as e:
                    self.call_from_thread(self._add_text,
                                          f"运行错误: {type(e).__name__}: {e}", style="red")
            threading.Thread(target=_run, daemon=True).start()

    # ----------------------------------------------------- binding: Ctrl+O
    def action_toggle_thinking(self) -> None:
        """Ctrl+O — toggle the thinking block at the current focus.

        Walks up from the focused widget to find the enclosing Collapsible. If
        the focus is on (or inside) a thinking block, toggles THAT one — so the
        user can choose which round to expand by clicking into it first. If no
        thinking block is focused (e.g. focus is on the input bar), falls back
        to the most recent one.
        """
        target = self._focused_collapsible()
        if target is None:
            # Fallback: focus not on a thinking block → toggle the most recent.
            target = self._last_collapsible
        if target is None:
            self._add_text("最近一轮没有思考内容。", style="dim")
            return
        target.collapsed = not target.collapsed

    def _focused_collapsible(self):
        """Find the Collapsible enclosing the currently-focused widget, if any."""
        focused = self.focused
        if focused is None:
            return None
        # Walk up the widget tree looking for a Collapsible ancestor.
        node = focused
        while node is not None:
            if isinstance(node, Collapsible):
                return node
            node = node.parent
        return None

    # ----------------------------------------------------- StreamHandler protocol
    def on_step_start(self, step: int = 1) -> None:
        self._flush_round_thinking()
        # A new model call begins: show "thinking" with the step number so the user
        # sees concrete progress ("步骤 2 · 思考中") instead of a vague spinner.
        self._set_phase("thinking", step=step)

    def on_token(self, text: str) -> None:
        self._flush_round_thinking()
        bar = self._status_bar
        if bar is None or bar.phase != "responding":
            self._set_phase("responding")
        # STREAM OUTPUT LIVE — but THROTTLED. Calling call_from_thread on EVERY
        # token (thousands per response) flooded the main event loop's callback
        # queue — _update_live_output never got to run, and the output appeared
        # to truncate. Now: accumulate in self.buffer on the agent thread, and
        # only dispatch a UI update at most once per ~80ms. The final on_finish
        # flush captures whatever buffer remains, so no content is lost between
        # throttle ticks.
        self.buffer += text
        now = time.monotonic()
        is_first = self._live_output is None
        if is_first or (now - self._live_output_last_flush) >= 0.08:
            self._live_output_last_flush = now
            import threading
            if threading.current_thread() is threading.main_thread():
                self._update_live_output(self.buffer)
            else:
                self.call_from_thread(self._update_live_output, self.buffer)

    def _update_live_output(self, full_text: str) -> None:
        """MAIN THREAD: create or update the live output widget."""
        import os
        if os.environ.get("CODERIO_DEBUG"):
            try:
                from pathlib import Path as _P
                with open(_P.home() / ".coderio" / "statusbar.log", "a", encoding="utf-8") as f:
                    f.write(f"{time.monotonic():.2f} live_output update chars={len(full_text)}\n")
            except Exception:
                pass
        if self._live_output is None:
            self._live_output = Static(Text(full_text))
            self._live_output_chars = len(full_text)
            self._mount_widget_main(self._live_output)
        else:
            self._live_output.update(Text(full_text))
            self._live_output_chars = len(full_text)

    def on_thinking(self, text: str) -> None:
        # Stream thinking LIVE: create an expanded Collapsible on the first chunk,
        # then append each subsequent chunk as it arrives. This fixes the core UX
        # bug where thinking was invisible until it finished — the user sees the
        # reasoning grow in real time, so they know the agent is working (not hung).
        #
        # Widget creation (Static/Collapsible) and .update() must run on the main
        # thread. When called from the agent's background thread, dispatch via
        # call_from_thread; when on the main thread (tests), call directly.
        if not self._round_thinking:
            self._round_think_start = time.monotonic()
        self._round_thinking += text
        import threading
        on_main = threading.current_thread() is threading.main_thread()
        if self._live_think_body is None:
            self._live_think_chars = len(self._round_thinking)
            fn = self._mount_live_thinking
            args = (self._round_thinking,)
        else:
            delta_len = len(self._round_thinking) - self._live_think_chars
            if delta_len <= 0:
                return
            self._live_think_chars = len(self._round_thinking)
            fn = self._update_live_thinking
            args = (self._round_thinking,)
        if on_main:
            fn(*args)
        else:
            self.call_from_thread(fn, *args)

    def _mount_live_thinking(self, full_text: str) -> None:
        """MAIN THREAD: create + mount the live (expanded) thinking block. Called
        via call_from_thread from on_thinking."""
        self._live_think_body = Static(Text(full_text))
        col = Collapsible(self._live_think_body, title="💭 思考中…",
                          collapsed=False, collapsed_symbol="▶", expanded_symbol="▼",
                          classes="think-block")
        self._last_collapsible = col
        self._mount_widget_main(col)

    def _update_live_thinking(self, full_text: str) -> None:
        """MAIN THREAD: update the live thinking body with accumulated text.
        Called via call_from_thread from on_thinking."""
        if self._live_think_body is not None:
            self._live_think_body.update(Text(full_text))

    def on_tool_start(self, name: str, args: dict[str, Any], step: int = 1,
                      tool_index: int = 0, tool_total: int = 0) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        # Tool execution has its own phase so the timer reflects the tool, not a
        # frozen "thinking" label. Pass step/tool_index/tool_total so the bar can
        # show "执行 read_file(1/3)" when multiple tools run in one round.
        self._set_phase("tool", tool_name=name, step=step,
                        tool_index=tool_index, tool_total=tool_total)
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        if len(args_str) > 100:
            args_str = args_str[:100] + "…"
        self._add_text(f"⏺ {name}({args_str})", style="green")

    def on_tool_end(self, name: str, result: str) -> None:
        # Tool finished; the next model call (on_step_start) will re-enter thinking.
        # Until then show responding-ish idle is wrong; go back to a brief thinking
        # phase since the loop immediately calls the model again.
        self._set_phase("thinking")
        if not self.show_tool_output:
            first = result.splitlines()[0][:60] if result.splitlines() else ""
            self._add_text(f"  → {first}{'…' if len(result) > 60 else ''}", style="dim")
            return
        lines = result.splitlines()
        shown = "\n".join(lines[:3])
        if len(lines) > 3:
            shown += f"\n…({len(lines) - 3} more lines)"
        self._add_text(shown, style="dim")

    def on_truncated(self, stop_reason: str) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        self._add_static(Panel(
            f"⚠ 输出被截断 (stop_reason: {stop_reason})。",
            title="截断警告", border_style="yellow"))

    def on_harness_warn(self, message: str) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        self._add_static(Panel(
            f"⚠ {message}\n\n产出可能未经验证，请人工复核。",
            title="⚠ harness 警告", border_style="red"))

    def on_finish(self) -> None:
        # Capture the final output NOW (on the agent thread) and dispatch ALL the
        # remaining rendering as ONE main-thread callback. Previously _flush_round_
        # thinking and _flush_buffer each dispatched separately via call_from_thread
        # — two async queue entries that could race or stall, and the final text
        # Panel sometimes didn't appear until the user's NEXT input drained the
        # queue. Bundling into one callback guarantees it all lands atomically.
        import threading
        think_text = self._round_thinking
        think_start = self._round_think_start
        had_live = self._live_think_body is not None
        buf = self.buffer
        # Reset accumulated state immediately (so a next round starts clean).
        self._round_thinking = ""
        self._round_think_start = 0.0
        self._live_think_body = None
        self._live_think_chars = 0
        self.buffer = ""
        # Capture the live output widget so we can remove it in the finalize callback
        live_out = self._live_output
        self._live_output = None
        self._live_output_chars = 0

        def _finalize():
            """MAIN THREAD: fold thinking + mount the final answer Panel."""
            import os
            if os.environ.get("CODERIO_DEBUG"):
                try:
                    from pathlib import Path as _P
                    with open(_P.home() / ".coderio" / "statusbar.log", "a", encoding="utf-8") as f:
                        f.write(f"{time.monotonic():.2f} finalize buf_chars={len(buf)} live_out={live_out is not None}\n")
                except Exception:
                    pass
            if think_text.strip():
                self._flush_round_thinking_main(think_text,
                                                time.monotonic() - think_start if think_start else 0.0,
                                                had_live)
            if buf.strip():
                # Remove the live raw-text widget (it was streaming); replace with Panel.
                if live_out is not None:
                    try:
                        live_out.remove()
                    except Exception:
                        pass
                self._add_static_main(Panel(Markdown(buf), border_style="blue", title="coderio"))
            self._status_bar.set_phase("idle") if self._status_bar else None

        if threading.current_thread() is threading.main_thread():
            _finalize()
        else:
            self.call_from_thread(_finalize)

    def add_usage(self, meta: dict[str, int]) -> None:
        for k in ("input_tokens", "output_tokens"):
            if k in meta:
                self.usage[k] += meta[k]

    # ----------------------------------------------------- thinking fold (true fold/unfold)
    def _flush_round_thinking(self) -> None:
        """Fold the live (in-progress) thinking block: collapse it and finalize
        the title with the elapsed time + char count.

        Runs on the agent's BACKGROUND thread (called by on_token/on_tool_start/
        on_finish). Widget operations (title/collapsed/mount) must be on the main
        thread, so the actual widget work is dispatched via call_from_thread.
        The plain-attribute state (snapshot of the accumulated text) is captured
        HERE and passed to the main-thread helper.
        """
        if not self._round_thinking.strip():
            return
        # Snapshot the state to pass to the main thread (the attributes get reset
        # below; the helper needs the values, not the live attributes).
        text = self._round_thinking
        secs = time.monotonic() - self._round_think_start if self._round_think_start else 0.0
        had_live = self._live_think_body is not None
        # Reset accumulated state NOW (so the next round starts clean even though
        # the widget update is async).
        self._round_thinking = ""
        self._round_think_start = 0.0
        self._live_think_body = None
        self._live_think_chars = 0
        # Dispatch the widget work to the main thread.
        import threading
        if threading.current_thread() is threading.main_thread():
            self._flush_round_thinking_main(text, secs, had_live)
        else:
            self.call_from_thread(self._flush_round_thinking_main, text, secs, had_live)

    def _flush_round_thinking_main(self, text: str, secs: float, had_live: bool) -> None:
        """MAIN THREAD: fold/create the thinking Collapsible. Called via
        call_from_thread from _flush_round_thinking."""
        chars = len(text)
        title = f"💭 思考 · {secs:.1f}s · {chars} 字 · Ctrl+O / 点击展开"
        if had_live and self._last_collapsible is not None:
            # Live streaming happened: collapse the existing expanded block.
            self._last_collapsible.title = title
            self._last_collapsible.collapsed = True
        else:
            # Fallback: no live body (e.g. thinking arrived non-streamed).
            body = Static(Text(text))
            col = Collapsible(body, title=title, collapsed=True,
                              collapsed_symbol="▶", expanded_symbol="▼", classes="think-block")
            self._last_collapsible = col
            self._mount_widget_main(col)

    def show_last_thinking(self) -> bool:
        """Expand the most recent thinking (compat with /think command)."""
        if self._last_collapsible is None:
            self._add_text("最近一轮没有思考内容。", style="dim")
            return False
        self._last_collapsible.collapsed = False  # expand
        return True

    # ----------------------------------------------------- helpers: add content to history
    def _flush_buffer(self) -> None:
        # The output was streamed live into _live_output; now finalize it by
        # replacing the raw-text Static with a formatted Markdown Panel.
        if self.buffer.strip():
            self._finalize_live_output()
        self.buffer = ""

    def _finalize_live_output(self) -> None:
        """MAIN THREAD (via callers): replace the live raw-text Static with a
        Markdown Panel, or mount one if there was no live widget."""
        import threading
        text = self.buffer
        def _do():
            # Remove the live raw-text widget if present (it'll be replaced by Panel)
            if self._live_output is not None:
                try:
                    self._live_output.remove()
                except Exception:
                    pass
                self._live_output = None
                self._live_output_chars = 0
            self._add_static_main(Panel(Markdown(text), border_style="blue", title="coderio"))
        if threading.current_thread() is threading.main_thread():
            _do()
        else:
            self.call_from_thread(_do)

    def _add_text(self, text: str, style: str = "") -> None:
        """Thread-safe: add a styled text line to the history."""
        import threading
        if threading.current_thread() is not threading.main_thread():
            self.call_from_thread(self._add_text_main, text, style)
        else:
            self._add_text_main(text, style)

    def _add_text_main(self, text: str, style: str = "") -> None:
        try:
            history = self.query_one("#history", VerticalScroll)
            history.mount(Static(Text(text, style=style) if style else Text(text)))
            history.call_after_refresh(history.scroll_end, animate=False)
        except Exception:
            pass

    def _add_static(self, renderable) -> None:
        """Thread-safe: add a Rich renderable (Panel/Markdown) to history."""
        import threading
        if threading.current_thread() is not threading.main_thread():
            self.call_from_thread(self._add_static_main, renderable)
        else:
            self._add_static_main(renderable)

    def _add_static_main(self, renderable) -> None:
        try:
            history = self.query_one("#history", VerticalScroll)
            widget = Static(renderable)
            history.mount(widget)
            # Scroll to end AFTER the mount + layout settle. Calling scroll_end
            # synchronously right after mount races the layout: the new widget's
            # height hasn't been computed yet, so scroll_end lands above the true
            # bottom — for long outputs (3000+ chars) the tail ends up below the
            # viewport and looks like 'missing content'. call_after_refresh defers
            # the scroll until Textual has finished the layout pass.
            history.call_after_refresh(history.scroll_end, animate=False)
        except Exception:
            pass

    def _mount_widget(self, widget) -> None:
        """Thread-safe: mount an arbitrary widget (e.g. Collapsible) to history."""
        import threading
        if threading.current_thread() is not threading.main_thread():
            self.call_from_thread(self._mount_widget_main, widget)
        else:
            self._mount_widget_main(widget)

    def _mount_widget_main(self, widget) -> None:
        try:
            history = self.query_one("#history", VerticalScroll)
            history.mount(widget)
            history.call_after_refresh(history.scroll_end, animate=False)
        except Exception:
            pass


def run_tui(
    provider_override: str | None = None,
    model_override: str | None = None,
    resume: str | None = None,
    continue_last: bool = False,
) -> None:
    """Launch the Textual TUI, wired to coderio's agent runtime.

    Builds the same runtime as the Rich REPL (config, model, tools, skills,
    session), then runs the CoderioTUI app. Each user submission drives
    run_agent in a background thread; the TUI stays interactive (Ctrl+O, scroll).

    ``resume`` / ``continue_last`` load a prior session into the conversation
    history (same semantics as the REPL's --resume/--continue).
    """
    from pathlib import Path
    from coderio.cli.repl import build_runtime, _resolve_resume
    from coderio.config import load_config
    from coderio.config.bootstrap import ensure_user_dirs

    ensure_user_dirs()
    search_from = "."
    creds_path = Path.home() / ".coderio" / "credentials"

    # Resolve a session to resume BEFORE building the runtime (so build_runtime
    # receives it instead of creating a fresh one).
    session = None
    if resume or continue_last:
        cfg = load_config(search_from=search_from)
        try:
            session = _resolve_resume(cfg, resume, continue_last)
        except SystemExit as e:
            # _resolve_resume raises SystemExit on "no previous session"; surface
            # it as a clean banner rather than a crash.
            tui = CoderioTUI()
            tui._banner = f"[red]{e}[/red]"
            tui.run()
            return

    try:
        cfg, store, model, tools, gate, session, active, _rich_stream = build_runtime(
            search_from=search_from, console=None, creds_path=creds_path,
            provider_override=provider_override, model_override=model_override,
            session=session,
        )
    except Exception as e:
        tui = CoderioTUI()
        tui._banner = (
            f"[red]启动失败:[/red] {type(e).__name__}: {e}\n\n"
            "常见原因: API key 未配置 / provider 无效 / 网络不通。\n"
            "运行 coderio config 检查配置, 或设置 ANTHROPIC_API_KEY 环境变量。"
        )
        tui.run()
        return

    banner = (
        f"[bold magenta]coderio[/bold magenta]  [dim]model=[/dim]{cfg.model.default}  "
        f"[dim]perm=[/dim]{gate.mode}"
        "\n[dim]模式:[/dim] [cyan]single-agent (Textual TUI)[/cyan]. "
        "大需求可用 [yellow]/crew[/yellow] 进 6-agent 流水线."
        "\n[dim]输入 /help 看命令, /exit 退出, Ctrl+O 展开/收起思考[/dim]"
    )

    # Mutable runtime holder — /model, /mode, /resume rebuild parts in place.
    rt = {"cfg": cfg, "model": model, "gate": gate, "session": session}

    def on_input(line: str) -> None:
        if line.startswith("/"):
            from coderio.cli.commands import handle_slash, ReplContext
            from coderio.session.store import Session
            from pathlib import Path as _P
            ctx = ReplContext(
                available_skills=store.names(),
                active_skills_names={s.name for s in active.all()},
                permission_mode=rt["gate"].mode,
                model_name=rt["cfg"].model.default,
                provider_id=rt["cfg"].model.provider_id,
                api_key="",
                base_url=rt["cfg"].model.base_url,
                recent_sessions=Session.list_recent(_P(rt["cfg"].session.save_dir).expanduser()),
                usage=tui.usage,
                stream=tui,
            )
            res = handle_slash(line, ctx)
            # /resume with no arg → open the interactive picker instead of printing.
            # push_screen MUST run on the main thread (it touches the Textual
            # event loop); on_input runs in the agent's background thread, so
            # dispatch via call_from_thread — same pattern as _add_text.
            if res.message == "__OPEN_PICKER__":
                summaries = Session.summaries(_P(rt["cfg"].session.save_dir).expanduser())
                def _on_picked(sid):
                    """Picker dismissed: sid is the chosen id, or None if cancelled."""
                    if sid is None:
                        return
                    _load_session(sid)
                tui.call_from_thread(tui.push_screen, SessionPickerScreen(summaries), _on_picked)
                return
            if res.message:
                tui._add_text(res.message)
            if not res.continue_loop:
                tui.call_from_thread(tui.exit)
                return
            # /resume <explicit-id> path: load straight from the result.
            if res.new_session_id:
                _load_session(res.new_session_id)
                return
            if res.reset_runtime:
                from dataclasses import replace as _replace
                from coderio.cli.repl import build_gate
                from coderio.llm import build_chat_model
                c = rt["cfg"]
                if res.new_permission_mode:
                    c = _replace(c, tools=_replace(c.tools, permission_mode=res.new_permission_mode))
                    rt["cfg"] = c
                    rt["gate"] = build_gate(c, console=None)
                cmd_name = line.strip().split(maxsplit=1)[0]
                if cmd_name == "/model":
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) > 1 and parts[1].strip():
                        c = _replace(c, model=_replace(c.model, default=parts[1].strip()))
                        rt["cfg"] = c
                        rt["model"] = build_chat_model(c, creds_path=creds_path)
            return
        from coderio.agent.loop import run_agent
        from coderio.cli.multimodal import build_user_content, extract_images
        imgs = extract_images(line)
        if imgs:
            tui._add_text(f"📎 已附加 {len(imgs)} 张图片: " + ", ".join(p for p, _, _ in imgs), style="dim")
        user_content = build_user_content(line)
        run_agent(
            user_input=user_content, model=rt["model"], tools=tools, gate=rt["gate"],
            skill_store=store, active_skills=active, session=rt["session"], stream=tui,
            max_rounds=rt["cfg"].tools.max_tool_rounds,
            stage_auto_inject=rt["cfg"].skills.stage_auto_inject,
            harness_enabled=rt["cfg"].skills.harness,
        )

    def _load_session(sid: str) -> None:
        """Swap the active session to a loaded one, clear skills, render history.

        Called after the picker picks a session (or /resume <id> is given). The
        old session's jsonl stays on disk; we just point the runtime at the new
        Session object so subsequent turns continue that conversation.
        """
        from coderio.session.store import Session
        from pathlib import Path as _P
        save_dir = _P(rt["cfg"].session.save_dir).expanduser()
        rt["session"] = Session.load_by_id(save_dir, sid)
        active.clear()
        # Render the resumed conversation into the history pane so the user sees
        # context they're continuing, not a blank screen.
        tui._add_text(f"↩ 已恢复会话 {sid}（{len(rt['session'].messages)} 条历史消息）", style="bold green")
        for m in rt["session"].messages:
            if m.role == "user":
                c = m.content
                if isinstance(c, list):
                    c = " ".join(b.get("text", "") for b in c
                                 if isinstance(b, dict) and b.get("type") == "text")
                tui._add_text(f"▸ you {c}", style="bold cyan")
            elif m.role == "assistant":
                tui._add_text(f"  {m.content[:200]}", style="blue")

    tui = CoderioTUI(on_input=on_input, show_tool_output=cfg.cli.show_tool_output, banner=banner)
    tui.run()
