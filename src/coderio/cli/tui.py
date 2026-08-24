"""Textual-based TUI for coderio — foldable thinking, scrollable history.

Implements the StreamHandler protocol (so agent/tools/harness call it
identically to the Rich version). Each round's thinking is rendered as a
Collapsible widget — collapsed by default, expandable via Ctrl+O (toggles the
most recent) or mouse click on the title. This gives true fold/unfold, which
RichLog (append-only) could not.

The agent runs in a background thread; UI updates flow through a thread-safe
render queue drained by a main-thread timer (see _drain_render_queue).
"""

from __future__ import annotations

import time
from typing import Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Button, Collapsible, Input, RichLog, Static

# Single-source version (read from pyproject.toml via importlib.metadata).
from coderio import __version__  # noqa: E402

# OnboardingScreen, _OnboardingApp, and _run_onboarding_tui have been extracted
# to tui_onboarding.py for modularity.
from coderio.cli.tui_onboarding import OnboardingScreen, _run_onboarding_tui  # noqa: E402

# The three modal picker screens (Profile / Mode / Session) live in tui_screens.py.
from coderio.cli.tui_screens import (  # noqa: E402
    ModePickerScreen,
    ProfilePickerScreen,
    SessionPickerScreen,
)

# CommandMenu, ConfirmMenu, StatusBar and _TASK_PHASE_LABELS have been extracted
# to tui_widgets.py for modularity.
from coderio.cli.tui_widgets import (  # noqa: E402
    CommandMenu,
    ConfirmMenu,
    StatusBar,
)


class CoderioTUI(App):
    """coderio's Textual app. Implements StreamHandler (duck-typed)."""

    CSS = """
    Screen { layout: vertical; }
    #history { border: round $accent; height: 1fr; min-height: 10; padding: 0 1; }
    #history Static { height: auto; }
    #input-bar { height: auto; dock: bottom; border-top: solid $accent; }
    #input-bar Input { border: none; }
    #status-row { height: auto; }
    /* interrupt-btn: NO border — a border adds 2 rows to the widget's outer
       height, which makes #status-row (height:auto, takes max child height)
       grow from 1 to 2 rows, leaving a black gap between the status bar and
       the input whenever the agent is running. Use a tinted background instead
       so the button stays exactly 1 row tall, matching the StatusBar. */
    #interrupt-btn {
        display: none; height: 1; min-width: 8; padding: 0 1;
        /* Explicitly kill the border — Button(variant="error") injects a 'tall'
           border by default, which adds 2 rows to the outer height and makes
           #status-row grow, leaving a black gap while the agent runs. */
        border: none;
        background: $error 20%;
        color: $error;
        text-style: bold;
    }
    /* interrupt-btn is shown only when the agent is running (via add_class). */
    #interrupt-btn.-visible { display: block; }
    /* Collapsible thinking blocks */
    Collapsible { border: round $boost 50%; margin: 0 0 0 0; }
    Collapsible > .collapsible__title { color: $text-muted; }
    /* NOTE: do NOT define `Screen { layers }` here — it changes how Textual
       renders the scrollable region (bottom rows of scrolled content stop
       rendering). The CommandMenu popup uses display:none/block for show/hide
       and dock:bottom for positioning, so it does NOT need layers. */
    """

    BINDINGS = [
        Binding("ctrl+o", "toggle_thinking", "展开/收起思考", show=True),
        Binding("escape", "interrupt", "中断任务", show=True),
        Binding("ctrl+c", "interrupt", "中断任务", show=False),
    ]

    name = "textual_tui"

    def __init__(
        self,
        on_input=None,
        show_tool_output: bool = True,
        banner: str | None = None,
        extra_completions: list[str] | None = None,
    ) -> None:
        super().__init__()
        self._on_input = on_input
        self.show_tool_output = show_tool_output
        self._banner = banner
        # Custom command completions (/name form), discovered by the caller at
        # startup — compose() runs later and has no project context of its own.
        self._extra_completions = extra_completions or []
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
        # Agent-thread-local flag: has a think_start already been queued for the
        # CURRENT round? `_live_think_body` is set by the MAIN thread 60ms later
        # (next _drain_render_queue tick), so reading it from the agent thread is
        # a race — within one drain window many on_thinking chunks arrive before
        # the main thread has had a chance to mount the Collapsible, and each one
        # would otherwise queue a SEPARATE think_start, fragmenting one continuous
        # thinking stream into N tiny Collapsibles ("The" / "The user is" / ...).
        # This flag is owned entirely by the agent thread (set in on_thinking,
        # cleared in _flush_round_thinking / on_finish), so there's no race.
        self._round_think_started: bool = False
        # Live output: a RichLog widget for streaming the answer as it arrives.
        # RichLog is append-only — each token chunk is written once, avoiding the
        # full-widget layout recompute that Static.update(Text(full_buffer)) would
        # trigger on every batch. On finish, the RichLog is replaced by the final
        # Markdown Panel.
        self._live_output: Static | None = None
        self._live_output_chars = 0
        self._live_output_last_flush: float = 0.0  # throttle: only flush >=80ms apart
        # Cached StatusBar reference: query_one() is a main-thread DOM query, but
        # _set_phase runs on the agent's BACKGROUND thread. Cache the widget once
        # (on_mount, main thread) and read the plain attribute thereafter
        # (GIL-safe from any thread).
        self._status_bar: StatusBar | None = None
        # Interrupt support: when the user clicks "中断" or presses Esc during
        # an agent turn, _interrupted is set. The agent checks it between rounds
        # (via stream.is_interrupted()) and exits gracefully. The worker ref is
        # held so we can also call .cancel() as a backup.
        self._interrupted: bool = False
        self._agent_worker = None
        self._is_running: bool = False  # True while the agent worker is active
        # Inline confirmation state: when _confirm_event is non-None, the
        # agent thread is blocked waiting for the user to allow/deny a write.
        self._confirm_event = None
        self._confirm_result: bool | str = False
        self._confirm_custom_mode = False  # True when user clicked "其他"
        # RENDER QUEUE: the agent's background thread pushes render instructions
        # here (thread-safe deque append/popleft). A main-thread set_interval
        # timer drains the queue and executes the instructions on the main thread.
        # This avoids call_from_thread, whose callbacks are not reliably delivered
        # in a real terminal. The deque + timer pattern matches the StatusBar
        # heartbeat approach.
        import collections

        self._render_q: collections.deque = collections.deque()
        self._live_out_widget: RichLog | None = None  # streaming output RichLog (main thread)
        self._live_rendered_len: int = 0  # chars already written to the RichLog
        # Dynamic TODO widget: mounted once on first write_todos, updated
        # in-place on subsequent calls (Claude Code style). Reset to None on
        # on_finish so the next turn gets a fresh widget.
        self._todo_widget: Static | None = None

    # ----------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        from coderio.cli.commands import slash_completions

        yield VerticalScroll(id="history")
        # input-bar holds the CommandMenu + StatusBar + Input. CommandMenu lives
        # INSIDE the bar (not as a separate dock:bottom sibling) so that when it
        # expands it pushes the bar's contents up as a unit — no overlap with
        # the StatusBar (which was hiding "就" in "就绪") and no lost bottom
        # border. The bar is dock:bottom; #history is 1fr and shrinks to fit.
        with Vertical(id="input-bar"):
            yield CommandMenu(slash_completions(self._extra_completions))
            with Horizontal(id="status-row"):
                yield StatusBar()
                yield Button("⏹ 中断", id="interrupt-btn", variant="error")
            # Vertical permission-confirmation menu (zcode/codex style): floats
            # above the input box, ↑↓ to choose, Enter to confirm. Replaces the
            # old three-button #confirm-row whose Button borders inflated the
            # layout and left a black gap.
            yield ConfirmMenu()
            yield Input(
                placeholder="输入消息, /help 看命令, Esc 中断任务",
                id="msg",
            )

    def on_mount(self) -> None:
        self.title = "coderio"
        self.sub_title = "skill-driven coding agent"
        if self._banner:
            self._add_static(
                Panel(
                    self._banner,
                    title="[bold magenta]coderio[/bold magenta]",
                    border_style="magenta",
                )
            )
        inp = self.query_one("#msg", Input)
        # Wire the popup command menu to the input.
        self.query_one(CommandMenu).bind_input(inp)
        # Cache the StatusBar reference NOW (main thread) — _set_phase runs on
        # the agent's background thread where query_one() can't run.
        self._status_bar = self.query_one(StatusBar)
        inp.focus()
        # Render-queue drain timer: runs on the MAIN thread (set_interval), pops
        # all queued render instructions and executes them. This is the only path
        # from background-thread data to main-thread widgets.
        self.set_interval(0.06, self._drain_render_queue)

    # ----------------------------------------------------- cross-thread confirmation
    def request_confirmation(self, tool_name: str, args: dict) -> bool | str:
        """Ask the user to allow/deny/custom-respond to a write operation.

        Called from the AGENT's background thread. Shows an inline confirmation
        row with three options: ✓ 允许 / ✗ 拒绝 / ✎ 其他.
        - 允许 → True (execute the tool)
        - 拒绝 → False (block, "Permission denied")
        - 其他 → user types free text → str (block, but feed user's instruction
          to the model as a tool result so it can adjust)

        The "其他" mode hides the buttons and turns #msg into a custom-reply
        input. The user types their instruction and presses Enter to submit.
        """
        import threading

        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        if len(args_str) > 120:
            args_str = args_str[:120] + "…"
        self._confirm_event = threading.Event()
        self._confirm_result: bool | str = False
        self._confirm_custom_mode = False

        def _show():
            try:
                menu = self.query_one(ConfirmMenu)
                menu.show(tool_name, args_str)
            except Exception:
                pass

        def _hide():
            try:
                menu = self.query_one(ConfirmMenu)
                menu.hide()
                # Reset #msg placeholder if it was in custom mode.
                inp = self.query_one("#msg", Input)
                inp.placeholder = "输入消息, /help 看命令, Esc 中断任务"
            except Exception:
                pass

        self.call_from_thread(_show)
        self._confirm_event.wait(timeout=120)
        self.call_from_thread(_hide)
        self._confirm_event = None
        self._confirm_custom_mode = False
        return self._confirm_result

    def _resolve_confirmation(self, result: bool | str) -> None:
        """MAIN THREAD: resolve the pending confirmation and wake the agent."""
        self._confirm_result = result
        if self._confirm_event is not None:
            self._confirm_event.set()

    def _enter_custom_mode(self) -> None:
        """MAIN THREAD: switch the input bar to custom-reply mode for '自定义回复'."""
        self._confirm_custom_mode = True
        try:
            # Hide the menu, turn #msg into a custom-reply input.
            self.query_one(ConfirmMenu).hide()
            inp = self.query_one("#msg", Input)
            inp.placeholder = "输入自定义回复，回车提交..."
            inp.value = ""
            inp.focus()
        except Exception:
            pass

    def _drain_render_queue(self) -> None:
        """MAIN THREAD (set_interval): drain the render queue and execute all
        pending instructions, then scroll to bottom.

        Dispatch is a dict lookup (self._RENDER_DISPATCH) mapping action name
        to a handler method. Each handler returns a scroll category:
          - "streaming": lightweight live update → single deferred scroll.
          - "final": new widget mounted → multi-stage delayed scroll (layout
            passes need time to settle on large Panels).
          - "none": no scroll trigger (clear_live, exit).

        This replaces a 9-branch if/elif chain (cyclomatic complexity ~18)
        with a flat table lookup — same behavior, far easier to extend (add a
        new render action by adding one dict entry + one method).
        """
        did_streaming = False
        did_final = False
        while self._render_q:
            action, *args = self._render_q.popleft()
            handler = self._RENDER_DISPATCH.get(action)
            if handler is None:
                continue
            try:
                category = handler(self, args)
                if category == "streaming":
                    did_streaming = True
                elif category == "final":
                    did_final = True
            except Exception:
                pass
        # Scroll strategy: streaming = single deferred scroll; final = multi-stage
        # delayed scroll (large Panels need multiple layout passes to settle).
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
                # without piling up timers across 60ms cycles.
                self.call_after_refresh(self._scroll_history_end)
            except Exception:
                pass

    # --- render-action handlers (each returns "streaming"|"final"|"none") ---
    # Kept as small staticmethod-like functions so the dispatch table can
    # reference them without constructing lambdas on every drain cycle.

    @staticmethod
    def _h_text(self, args):
        self._render_live_output(args[0])
        return "streaming"

    @staticmethod
    def _h_finalize(self, args):
        self._render_finalize(*args)
        return "final"

    @staticmethod
    def _h_think_start(self, args):
        self._render_think_start(args[0])
        return "streaming"

    @staticmethod
    def _h_think_update(self, args):
        self._render_think_update(args[0])
        return "streaming"

    @staticmethod
    def _h_think_fold(self, args):
        self._render_think_fold(*args)
        return "final"

    @staticmethod
    def _h_static(self, args):
        self._add_text_main(args[0], args[1] if len(args) > 1 else "")
        return "final"

    @staticmethod
    def _h_panel(self, args):
        self._add_static_main(args[0])
        return "final"

    @staticmethod
    def _h_clear_live(self, args):
        if self._live_out_widget is not None:
            try:
                self._live_out_widget.remove()
            except Exception:
                pass
            self._live_out_widget = None
            self._live_rendered_len = 0
        return "none"

    @staticmethod
    def _h_exit(self, args):
        self.exit()
        return "none"

    @staticmethod
    def _h_todo_update(self, args):
        """Render todos as a dynamic checklist in the output area (main thread).

        Claude Code style: a SINGLE checklist widget is mounted on first
        write_todos, then updated in-place on subsequent calls. Tool output
        (edit_file, execute, etc.) appears BELOW it. The widget stays in
        history as a record of the task's progress.
        """
        todos = args[0] if args else []
        if not todos:
            return "none"
        done = sum(1 for t in todos if t.get("status") == "completed")
        total = len(todos)
        lines = [f"**任务清单 ({done}/{total})**"]
        for t in todos:
            status = t.get("status", "pending")
            content = t.get("content", "")
            if status == "completed":
                lines.append(f"- [x] {content}")
            elif status == "in_progress":
                lines.append(f"- [ ] {content} ←")
            else:
                lines.append(f"- [ ] {content}")
        text = "\n".join(lines)
        panel = Panel(Markdown(text), title="📝 任务清单", border_style="cyan")

        if self._todo_widget is not None:
            # Update existing widget in-place (no new mount).
            try:
                self._todo_widget.update(panel)
                return "none"
            except Exception:
                pass  # widget was removed (scrolled off) → mount a new one
        # First call or widget lost → mount new.
        from textual.widgets import Static

        self._todo_widget = Static(panel)
        self._add_static_main(panel)
        return "final"

    # Dispatch table: action name -> handler. Built once at class definition.
    # Handlers are staticmethods taking (self, args) so they can live in the
    # table without binding overhead.
    _RENDER_DISPATCH = {
        "text": _h_text,
        "finalize": _h_finalize,
        "think_start": _h_think_start,
        "think_update": _h_think_update,
        "think_fold": _h_think_fold,
        "static": _h_static,
        "panel": _h_panel,
        "clear_live": _h_clear_live,
        "exit": _h_exit,
        "todo_update": _h_todo_update,
    }

    # ----------------------------------------------------- render methods (MAIN THREAD, called by _drain_render_queue)
    def _scroll_history_end(self) -> None:
        """Scroll the history pane to the bottom."""
        try:
            h = self.query_one("#history", VerticalScroll)
            h.scroll_end(animate=False)
        except Exception:
            pass

    def _clear_history(self) -> None:
        """Wipe all widgets from the history pane (used by /clear).

        The old session's jsonl stays on disk — this only clears what's visible
        on screen. Must run on the main thread (touches the DOM).
        """
        try:
            h = self.query_one("#history", VerticalScroll)
            h.remove_children()
        except Exception:
            pass

    def _render_live_output(self, full_text: str) -> None:
        """MAIN THREAD: append the NEW part of the streaming text to a RichLog.

        RichLog is append-only: each call writes just the delta (the new chars
        since the last write), NOT the full buffer re-rendered. This avoids the
        layout recompute that Static.update(Text(full_buffer)) would trigger on
        every batch (re-laying-out the entire history)."""
        if self._live_out_widget is None:
            self._live_out_widget = RichLog(wrap=True, markup=False, auto_scroll=True)
            self._mount_widget_main(self._live_out_widget)
            self._live_rendered_len = 0
        # Write only the delta (new chars since last write).
        delta = full_text[self._live_rendered_len :]
        if delta:
            self._live_rendered_len = len(full_text)
            self._live_out_widget.write(delta)

    def _render_think_start(self, full_text: str) -> None:
        """MAIN THREAD: mount the live (expanded) thinking block."""
        self._live_think_body = Static(Text(full_text))
        col = Collapsible(
            self._live_think_body,
            title="💭 思考中…",
            collapsed=False,
            collapsed_symbol="▶",
            expanded_symbol="▼",
            classes="think-block",
        )
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
            col = Collapsible(
                body,
                title=title,
                collapsed=True,
                collapsed_symbol="▶",
                expanded_symbol="▼",
                classes="think-block",
            )
            self._last_collapsible = col
            self._mount_widget_main(col)

    def _render_finalize(self, buf: str, think_text: str, secs: float, had_live: bool) -> None:
        """MAIN THREAD: fold thinking + replace live output with final Markdown Panel.

        The final answer is rendered as Static(Panel(Markdown(buf))) (see
        _mount_final_panel). scroll_end lands on the real content bottom once the
        layout settles (note the layers caveat in the CSS).
        """
        if think_text.strip():
            self._render_think_fold(think_text, secs, had_live)
        if buf.strip():
            # Remove the streaming RichLog and replace with the final Markdown Panel.
            if self._live_out_widget is not None:
                try:
                    self._live_out_widget.remove()
                except Exception:
                    pass
                self._live_out_widget = None
                self._live_rendered_len = 0
            self.call_after_refresh(self._mount_final_panel, buf)

    def _mount_final_panel(self, buf: str) -> None:
        """MAIN THREAD: mount the final Markdown Panel and scroll to the bottom.

        Mounts Static(Panel(Markdown(buf))) and schedules a multi-stage delayed
        scroll (the Panel's height needs a few layout passes to settle).
        """
        history = self.query_one("#history")
        widget = Static(Panel(Markdown(buf), border_style="blue", title="coderio"))
        history.mount(widget)
        self.set_timer(0.15, self._scroll_history_end)
        self.set_timer(0.3, self._scroll_history_end)
        self.set_timer(0.5, self._scroll_history_end)

    # ----------------------------------------------------- status bar (phase routing)
    def _set_phase(
        self,
        phase: str,
        tool_name: str = "",
        step: int = 0,
        tool_index: int = 0,
        tool_total: int = 0,
    ) -> None:
        """Forward a phase change to the StatusBar widget.

        Called from the agent's BACKGROUND thread (StreamHandler callbacks). Do
        NOT use query_one here — it's a main-thread DOM query. Use the cached
        reference instead (plain attribute read, GIL-safe). StatusBar.set_phase
        also only writes plain attributes.
        """
        bar = self._status_bar
        if bar is None:
            return  # not mounted yet
        bar.set_phase(phase, tool_name, step=step, tool_index=tool_index, tool_total=tool_total)

    def _show_interrupt_btn(self, show: bool) -> None:
        """Show/hide the interrupt button. Safe from any thread."""
        # add_class/remove_class on a Button are plain attribute mutations
        # (GIL-safe). We don't need call_from_thread here — the CSS class
        # toggle is picked up by the next layout pass. This avoids the race
        # where call_from_thread is deferred until after the blocking agent
        # call returns (too late — the button never showed during the turn).
        try:
            btn = self.query_one("#interrupt-btn", Button)
            if show:
                btn.add_class("-visible")
            else:
                btn.remove_class("-visible")
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle button clicks (interrupt only — confirm is now a keyboard menu)."""
        if event.button.id == "interrupt-btn":
            self.action_interrupt()

    # ----------------------------------------------------- command menu (autocomplete)
    def on_input_changed(self, event: Input.Changed) -> None:
        """Live-filter the command menu as the user types in the main input."""
        if event.input.id != "msg":
            return
        self.query_one(CommandMenu).refresh_for(event.value)

    def on_key(self, event) -> None:
        """Handle command-menu navigation + inline confirmation keys."""
        # Custom-reply mode: Enter submits the user's text as a str result.
        if self._confirm_custom_mode:
            # Let normal input editing happen; only intercept Enter/Esc.
            if event.key == "enter":
                inp = self.query_one("#msg", Input)
                text = inp.value.strip()
                inp.value = ""
                self._resolve_confirmation(text if text else False)
                event.prevent_default()
                return
            if event.key == "escape":
                self._resolve_confirmation(False)
                event.prevent_default()
                return
            return  # let other keys type into the input
        # If the confirmation menu is visible, navigate it with ↑↓ + Enter.
        # (Esc cancels = deny.) This is the zcode/codex vertical-selection model.
        try:
            confirm_menu = self.query_one(ConfirmMenu)
            if confirm_menu.visible():
                if event.key == "up":
                    confirm_menu.move(-1)
                    event.prevent_default()
                    return
                if event.key == "down":
                    confirm_menu.move(1)
                    event.prevent_default()
                    return
                if event.key == "enter":
                    choice = confirm_menu.accept()
                    if choice == ConfirmMenu.ALLOW:
                        self._resolve_confirmation(True)
                    elif choice == ConfirmMenu.CUSTOM:
                        self._enter_custom_mode()
                    else:  # DENY or None (nothing selected)
                        self._resolve_confirmation(False)
                    event.prevent_default()
                    return
                if event.key == "escape":
                    self._resolve_confirmation(False)
                    event.prevent_default()
                    return
        except Exception:
            pass
        # Command-menu navigation (only when menu is visible).
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
        # CRITICAL: only handle the MAIN chat input. Input.Submitted bubbles up
        # the widget tree by default, so a submission in OnboardingScreen's
        # `#onboard-input` (API key / model / base_url fields) or any other
        # modal's Input would also land here and be dispatched to _on_input →
        # run_deep_agent → session.append, leaking sensitive fields like API keys
        # into the session jsonl. Observed in real sessions: a 64-char API
        # key was persisted as a user message. Guard by widget id — only `#msg`
        # is the chat input. (on_input_changed above already has this guard.)
        if event.input.id != "msg":
            return
        line = event.value.strip()
        if not line:
            return
        event.input.value = ""
        self._add_text(f"▸ you {line}", style="bold cyan")
        if self._on_input:
            # Use a Textual WORKER (thread=True), not a raw threading.Thread. A
            # worker is managed by Textual, so the main event loop stays alive and
            # keeps draining pending UI updates while the worker runs AND after it
            # completes — a raw daemon thread would not.
            def _run():
                self._is_running = True
                self._interrupted = False
                self.call_from_thread(self._show_interrupt_btn, True)
                try:
                    self._on_input(line)
                except SystemExit:
                    self._render_q.append(("exit",))
                except InterruptedError:
                    # User pressed Esc / clicked 中断. Show a notice and clean up.
                    self._round_thinking = ""
                    self._round_think_start = 0.0
                    self._live_think_body = None
                    self._live_think_chars = 0
                    self._round_think_started = False
                    self.buffer = ""
                    self._live_output_last_flush = 0.0
                    if self._status_bar:
                        self._status_bar.set_phase("idle")
                    self._render_q.append(
                        (
                            "panel",
                            Panel(
                                "用户已中断当前任务。已完成的中间结果保留在历史中。\n"
                                "输入新消息继续，或按 ↑ 恢复上一条输入。",
                                title="⚠ 已中断",
                                border_style="yellow",
                            ),
                        )
                    )
                except Exception as e:
                    # Reset the streaming state + status bar so the TUI doesn't
                    # get stuck in 'thinking' phase when the agent errors out
                    # (e.g. API auth failure, network error). on_finish is never
                    # called when run_deep_agent raises, so we must clean up here.
                    self._round_thinking = ""
                    self._round_think_start = 0.0
                    self._live_think_body = None
                    self._live_think_chars = 0
                    self._round_think_started = False
                    self.buffer = ""
                    self._live_output_last_flush = 0.0
                    if self._status_bar:
                        self._status_bar.set_phase("idle")
                    self._render_q.append(
                        (
                            "panel",
                            Panel(
                                Text(f"⚠ {type(e).__name__}: {e}\n\n你的输入已保留在输入框，按 Enter 可重试。"),
                                title="⚠ 运行错误",
                                border_style="red",
                            ),
                        )
                    )

                    def _refill_input():
                        try:
                            inp = self.query_one("#msg", Input)
                            inp.value = line
                            inp.focus()
                        except Exception:
                            pass

                    self.call_from_thread(_refill_input)
                finally:
                    self._is_running = False
                    self.call_from_thread(self._show_interrupt_btn, False)

            self._agent_worker = self.run_worker(
                _run,
                thread=True,
                exclusive=True,
                name="agent_turn",
                exit_on_error=False,
            )

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

    # ----------------------------------------------------- binding: Esc = interrupt
    def action_interrupt(self) -> None:
        """Interrupt the currently-running agent turn (bound to Esc + Ctrl+C).

        Sets the _interrupted flag (checked by is_interrupted() between rounds)
        and cancels the worker as a backup. The agent thread sees the flag at
        the next safe checkpoint (start of a new ReAct round) and raises
        InterruptedError, which _run catches to show a '⚠ 已中断' panel.

        If no agent is running, this is a no-op (Esc does nothing instead of
        quitting the TUI — the old behavior was ctrl+c=quit which killed the
        app mid-task with no way to stop gracefully).
        """
        if not self._is_running:
            return  # nothing to interrupt
        self._interrupted = True
        # Cancel the worker as a backup — this unblocks subprocess.run calls
        # and model.stream() that might be waiting on I/O. The flag handles
        # the clean exit; cancel handles the "stuck in I/O" case.
        if self._agent_worker is not None:
            try:
                self._agent_worker.cancel()
            except Exception:
                pass

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
    # _drain_render_queue (set_interval 60ms) pops and executes them — no
    # call_from_thread here.
    def on_step_start(self, step: int = 1) -> None:
        self._flush_round_thinking()
        # Reset turn token counter at the start of each turn (step 1 = new turn).
        if step == 1:
            self.usage = {"input_tokens": 0, "output_tokens": 0}
            if self._status_bar:
                self._status_bar.set_turn_tokens(0)
        self._set_phase("thinking", step=step)

    def is_interrupted(self) -> bool:
        """Check if the user requested an interrupt (agent thread calls this).

        Called between ReAct rounds by _execute_turn. When True, the turn ends
        gracefully — the user gets whatever partial output exists, plus a
        '⚠ 已中断' panel. The agent thread does NOT raise or crash; it just
        stops looping and returns.
        """
        return self._interrupted

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
        # Decide think_start vs think_update using an AGENT-THREAD-LOCAL flag,
        # NOT `_live_think_body`. The latter is set by the main thread only after
        # the next _drain_render_queue tick (60ms away), so reading it here is a
        # race: within one drain window many on_thinking chunks arrive, each one
        # would re-enter the "first chunk" branch and queue another think_start,
        # fragmenting one continuous thinking stream into many tiny Collapsibles.
        if not self._round_think_started:
            # First chunk of this round: queue think_start with the FULL text so
            # far, and mark the round as started. The main thread will mount ONE
            # Collapsible; subsequent chunks queue think_update against that same
            # widget.
            self._round_think_started = True
            self._live_think_chars = len(self._round_thinking)
            self._render_q.append(("think_start", self._round_thinking))
            self._live_output_last_flush = now  # reuse throttle timer for thinking
        else:
            delta_len = len(self._round_thinking) - self._live_think_chars
            if delta_len > 0 and (now - self._live_output_last_flush) >= 0.06:
                self._live_think_chars = len(self._round_thinking)
                self._live_output_last_flush = now
                self._render_q.append(("think_update", self._round_thinking))

    def on_tool_start(
        self,
        name: str,
        args: dict[str, Any],
        step: int = 1,
        tool_index: int = 0,
        tool_total: int = 0,
    ) -> None:
        self._flush_round_thinking()
        self._flush_buffer()
        self._set_phase(
            "tool",
            tool_name=name,
            step=step,
            tool_index=tool_index,
            tool_total=tool_total,
        )
        # Special-case the `task` tool (subagent delegation): show a friendly
        # notice instead of the raw (very long) args. The subagent runs
        # synchronously inside the tools node — the main agent blocks until it
        # finishes, which can take minutes. Without this notice the user sees
        # a frozen "执行 task(…)" with no indication that a subagent is working.
        if name == "task":
            subagent = args.get("subagent_type", "general-purpose")
            desc = args.get("description", "") or args.get("instructions", "")
            desc_short = desc.split("\n")[0][:80] if desc else ""
            self._render_q.append(("static", f"🔄 委派子 agent [{subagent}]：{desc_short}…（执行中，请稍候）", "cyan"))
            return
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        if len(args_str) > 100:
            args_str = args_str[:100] + "…"
        self._render_q.append(("static", f"⏺ {name}({args_str})", "green"))

    # Tools that modify files — shown with a prominent yellow line so the user
    # always knows what changed, even in auto mode (where there's no confirmation).
    _WRITE_TOOLS: frozenset[str] = frozenset({"write_file", "edit_file", "multi_edit"})

    def on_tool_end(self, name: str, result: str) -> None:
        self._set_phase("thinking")
        # Write tools get a prominent yellow line so the user sees what was
        # modified — matching the "always show file changes" UX of claude code /
        # zcode. Other tools keep the existing dim/grey output.
        if name in self._WRITE_TOOLS and not result.startswith(("Error", "Permission denied")):
            self._render_q.append(
                ("static", f"  📝 {result.splitlines()[0] if result.splitlines() else name}", "yellow bold")
            )
            return
        if name == "_empty_response":
            # Empty-response exhaustion is a hard interruption, not a normal
            # tool result — show it as a red panel (like on_harness_warn) so
            # it's visible instead of buried in dim grey text.
            self._flush_round_thinking()
            self._flush_buffer()
            self._render_q.append(
                (
                    "panel",
                    Panel(
                        Text(f"⚠ {result}\n\n建议用 /clear 清理上下文后重试，或检查模型状态。"),
                        title="⚠ 会话中断",
                        border_style="red",
                    ),
                )
            )
            return
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
        self._render_q.append(
            (
                "panel",
                Panel(
                    Text(f"⚠ 输出被截断 (stop_reason: {stop_reason})。"),
                    title="截断警告",
                    border_style="yellow",
                ),
            )
        )

    def on_harness_warn(self, message: str) -> None:
        """Escalation release: the harness allowed a sketchy answer through.

        IMPORTANT: do NOT call _flush_buffer() here. on_harness_warn is always
        followed by on_finish (see loop.py), which renders self.buffer as the
        final blue 'coderio' Panel. Flushing here would instead render it as a
        cyan '中间输出' Panel AND clear the buffer, so on_finish would have
        nothing left to show — the model's real final answer would appear as
        misleading intermediate output (the exact bug we fixed). Just clear the
        live streaming widget so on_finish can mount the final Panel cleanly."""
        self._flush_round_thinking()
        self._render_q.append(("clear_live",))
        self._render_q.append(
            (
                "panel",
                Panel(
                    Text(f"⚠ {message}\n\n产出可能未经验证，请人工复核。"),
                    title="⚠ harness 警告",
                    border_style="red",
                ),
            )
        )

    def on_harness_continue(self, reason: str) -> None:
        """Surface a harness force-continue as a dim notice line.

        The model produced what looked like a final answer but the harness found
        unfinished work and demanded more. Flush the first output as an
        intermediate panel (user can see it) then show a dim notice.
        """
        self._flush_round_thinking()
        self._flush_buffer()
        first_line = reason.splitlines()[0] if reason else ""
        if len(first_line) > 120:
            first_line = first_line[:117] + "…"
        self._render_q.append(
            (
                "static",
                f"↻ harness 要求继续：{first_line}",
                "dim italic",
            )
        )

    def on_finish(self) -> None:
        # Capture everything remaining and push ONE finalize instruction.
        # The main-thread drain will fold thinking + mount the final Markdown Panel.
        think_text = self._round_thinking
        secs = time.monotonic() - self._round_think_start if self._round_think_start else 0.0
        # had_live = a live Collapsible was mounted for this round. Use the
        # agent-thread flag (truthful at this moment) rather than _live_think_body
        # (which the main thread owns and may not have updated yet).
        had_live = self._round_think_started
        buf = self.buffer
        # Reset accumulated state.
        self._round_thinking = ""
        self._round_think_start = 0.0
        self._live_think_body = None
        self._live_think_chars = 0
        self._round_think_started = False
        self.buffer = ""
        self._live_output_last_flush = 0.0
        # Reset the todo widget so the next turn mounts a fresh one.
        self._todo_widget = None
        self._render_q.append(("finalize", buf, think_text, secs, had_live))
        if self._status_bar:
            self._status_bar.set_phase("idle")
            self._status_bar.set_turn_tokens(0)

    def on_turn_end(self, writes: list[str]) -> None:
        """Turn-end summary: show a panel listing all files modified this turn.

        Called after on_finish. If the turn modified any files (write_file /
        edit_file / multi_edit), render a compact summary so the user always
        knows what changed — even in auto mode where there's no confirmation.
        Matches the 'always show file changes' UX of claude code / zcode.
        """
        if not writes:
            return
        body = "\n".join(f"📝 {w}" for w in writes)
        self._render_q.append(
            (
                "panel",
                Panel(body, title="本轮修改的文件", border_style="yellow"),
            )
        )

    def add_usage(self, meta: dict[str, int]) -> None:
        for k in ("input_tokens", "output_tokens"):
            if k in meta:
                self.usage[k] += meta[k]
        # Push the turn total to the StatusBar so it shows live token consumption.
        if self._status_bar:
            total = self.usage["input_tokens"] + self.usage["output_tokens"]
            self._status_bar.set_turn_tokens(total)

    def on_todos_update(self, todos: list[dict]) -> None:
        """Push a todo list update to the render queue (agent background thread).

        Called when deepagents' write_todos tool fires. The whole list is
        replaced each call. The main-thread drain renders it as a Markdown
        checklist in the output area (Claude Code style).
        """
        self._render_q.append(("todo_update", todos))

    def on_phase_change(self, state: str, step: int, hint: str) -> None:
        """Task-level phase change (explore/plan/implement/verify/...).

        Called from Harness._track_phase on the agent's background thread. Just
        forwards to StatusBar.set_task_phase (plain attribute write, GIL-safe);
        the heartbeat repaints within ~100ms. 'complete' clears the tag.
        """
        if self._status_bar:
            self._status_bar.set_task_phase("" if state == "complete" else state)

    # ----------------------------------------------------- thinking fold (true fold/unfold)
    def _flush_round_thinking(self) -> None:
        """Push a think_fold instruction to the render queue (agent thread).

        Called whenever a round's thinking needs to be sealed off: before each
        tool call, before non-thinking output begins, and at turn end. Clears
        the agent-thread `_round_think_started` flag so the NEXT round's first
        on_thinking chunk queues a fresh think_start (one Collapsible per round,
        never per-chunk)."""
        if not self._round_thinking.strip():
            # Even if there's no text, drop the started flag so the next round
            # gets a clean start (a stray True here with no body to fold would
            # make the next on_thinking skip think_start).
            self._round_think_started = False
            return
        text = self._round_thinking
        secs = time.monotonic() - self._round_think_start if self._round_think_start else 0.0
        # had_live = a live Collapsible was mounted for THIS round. Read the
        # agent-thread flag, not _live_think_body (main-thread state, races).
        had_live = self._round_think_started
        self._round_thinking = ""
        self._round_think_start = 0.0
        self._live_think_body = None
        self._live_think_chars = 0
        self._round_think_started = False
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
        """Push the accumulated output to the render queue, replacing the live
        streaming RichLog with a Markdown Panel.

        Marked as an INTERMEDIATE panel (distinct title + dim cyan border) so the
        user can visually tell this is mid-process output, NOT the final answer.
        The final answer is mounted by _mount_final_panel with title "coderio"
        and a blue border. Without this distinction, a model that emits text then
        continues with more tool calls looks like it "finished, then restarted"."""
        # Remove the live streaming widget first (if present).
        self._render_q.append(("clear_live",))
        if self.buffer.strip():
            self._render_q.append(
                (
                    "panel",
                    Panel(
                        Markdown(self.buffer),
                        border_style="cyan",
                        title="[dim]中间输出 · agent 仍在运行…[/dim]",
                    ),
                )
            )
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
            # synchronously right after mount races the layout (the new widget's
            # height isn't computed yet), so scroll_end lands above the true
            # bottom. call_after_refresh defers the scroll until the layout pass
            # finishes.
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


def _switch_active_profile(profile_name: str) -> str:
    """Write the chosen profile name to config.toml as active_profile.

    Read-modify-write so other sections and the profiles array are preserved.
    Returns the name written (empty string if it couldn't be written). Called by
    the /profile picker callback after the user picks a profile.
    """
    import tomllib
    from pathlib import Path

    import tomli_w

    config_path = Path.home() / ".coderio" / "config.toml"
    data: dict = {}
    if config_path.is_file():
        try:
            with open(config_path, "rb") as f:
                data = tomllib.load(f)
        except Exception:
            data = {}
    data["active_profile"] = profile_name
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "wb") as f:
        tomli_w.dump(data, f)
    return profile_name


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

    from coderio.cli.repl import _needs_onboarding, _resolve_resume, build_runtime
    from coderio.config import load_config
    from coderio.config.bootstrap import ensure_user_dirs

    ensure_user_dirs()
    search_from = "."
    creds_path = Path.home() / ".coderio" / "credentials"

    # Run TUI-based onboarding if needed (replaces the old console wizard).
    if _needs_onboarding(creds_path):
        result = _run_onboarding_tui()
        if result is None:
            return  # user cancelled

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

    # Repo-config trust confirmation (2026-08-14 v2 audit: cloned malicious
    # repos could set permission_mode="full"/redirect base_url/spawn MCP
    # commands with zero prompt). Must run BEFORE build_runtime — that call
    # loads the repo config AND spawns .mcp.json servers.
    # Discovery is search_from-based (v3 audit P0): the trust scope walks up
    # per-file exactly like the loaders, so launching from a subdirectory of
    # a repo whose root only has .mcp.json no longer bypasses the gate.
    from coderio.config.trust import (
        existing_repo_configs,
        is_repo_trusted,
        mark_repo_trusted,
        summarize_repo_configs,
    )

    user_dir = Path.home() / ".coderio"
    if existing_repo_configs(search_from) and not is_repo_trusted(search_from, user_dir):
        import typer

        typer.secho(
            "This repository contains coderio configuration files that were\nnot previously trusted:",
            fg=typer.colors.YELLOW,
        )
        typer.echo()
        typer.echo(summarize_repo_configs(search_from))
        typer.echo()
        typer.secho(
            "These can change permissions, redirect the model endpoint, or start\n"
            "local processes (MCP servers, hooks). Only trust repositories you\n"
            "control or have reviewed.",
            fg=typer.colors.YELLOW,
        )
        answer = typer.prompt("Trust this repository's coderio config? [y/N]", default="N")
        if answer.strip().lower() not in ("y", "yes"):
            typer.secho("Not trusted — exiting. Re-run and confirm to use this repo's config.", fg=typer.colors.RED)
            raise typer.Exit(1)
        mark_repo_trusted(search_from, user_dir)

    try:
        cfg, store, model, tools, gate, session, active, _rich_stream = build_runtime(
            search_from=search_from,
            console=None,
            creds_path=creds_path,
            provider_override=provider_override,
            model_override=model_override,
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
        f"[bold magenta]coderio[/bold magenta] v{__version__}  "
        f"[dim]profile=[/dim]{cfg.active_profile or 'default'}  "
        f"[dim]model=[/dim]{cfg.model.default}  "
        f"[dim]perm=[/dim]{gate.mode}"
        "\n[dim]引擎:[/dim] [cyan]deepagents[/cyan]  "
        "[dim]输入 /help 看命令, /exit 退出, Ctrl+O 展开/收起思考[/dim]"
    )

    # Mutable runtime holder — /model, /mode, /resume rebuild parts in place.
    rt = {"cfg": cfg, "model": model, "gate": gate, "session": session}

    # Custom project/user commands (.coderio/commands/*.md + ~/.coderio/commands).
    # Discovered ONCE here so the completions menu and execution share one set;
    # new command files land after a restart (documented v1 behavior). Layer-dir
    # convention mirrors load_skill_store: the CALLER joins "<anchor>/.coderio/
    # <thing>" — passing the bare project root would glob every root-level *.md
    # (README, CHANGELOG...) into the command set (runtime-audit finding).
    from pathlib import Path as _Pc

    from coderio.cli.custom_commands import discover_custom_commands, try_expand_line
    from coderio.config.loader import _find_project_dir

    custom_commands = discover_custom_commands(
        project_dir=_find_project_dir(_Pc.cwd()) / ".coderio" / "commands",
        user_dir=_Pc.home() / ".coderio" / "commands",
    )
    rt["custom_commands"] = custom_commands

    def on_input(line: str) -> None:
        # Custom commands expand FIRST: "/name args" → template body becomes
        # the user prompt. The expanded text goes STRAIGHT to the engine path
        # below — NEVER back into handle_slash. Re-entry would let a repo file
        # with body "/mode full" flip the permission gate, or "/export <path>"
        # exfiltrate the session (adversarial-review finding); hence `elif`,
        # not a second sequential `if`.
        expanded = try_expand_line(line, custom_commands)
        if expanded is not None:
            line = expanded
        elif line.startswith("/"):
            from pathlib import Path as _P

            from coderio.cli.commands import ReplContext, handle_slash
            from coderio.session.store import Session

            ctx = ReplContext(
                available_skills=store.names(),
                active_skills_names={s.name for s in active.all()},
                permission_mode=rt["gate"].mode,
                model_name=rt["cfg"].model.default,
                provider_id=rt["cfg"].model.provider_id,
                api_key="",
                base_url=rt["cfg"].model.base_url,
                recent_sessions=Session.list_recent(_P(rt["cfg"].session.save_dir).expanduser()),
                session_save_dir=str(_P(rt["cfg"].session.save_dir).expanduser()),
                session=rt["session"],
                profiles=rt["cfg"].profiles,
                active_profile=rt["cfg"].active_profile,
                usage=tui.usage,
                stream=tui,
                custom_commands=custom_commands,
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

                tui.call_from_thread(
                    tui.push_screen,
                    SessionPickerScreen(
                        summaries,
                        save_dir=str(_P(rt["cfg"].session.save_dir).expanduser()),
                        active_session_id=getattr(rt["session"], "id", ""),
                    ),
                    _on_picked,
                )
                return
            if res.message == "__OPEN_ONBOARDING__":
                # /setup → open the OnboardingScreen to reconfigure provider/model.
                # After it completes, rebuild the runtime with the new config.
                def _on_reconfigured(result):
                    if result is None:
                        return
                    # Reload config + rebuild model with the new provider/key.
                    from pathlib import Path as _Path

                    from coderio.llm import build_chat_model as _build

                    creds = _Path.home() / ".coderio" / "credentials"
                    new_cfg = load_config(search_from=".")
                    rt["cfg"] = new_cfg
                    rt["model"] = _build(new_cfg, creds_path=creds)
                    tui._add_text(
                        f"✅ 已重新配置 → {new_cfg.model.default}（{new_cfg.model.provider_id}）",
                        style="bold green",
                    )

                tui.call_from_thread(tui.push_screen, OnboardingScreen(), _on_reconfigured)
                return
            if res.message == "__OPEN_PROFILE_PICKER__":
                # /profile → open the ProfilePickerScreen. After the user picks,
                # write active_profile to config.toml and rebuild the model.
                profiles = rt["cfg"].profiles or []
                active_name = rt["cfg"].active_profile
                if not profiles:
                    tui._add_text("[yellow]还没有保存的 profile。用 /setup 添加一个配置。[/yellow]")
                    return

                def _on_profile_picked(name):
                    if name is None or name == active_name:
                        return  # cancelled or re-picked the same one
                    _switch_active_profile(name)
                    from coderio.llm import build_chat_model as _build

                    new_cfg = load_config(search_from=".")
                    rt["cfg"] = new_cfg
                    rt["model"] = _build(new_cfg, creds_path=creds_path)
                    tui._add_text(f"✅ 已切换到配置 → {name}", style="bold green")

                tui.call_from_thread(
                    tui.push_screen,
                    ProfilePickerScreen(profiles, active_name),
                    _on_profile_picked,
                )
                return
            if res.message == "__OPEN_MODE_PICKER__":
                # /mode (no arg) → open the ModePickerScreen. After the user
                # picks, rebuild the gate with the new permission mode.
                current_mode = rt["gate"].mode

                def _on_mode_picked(mode):
                    if mode is None or mode == current_mode:
                        return  # cancelled or re-picked the same one
                    from dataclasses import replace as _replace

                    from coderio.cli.repl import build_gate

                    c = _replace(rt["cfg"], tools=_replace(rt["cfg"].tools, permission_mode=mode))
                    rt["cfg"] = c
                    rt["gate"] = build_gate(c, console=None, tui=tui)
                    tui._add_text(f"✅ 已切换到 {mode} 模式", style="bold green")

                tui.call_from_thread(
                    tui.push_screen,
                    ModePickerScreen(current_mode),
                    _on_mode_picked,
                )
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
                    c = _replace(
                        c,
                        tools=_replace(c.tools, permission_mode=res.new_permission_mode),
                    )
                    rt["cfg"] = c
                    rt["gate"] = build_gate(c, console=None, tui=tui)
                cmd_name = line.strip().split(maxsplit=1)[0]
                if cmd_name == "/clear":
                    # /clear: start a fresh session + wipe active skills + clear
                    # the history pane. Without this the old session's messages
                    # keep being fed to the model (it reads session.messages).
                    _clear_context()
                    return
                if cmd_name == "/model":
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) > 1 and parts[1].strip():
                        c = _replace(c, model=_replace(c.model, default=parts[1].strip()))
                        rt["cfg"] = c
                        rt["model"] = build_chat_model(c, creds_path=creds_path)
            return
        from coderio.agent.deep_loop import run_deep_agent
        from coderio.cli.multimodal import build_user_content, extract_images
        from coderio.tools.command_policy import CommandPolicy

        imgs = extract_images(line)
        if imgs:
            tui._add_text(
                f"📎 已附加 {len(imgs)} 张图片: " + ", ".join(p for p, _, _ in imgs),
                style="dim",
            )
        user_content = build_user_content(line)
        # deepagents engine: provides context management, subagents, filesystem.
        # coderio's harness + permission + command review run as middleware.
        cmd_policy = CommandPolicy(
            extra_blocked=rt["cfg"].tools.blocked_commands,
            network_allowed=rt["cfg"].tools.network_allowed,
            whitelist_mode=rt["cfg"].tools.whitelist_mode,
            allowed_commands=rt["cfg"].tools.allowed_commands,
        )
        run_deep_agent(
            user_input=user_content,
            model=rt["model"],
            session=rt["session"],
            stream=tui,
            gate=rt["gate"],
            skill_store=store,
            active_skills=active,
            tools=tools,
            workdir=rt["cfg"].tools.workspace_root or None,
            harness_enabled=rt["cfg"].skills.harness,
            command_policy=cmd_policy,
            sandbox_mode=rt["cfg"].tools.sandbox_mode,
            network_allowed=rt["cfg"].tools.network_allowed,
            fs_config=rt["cfg"].tools.sandbox_fs,
            bash_shell=rt["cfg"].tools.bash_shell,
            hooks=rt["cfg"].hooks,
        )

    def _load_session(sid: str) -> None:
        """Swap the active session to a loaded one, clear skills, render history.

        Called after the picker picks a session (or /resume <id> is given). The
        old session's jsonl stays on disk; we just point the runtime at the new
        Session object so subsequent turns continue that conversation.
        """
        from pathlib import Path as _P

        from coderio.session.store import Session

        save_dir = _P(rt["cfg"].session.save_dir).expanduser()
        rt["session"] = Session.load_by_id(save_dir, sid)
        active.clear()
        # Render the resumed conversation into the history pane so the user sees
        # context they're continuing, not a blank screen.
        # Count only conversation messages (exclude system-role metadata like
        # phase_timeline / context_summary so the count matches what's displayed).
        convo_msgs = [m for m in rt["session"].messages if m.role != "system"]
        tui._add_text(f"↩ 已恢复会话 {sid}（{len(convo_msgs)} 条历史消息）", style="bold green")
        for m in rt["session"].messages:
            if m.role == "user":
                c = m.content
                if isinstance(c, list):
                    c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                tui._add_text(f"▸ you {c}", style="bold cyan")
            elif m.role == "assistant":
                tui._add_text(f"  {m.content[:200]}", style="blue")

    def _clear_context() -> None:
        """Start a fresh session + clear active skills + wipe the history pane.

        Backs the /clear command. Without this the old session's messages keep
        being fed to the model (loop.py reads session.messages), so 'context
        cleared' was previously a lie — the model still saw the full history.
        """
        from pathlib import Path as _P

        from coderio.session.store import Session

        save_dir = _P(rt["cfg"].session.save_dir).expanduser()
        rt["session"] = Session.create(
            save_dir,
            {
                "model": rt["cfg"].model.default,
                "provider": rt["cfg"].model.provider,
            },
        )
        active.clear()
        # Wipe the visible history pane so the user sees a clean slate (the old
        # session's jsonl is preserved on disk — /resume can still get it back).
        tui._clear_history()
        tui._add_text("🆕 已开启新会话（历史已清空，可用 /resume 恢复）", style="bold green")

    tui = CoderioTUI(
        on_input=on_input,
        show_tool_output=cfg.cli.show_tool_output,
        banner=banner,
        extra_completions=[f"/{n} " for n in sorted(custom_commands)],
    )
    # Rebuild the gate with the TUI reference attached. The initial gate from
    # build_runtime doesn't have the TUI (it was constructed before tui existed).
    # Without this, confirm mode would use input() which deadlocks against
    # Textual's terminal takeover.
    from coderio.cli.repl import build_gate as _bg

    rt["gate"] = _bg(cfg, console=None, tui=tui)
    tui.run()
