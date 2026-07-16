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
    #history Static { height: auto; }
    #input-bar { height: auto; dock: bottom; border-top: solid $accent; }
    #input-bar Input { border: none; }
    /* Collapsible thinking blocks */
    Collapsible { border: round $boost 50%; margin: 0 0 0 0; }
    Collapsible > .collapsible__title { color: $text-muted; }
    /* NOTE: do NOT define `Screen { layers }` here. Defining layers changes how
       Textual computes the scrollable region's rendering — the bottom rows of
       scrolled content stop rendering when layers are active (the long-output
       truncation bug). The CommandMenu popup uses display:none/block for
       show/hide and dock:bottom for positioning, so it does NOT need layers. */
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
        # RENDER QUEUE: the agent's background thread pushes render instructions
        # here (thread-safe deque append/popleft). A main-thread set_interval
        # timer drains the queue and executes the instructions on the main thread.
        # This REPLACES call_from_thread entirely — which was unreliable in a real
        # terminal (callbacks queued by on_token/on_finish never executed, verified
        # via diagnostic logging: streamed=1228 chars but live_output/finalize had
        # ZERO log entries). The deque + timer pattern is the same proven approach
        # used by the StatusBar heartbeat (statusbar.log: 85k+ reliable renders).
        import collections
        self._render_q: collections.deque = collections.deque()
        self._live_out_widget: Static | None = None  # the live output Static (main thread)

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
        # Render-queue drain timer: runs on the MAIN thread (set_interval), pops
        # all queued render instructions and executes them. This is the ONLY path
        # from background-thread data to main-thread widgets — no call_from_thread.
        self.set_interval(0.06, self._drain_render_queue)

    def _drain_render_queue(self) -> None:
        """MAIN THREAD (set_interval): drain the render queue and execute all
        pending instructions, then scroll to bottom.

        Two scroll strategies depending on what was rendered:
          - STREAMING updates (text/think_update): fire ONE immediate scroll_end
            via call_after_refresh. These fire every ~60ms during output; scheduling
            3 delayed scroll timers per update caused timer pile-up and the
            visible jitter/flicker (scroll → relayout → scroll repeating).
          - FINAL render (finalize/static/panel/think_fold): these mount new widgets
            whose height needs multiple layout passes to settle, so use the
            multi-stage delayed scroll (0.15/0.3/0.5s).
        """
        did_streaming = False
        did_final = False
        while self._render_q:
            action, *args = self._render_q.popleft()
            try:
                if action == "text":
                    self._render_live_output(args[0])
                    did_streaming = True
                elif action == "finalize":
                    self._render_finalize(*args)
                    did_final = True
                elif action == "think_start":
                    self._render_think_start(args[0])
                    did_streaming = True
                elif action == "think_update":
                    self._render_think_update(args[0])
                    did_streaming = True
                elif action == "think_fold":
                    self._render_think_fold(*args)
                    did_final = True
                elif action == "static":
                    self._add_text_main(args[0], args[1] if len(args) > 1 else "")
                    did_final = True
                elif action == "panel":
                    self._add_static_main(args[0])
                    did_final = True
                elif action == "exit":
                    self.exit()
            except Exception:
                pass
        # Scroll strategy: streaming = single immediate scroll (no timer pile-up);
        # final = multi-stage delayed scroll (large Panels need layout passes).
        if did_final:
            try:
                self.set_timer(0.15, self._scroll_history_end)
                self.set_timer(0.3, self._scroll_history_end)
                self.set_timer(0.5, self._scroll_history_end)
            except Exception:
                pass
        elif did_streaming:
            try:
                # Single deferred scroll — runs after this tick's layout settles,
                # but doesn't pile up like 3 delayed timers would over 60ms cycles.
                self.call_after_refresh(self._scroll_history_end)
            except Exception:
                pass

    # ----------------------------------------------------- render methods (MAIN THREAD, called by _drain_render_queue)
    def _scroll_history_end(self) -> None:
        """Scroll the history pane to the bottom."""
        import os
        try:
            h = self.query_one("#history", VerticalScroll)
            h.scroll_end(animate=False)
            if os.environ.get("CODERIO_DEBUG"):
                from pathlib import Path as _P
                children = list(h.children)
                child_info = ", ".join(f"{type(c).__name__}(h={c.virtual_size.height})" for c in children)
                with open(_P.home() / ".coderio" / "statusbar.log", "a", encoding="utf-8") as f:
                    f.write(f"scroll_end: scroll_y={h.scroll_y:.0f} virtual={h.virtual_size.height} "
                            f"content={h.content_size.height} children=[{child_info}]\n")
        except Exception:
            pass

    def _render_live_output(self, full_text: str) -> None:
        """MAIN THREAD: create or update the live streaming output widget.

        Uses layout=True on update (the text grows, so the widget's height must
        recompute for new lines to show). The jitter fix is NOT here — it's in
        _drain_render_queue, which now uses a single immediate scroll for
        streaming updates instead of 3 delayed timers that piled up and caused
        scroll→relayout→scroll flicker."""
        if self._live_out_widget is None:
            self._live_out_widget = Static(Text(full_text))
            self._mount_widget_main(self._live_out_widget)
        else:
            self._live_out_widget.update(Text(full_text))

    def _render_think_start(self, full_text: str) -> None:
        """MAIN THREAD: mount the live (expanded) thinking block."""
        self._live_think_body = Static(Text(full_text))
        col = Collapsible(self._live_think_body, title="💭 思考中…",
                          collapsed=False, collapsed_symbol="▶", expanded_symbol="▼",
                          classes="think-block")
        self._last_collapsible = col
        self._mount_widget_main(col)

    def _render_think_update(self, full_text: str) -> None:
        """MAIN THREAD: update the live thinking body."""
        if self._live_think_body is not None:
            self._live_think_body.update(Text(full_text))

    def _render_think_fold(self, text: str, secs: float, had_live: bool) -> None:
        """MAIN THREAD: fold the thinking Collapsible."""
        chars = len(text)
        title = f"💭 思考 · {secs:.1f}s · {chars} 字 · Ctrl+O / 点击展开"
        if had_live and self._last_collapsible is not None:
            self._last_collapsible.title = title
            self._last_collapsible.collapsed = True
        else:
            body = Static(Text(text))
            col = Collapsible(body, title=title, collapsed=True,
                              collapsed_symbol="▶", expanded_symbol="▼", classes="think-block")
            self._last_collapsible = col
            self._mount_widget_main(col)

    def _render_finalize(self, buf: str, think_text: str, secs: float, had_live: bool) -> None:
        """MAIN THREAD: fold thinking + replace live output with final Markdown Panel.

        The final answer is PRE-RENDERED to plain text at the history container's
        inner width, then mounted as a Static string — NOT as Static(Panel(Markdown)).

        Why: the previous Static(Panel(Markdown(buf))) path relied on Textual's
        deferred RichVisual height calc (get_height), which on the user's wide
        VSCode terminal measured the widget at ~2x its true rendered height
        (widget_vh=122 while the rendered content was ~61 rows). The layout then
        thought the widget was 122 rows tall, scrolled to y=200, and showed the
        EMPTY region (rows 61-122) instead of the content — the bottom border and
        tail were 'below' a phantom empty area. Pre-rendering to text at a known
        width makes the Static's measured height EXACTLY match its rendered lines,
        so scroll_end lands on the real content bottom.
        """
        if think_text.strip():
            self._render_think_fold(think_text, secs, had_live)
        if buf.strip():
            # Step 1: remove the live raw-text widget (this tick)
            if self._live_out_widget is not None:
                try:
                    self._live_out_widget.remove()
                except Exception:
                    pass
                self._live_out_widget = None
            # Step 2: mount in the NEXT layout cycle (after removal processed).
            self.call_after_refresh(self._mount_final_panel, buf)

    def _mount_final_panel(self, buf: str) -> None:
        """MAIN THREAD: mount the final Markdown Panel and scroll to the bottom.

        Simple and correct: mount Static(Panel(Markdown(buf))). The long-output
        truncation bug (bottom rows not rendering after scroll) was NOT caused by
        the Panel, by height measurement, or by Conpty — it was caused by the
        `Screen { layers: base above }` CSS definition, which changes how Textual
        computes the scrollable region's rendering: with layers active, the
        bottom rows of scrolled-to-the-end content stop rendering (they render
        as blank). Removing the layers definition fixes it completely. Verified:
        sentinel text + Panel bottom border both visible after scroll_end, on
        both narrow (80) and wide (214) terminals.
        """
        history = self.query_one("#history")
        widget = Static(Panel(Markdown(buf), border_style="blue", title="coderio"))
        history.mount(widget)
        self.set_timer(0.15, self._scroll_history_end)
        self.set_timer(0.3, self._scroll_history_end)
        self.set_timer(0.5, self._scroll_history_end)

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
            # Use a Textual WORKER (thread=True), not a raw threading.Thread.
            # The core bug: raw daemon threads are invisible to Textual's event
            # loop. When the agent thread finishes, call_from_thread callbacks it
            # queued (final output Panel, thinking fold, etc.) sit UNPROCESSED in
            # the loop's queue — they only get drained on the next user input
            # (the 'content appears on next message' bug). A worker is managed by
            # Textual: the main loop stays alive and processes the queue while the
            # worker runs AND after it completes, so pending UI updates land.
            def _run():
                try:
                    self._on_input(line)
                except SystemExit:
                    self._render_q.append(("exit",))
                except Exception as e:
                    self._render_q.append(("static", f"运行错误: {type(e).__name__}: {e}", "red"))
            self.run_worker(_run, thread=True, exclusive=True,
                            name="agent_turn", exit_on_error=False)

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
    # ALL callbacks run on the agent's BACKGROUND thread. They ONLY push render
    # instructions to self._render_q (a thread-safe deque). The main-thread timer
    # _drain_render_queue (set_interval 60ms) pops and executes them. NO
    # call_from_thread anywhere — it was the root cause of content not rendering.
    def on_step_start(self, step: int = 1) -> None:
        self._flush_round_thinking()
        self._set_phase("thinking", step=step)

    def on_token(self, text: str) -> None:
        self._flush_round_thinking()
        bar = self._status_bar
        if bar is None or bar.phase != "responding":
            self._set_phase("responding")
        # Accumulate in buffer (agent thread). Push a "text" render instruction
        # with the FULL buffer so the main thread can update the live widget.
        # Throttle: only push at most once per ~60ms to avoid flooding the queue.
        self.buffer += text
        now = time.monotonic()
        if self._live_output_last_flush == 0.0 or (now - self._live_output_last_flush) >= 0.06:
            self._live_output_last_flush = now
            self._render_q.append(("text", self.buffer))

    def on_thinking(self, text: str) -> None:
        if not self._round_thinking:
            self._round_think_start = time.monotonic()
        self._round_thinking += text
        now = time.monotonic()
        if self._live_think_body is None:
            # First chunk: push a think_start
            self._live_think_chars = len(self._round_thinking)
            self._render_q.append(("think_start", self._round_thinking))
            self._live_output_last_flush = now  # reuse throttle timer for thinking
        else:
            delta_len = len(self._round_thinking) - self._live_think_chars
            if delta_len > 0 and (now - self._live_output_last_flush) >= 0.06:
                self._live_think_chars = len(self._round_thinking)
                self._live_output_last_flush = now
                self._render_q.append(("think_update", self._round_thinking))

    def on_tool_start(self, name: str, args: dict[str, Any], step: int = 1,
                      tool_index: int = 0, tool_total: int = 0) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        self._set_phase("tool", tool_name=name, step=step,
                        tool_index=tool_index, tool_total=tool_total)
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        if len(args_str) > 100:
            args_str = args_str[:100] + "…"
        self._render_q.append(("static", f"⏺ {name}({args_str})", "green"))

    def on_tool_end(self, name: str, result: str) -> None:
        self._set_phase("thinking")
        if not self.show_tool_output:
            first = result.splitlines()[0][:60] if result.splitlines() else ""
            self._render_q.append(("static", f"  → {first}{'…' if len(result) > 60 else ''}", "dim"))
            return
        lines = result.splitlines()
        shown = "\n".join(lines[:3])
        if len(lines) > 3:
            shown += f"\n…({len(lines) - 3} more lines)"
        self._render_q.append(("static", shown, "dim"))

    def on_truncated(self, stop_reason: str) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        self._render_q.append(("panel", Panel(
            f"⚠ 输出被截断 (stop_reason: {stop_reason})。",
            title="截断警告", border_style="yellow")))

    def on_harness_warn(self, message: str) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        self._render_q.append(("panel", Panel(
            f"⚠ {message}\n\n产出可能未经验证，请人工复核。",
            title="⚠ harness 警告", border_style="red")))

    def on_finish(self) -> None:
        # Capture everything remaining and push ONE finalize instruction.
        # The main-thread drain will fold thinking + mount the final Markdown Panel.
        think_text = self._round_thinking
        secs = time.monotonic() - self._round_think_start if self._round_think_start else 0.0
        had_live = self._live_think_body is not None
        buf = self.buffer
        # Reset accumulated state.
        self._round_thinking = ""
        self._round_think_start = 0.0
        self._live_think_body = None
        self._live_think_chars = 0
        self.buffer = ""
        self._live_output_last_flush = 0.0
        self._render_q.append(("finalize", buf, think_text, secs, had_live))
        if self._status_bar:
            self._status_bar.set_phase("idle")

    def add_usage(self, meta: dict[str, int]) -> None:
        for k in ("input_tokens", "output_tokens"):
            if k in meta:
                self.usage[k] += meta[k]

    # ----------------------------------------------------- thinking fold (true fold/unfold)
    def _flush_round_thinking(self) -> None:
        """Push a think_fold instruction to the render queue (agent thread)."""
        if not self._round_thinking.strip():
            return
        text = self._round_thinking
        secs = time.monotonic() - self._round_think_start if self._round_think_start else 0.0
        had_live = self._live_think_body is not None
        self._round_thinking = ""
        self._round_think_start = 0.0
        self._live_think_body = None
        self._live_think_chars = 0
        self._render_q.append(("think_fold", text, secs, had_live))

    def show_last_thinking(self) -> bool:
        """Expand the most recent thinking (compat with /think command)."""
        if self._last_collapsible is None:
            self._add_text("最近一轮没有思考内容。", style="dim")
            return False
        self._last_collapsible.collapsed = False  # expand
        return True

    # ----------------------------------------------------- helpers: add content to history
    def _flush_buffer(self) -> None:
        """Push the accumulated output to the render queue as a Panel instruction."""
        if self.buffer.strip():
            self._render_q.append(("panel", Panel(Markdown(self.buffer), border_style="blue", title="coderio")))
        self.buffer = ""

    def _add_text(self, text: str, style: str = "") -> None:
        """Push a text line to the render queue."""
        self._render_q.append(("static", text, style))

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
