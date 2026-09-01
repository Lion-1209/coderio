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

import logging
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

# StreamHandler protocol + agent-thread state + render-queue drain (P2-3).
from coderio.cli.stream_controller import ChatStreamController  # noqa: E402

# OnboardingScreen, _OnboardingApp, and _run_onboarding_tui have been extracted
# to tui_onboarding.py for modularity.
from coderio.cli.tui_onboarding import _run_onboarding_tui  # noqa: E402

# The three modal picker screens (Profile / Mode / Session) live in tui_screens.py.
# CommandMenu, ConfirmMenu, StatusBar and _TASK_PHASE_LABELS have been extracted
# to tui_widgets.py for modularity.
from coderio.cli.tui_widgets import (  # noqa: E402
    CommandMenu,
    ConfirmMenu,
    StatusBar,
)

_log = logging.getLogger(__name__)


class CoderioTUI(App):
    """coderio's Textual app. Implements StreamHandler (duck-typed)."""

    CSS = """
    Screen { layout: vertical; }
    #history { border: round $accent; height: 1fr; min-height: 10; padding: 0 1; }
    #history Static { height: auto; }
    /* INPUT PANEL — "floating on the surface" (user spec 2026-08-28):
       ONE seamless rounded canvas. The status text and the send/stop
       buttons are painted DIRECTLY ON the panel — no element carries its
       own background band, so nothing reads as a separate stacked "row".
       Panel padding gives the border breathing room on BOTH sides
       (symmetric 2 columns — the old 1-column padding let the send
       button's tint sit flush against the right border and visually eat
       it). */
    #input-panel {
        height: auto; dock: bottom; margin-bottom: 1;
        border: round $accent; padding: 0 2;
    }
    #input-panel Input { border: none; background: transparent; padding: 0; }
    /* Toolbar row is transparent: status + buttons float on the panel. */
    #input-toolbar { height: 1; background: transparent; }
    #input-toolbar StatusBar {
        width: 1fr; height: 1; padding: 0;
        background: transparent;
    }
    /* ONE morphing button (zcode style — one concept, one control):
       idle ➤ submits; running it IS the stop button (⏹, error tint).
       No separate pause concept: interrupt kills the turn, resubmitting
       continues the work. */
    #send-btn {
        width: auto; min-width: 4; height: 1; padding: 0 1;
        border: none;
        text-style: bold;
        background: $accent 20%; color: $accent;
    }
    #send-btn.running { background: $error 20%; color: $error; }
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
        # StreamHandler protocol + agent-thread streaming state + render queue
        # live in the controller (P2-3); this App owns widgets and user events.
        # Created here so tests and the engine can drive the TUI through the
        # same StreamHandler duck-type as before (forwarders below).
        self._stream = ChatStreamController(self)
        # The most recent thinking Collapsible (MAIN-THREAD widget reference,
        # used by Ctrl+O toggle and /think).
        self._last_collapsible = None
        # Live thinking: the Static body of the IN-PROGRESS thinking block. While
        # non-None, the main thread appends to it in real time (the user sees
        # thinking stream live, not dumped all at once when it ends). Set to None
        # when a round's thinking is flushed/folded. MAIN-THREAD state (the
        # controller only clears the reference from the agent thread).
        self._live_think_body: Static | None = None
        # Cached StatusBar reference: query_one() is a main-thread DOM query, but
        # the controller's callbacks run on the agent's BACKGROUND thread. Cache
        # the widget once (on_mount, main thread) and read the plain attribute
        # thereafter (GIL-safe from any thread).
        self._status_bar: StatusBar | None = None
        # Worker handle (Textual worker managing the agent turn) + UI-side
        # running flag driving the morphing send/stop button.
        self._agent_worker = None
        self._is_running: bool = False

        # Live output: a RichLog widget for streaming the answer as it arrives.
        # RichLog is append-only — each token chunk is written once, avoiding the
        # full-widget layout recompute that Static.update(Text(full_buffer)) would
        # trigger on every batch. On finish, the RichLog is replaced by the final
        # Markdown Panel.
        self._live_out_widget: RichLog | None = None
        self._live_rendered_len: int = 0  # chars already written to the RichLog
        # Dynamic TODO widget: mounted once on first write_todos, updated
        # in-place on subsequent calls (Claude Code style). Reset to None on
        # on_finish so the next turn gets a fresh widget.
        self._todo_widget: Static | None = None

    @property
    def usage(self) -> dict[str, int]:
        """Turn token totals (lives in the controller since P2-3; the /cost
        command reads this)."""
        return self._stream.usage

    # ----------------------------------------------------- layout
    def compose(self) -> ComposeResult:
        from coderio.cli.commands import slash_completions, slash_descriptions

        yield VerticalScroll(id="history")
        # INPUT PANEL (replicates the ZCode app's input box, 2026-08-28):
        # one rounded panel = text area on top + a one-row TOOLBAR below
        # (status left, send/stop right). The buttons are toolbar members —
        # never squeezed into the text line, so no alignment gaps, no dead
        # black strips, and the text area keeps its natural height.
        with Vertical(id="input-panel"):
            yield CommandMenu(slash_completions(self._extra_completions), slash_descriptions())
            yield Input(
                placeholder="输入消息, /help 看命令, Esc 中断任务",
                id="msg",
            )
            # Vertical permission-confirmation menu (zcode/codex style): floats
            # above the input, ↑↓ to choose, Enter to confirm.
            yield ConfirmMenu()
            with Horizontal(id="input-toolbar"):
                yield StatusBar()
                yield Button("➤", id="send-btn", variant="primary", tooltip="发送 (Enter) / 运行中: 中断⏹ (Esc)")

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
    # The wait/result state lives in the controller (P2-3 — it is agent-thread
    # state); this App only owns the ConfirmMenu DOM, shown/hidden from the
    # main thread via call_from_thread.

    def request_confirmation(self, tool_name: str, args: dict) -> bool | str:
        """Agent thread: block until the user allows/denies/custom-responds.

        Forwarder — see ChatStreamController.request_confirmation for the
        three-option contract (✓ 允许 / ✗ 拒绝 / ✎ 其他): 允许 → True,
        拒绝 → False, 其他 → str (the user's free-text instruction).
        """
        return self._stream.request_confirmation(tool_name, args)

    def _resolve_confirmation(self, result: bool | str) -> None:
        """MAIN THREAD: resolve the pending confirmation and wake the agent."""
        self._stream.resolve_confirmation(result)

    def _enter_custom_mode(self) -> None:
        """MAIN THREAD: switch the input bar to custom-reply mode for '自定义回复'."""
        self._stream.enter_custom_mode()
        try:
            # Hide the menu, turn #msg into a custom-reply input.
            self.query_one(ConfirmMenu).hide()
            inp = self.query_one("#msg", Input)
            inp.placeholder = "输入自定义回复，回车提交..."
            inp.value = ""
            inp.focus()
        except Exception:  # noqa: BLE001 — custom-mode UI is best-effort
            _log.debug("custom-reply mode switch failed", exc_info=True)

    def _show_confirm_menu(self, tool_name: str, args_str: str) -> None:
        """MAIN THREAD: display the inline permission menu."""
        self.query_one(ConfirmMenu).show(tool_name, args_str)

    def _hide_confirm_menu(self) -> None:
        """MAIN THREAD: hide the menu and reset #msg's placeholder."""
        self.query_one(ConfirmMenu).hide()
        # Reset #msg placeholder if it was in custom mode.
        inp = self.query_one("#msg", Input)
        inp.placeholder = "输入消息, /help 看命令, Esc 中断任务"

    def _drain_render_queue(self) -> None:
        """MAIN THREAD (set_interval): drain the render queue and execute all
        pending instructions, then scroll to bottom.

        The queue + dispatch table live in ChatStreamController (P2-3); this
        method only runs the drain and applies the scroll strategy:
          - "final" → multi-stage delayed scroll (large Panels need multiple
            layout passes to settle).
          - "streaming" → single deferred scroll — runs after this tick's
            layout settles, without piling up timers across 60ms cycles.
        """
        did_streaming, did_final = self._stream.drain_ui()
        if did_final:
            try:
                self.set_timer(0.15, self._scroll_history_end)
                self.set_timer(0.3, self._scroll_history_end)
                self.set_timer(0.5, self._scroll_history_end)
            except Exception:  # noqa: BLE001 — timer scheduling is best-effort
                _log.debug("scroll timer scheduling failed", exc_info=True)
        elif did_streaming:
            try:
                self.call_after_refresh(self._scroll_history_end)
            except Exception:  # noqa: BLE001
                _log.debug("deferred scroll scheduling failed", exc_info=True)

    def _clear_live_output(self) -> None:
        """MAIN THREAD: remove the streaming RichLog (if any)."""
        if self._live_out_widget is not None:
            try:
                self._live_out_widget.remove()
            except Exception:  # noqa: BLE001 — widget may already be detached
                _log.debug("live output widget removal failed", exc_info=True)
            self._live_out_widget = None
            self._live_rendered_len = 0

    def _exit_app(self) -> None:
        """MAIN THREAD: quit the app (queued by the worker on SystemExit)."""
        self.exit()

    def _render_todos(self, todos: list[dict]) -> None:
        """Render todos as a dynamic checklist in the output area (main thread).

        Claude Code style: a SINGLE checklist widget is mounted on first
        write_todos, then updated in-place on subsequent calls. Tool output
        (edit_file, execute, etc.) appears BELOW it. The widget stays in
        history as a record of the task's progress.

        Returns the scroll category for drain_ui: "none" for an empty list
        and for in-place updates (no new widget → no final scroll), "final"
        when a fresh widget is mounted — matching the pre-P2-3 dynamic
        behavior of the old _h_todo_update handler.
        """
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
            # Update existing widget in-place (no new mount) — but only while
            # it is actually attached. A detached Static.update() does NOT
            # raise (verified on textual 8.2.8), so an attached-check is the
            # only reliable way to detect "widget was cleared/remounted" and
            # fall through to a fresh mount.
            if getattr(self._todo_widget, "is_attached", True):
                try:
                    self._todo_widget.update(panel)
                    return "none"
                except Exception:  # noqa: BLE001 — update failed → mount a new one
                    _log.debug("todo widget in-place update failed", exc_info=True)
            self._todo_widget = None  # detached: remount below
        # First call or widget lost → mount new.
        # FIX (2026-08-28 audit C2): mount THE tracked widget. The old code
        # assigned Static(panel) to self._todo_widget but mounted a SECOND
        # instance via _add_static_main(panel) — the tracked widget was an
        # orphan, so every in-place .update() silently no-op'd and each
        # write_todos silently froze the panel content in place.
        self._todo_widget = Static(panel)
        self._mount_widget_main(self._todo_widget)
        return "final"

    def _render_static(self, text: str, style: str = "") -> None:
        """MAIN THREAD: render a queued ("static", text[, style]) line.

        Tolerates the 1-element tuple form (no style) — the old _h_static
        handler did, and queue producers must not silently lose lines to an
        unpack TypeError inside drain_ui."""
        self._add_text_main(text, style if style else "")

    # ----------------------------------------------------- render methods (MAIN THREAD, called by _drain_render_queue)
    def _scroll_history_end(self) -> None:
        """Scroll the history pane to the bottom."""
        try:
            h = self.query_one("#history", VerticalScroll)
            h.scroll_end(animate=False)
        except Exception:  # noqa: BLE001 — pane may not be mounted yet
            _log.debug("history scroll failed", exc_info=True)

    def _clear_history(self) -> None:
        """Wipe all widgets from the history pane (used by /clear).

        The old session's jsonl stays on disk — this only clears what's visible
        on screen. Must run on the main thread (touches the DOM).
        """
        try:
            h = self.query_one("#history", VerticalScroll)
            h.remove_children()
        except Exception:  # noqa: BLE001
            _log.debug("history clear failed", exc_info=True)
        # The tracked todo widget died with the pane — without this reset the
        # next turn's write_todos would .update() a detached widget (a silent
        # no-op) and the todo list would stay invisible for the whole session
        # (2026-08-28 adversarial review, finding 4).
        self._todo_widget = None

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
        _mount_final_panel). scroll_end lands on the real content bottom once
        the layout settles (note the layers caveat in the CSS).
        """
        if think_text.strip():
            self._render_think_fold(think_text, secs, had_live)
        if buf.strip():
            # Remove the streaming RichLog and replace with the final Markdown Panel.
            if self._live_out_widget is not None:
                try:
                    self._live_out_widget.remove()
                except Exception:  # noqa: BLE001 — already detached
                    _log.debug("live output removal failed", exc_info=True)
                self._live_out_widget = None
                self._live_rendered_len = 0
            self.call_after_refresh(self._mount_final_panel, buf)

    def _mount_final_panel(self, buf: str) -> None:
        """MAIN THREAD: mount the final answer and scroll to the bottom.

        KEPT: the blue-bordered Panel titled "coderio". A border-free redesign
        was tried (2026-08-27) and reverted the same day on direct user
        feedback — the box reads better and brands the answer. Mounts Static
        and schedules a multi-stage delayed scroll (the Panel's height needs a
        few layout passes to settle).
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
        """Morph the send slot with the turn state (safe from any thread):
        idle ➤ submits; running ⏹ interrupts — zcode's one-control pattern,
        the send button IS the stop button. Turn end always restores ➤.
        label/add_class writes are plain attribute mutations (GIL-safe), no
        call_from_thread needed — the next layout pass picks them up."""
        try:
            btn = self.query_one("#send-btn", Button)
            if show:
                btn.label = "⏹"
                btn.add_class("running")
            else:
                btn.label = "➤"
                btn.remove_class("running")
        except Exception:  # noqa: BLE001 — button may not be mounted yet
            _log.debug("send/stop button morph failed", exc_info=True)

    def _submit_current_input(self) -> None:
        """Send-button submit: dispatch whatever is in #msg, exactly as Enter
        would (same echo, same worker). No-op on empty input."""
        try:
            inp = self.query_one("#msg", Input)
        except Exception:  # noqa: BLE001 — input not mounted yet
            _log.debug("send-button submit: input not found", exc_info=True)
            return
        line = inp.value.strip()
        if not line:
            return
        inp.value = ""
        self._on_input(line)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle the morphing send/stop button: ➤ submits when idle, ⏹
        interrupts while running. Confirm is a keyboard menu."""
        if event.button.id == "send-btn":
            if self._is_running:
                self.action_interrupt()
            else:
                self._submit_current_input()

    # ----------------------------------------------------- command menu (autocomplete)
    def on_input_changed(self, event: Input.Changed) -> None:
        """Live-filter the command menu as the user types in the main input."""
        if event.input.id != "msg":
            return
        self.query_one(CommandMenu).refresh_for(event.value)

    def on_key(self, event) -> None:
        """Handle command-menu navigation + inline confirmation keys."""
        # Custom-reply mode: Enter submits the user's text as a str result.
        if self._stream.confirm_custom_mode:
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
        except Exception:  # noqa: BLE001 — menu may not be mounted yet
            _log.debug("confirm menu key handling failed", exc_info=True)
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
        self._spawn_turn(line)

    def _spawn_turn(self, line: str) -> None:
        """Echo + start the agent worker for one user line. Shared by Enter
        (on_input_submitted) and the ➤ send button so both paths stay
        behaviorally identical."""
        self._add_text(f"▸ you {line}", style="bold cyan")
        if not self._on_input:
            return

        # Use a Textual WORKER (thread=True), not a raw threading.Thread. A
        # worker is managed by Textual, so the main event loop stays alive and
        # keeps draining pending UI updates while the worker runs AND after it
        # completes — a raw daemon thread would not.
        def _run():
            self._is_running = True
            self._stream.begin_turn()
            self.call_from_thread(self._show_interrupt_btn, True)
            try:
                self._on_input(line)
            except SystemExit:
                self._stream.queue_exit()
            except InterruptedError:
                # User pressed Esc / clicked 中断. Show a notice and clean up.
                self._stream.reset_stream_state()
                if self._status_bar:
                    self._status_bar.set_phase("idle")
                self._stream.queue_panel(
                    Panel(
                        "用户已中断当前任务。已完成的中间结果保留在历史中。\n输入新消息继续，或按 ↑ 恢复上一条输入。",
                        title="⚠ 已中断",
                        border_style="yellow",
                    )
                )
            except Exception as e:
                # Reset the streaming state + status bar so the TUI doesn't
                # get stuck in 'thinking' phase when the agent errors out
                # (e.g. API auth failure, network error). on_finish is never
                # called when run_deep_agent raises, so we must clean up here.
                self._stream.reset_stream_state()
                if self._status_bar:
                    self._status_bar.set_phase("idle")
                self._stream.queue_panel(
                    (
                        Panel(
                            Text(f"⚠ {type(e).__name__}: {e}\n\n你的输入已保留在输入框，按 Enter 可重试。"),
                            title="⚠ 运行错误",
                            border_style="red",
                        )
                    )
                )

                def _refill_input():
                    try:
                        inp = self.query_one("#msg", Input)
                        inp.value = line
                        inp.focus()
                    except Exception:  # noqa: BLE001 — input may not exist yet
                        _log.debug("input refill failed", exc_info=True)

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
        self._stream.request_interrupt()
        # Cancel the worker as a backup — this unblocks subprocess.run calls
        # and model.stream() that might be waiting on I/O. The flag handles
        # the clean exit; cancel handles the "stuck in I/O" case.
        if self._agent_worker is not None:
            try:
                self._agent_worker.cancel()
            except Exception:  # noqa: BLE001 — worker may have just finished
                _log.debug("worker cancel on interrupt failed", exc_info=True)

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
    # Thin forwarders to ChatStreamController (P2-3): the protocol logic and
    # agent-thread streaming state live there; CoderioTUI stays a StreamHandler
    # duck-type so the engine (run_deep_agent(stream=self.tui)) and the tests
    # keep a stable surface. All of these run on the agent's BACKGROUND thread;
    # the controller only queues render instructions and writes plain
    # attributes — no call_from_thread anywhere.

    def on_step_start(self, step: int = 1) -> None:
        self._stream.on_step_start(step)

    def is_interrupted(self) -> bool:
        return self._stream.is_interrupted()

    def on_token(self, text: str) -> None:
        self._stream.on_token(text)

    def on_thinking(self, text: str) -> None:
        self._stream.on_thinking(text)

    def on_tool_start(
        self,
        name: str,
        args: dict[str, Any],
        step: int = 1,
        tool_index: int = 0,
        tool_total: int = 0,
    ) -> None:
        self._stream.on_tool_start(name, args, step=step, tool_index=tool_index, tool_total=tool_total)

    def on_tool_end(self, name: str, result: str) -> None:
        self._stream.on_tool_end(name, result)

    def on_harness_warn(self, message: str) -> None:
        self._stream.on_harness_warn(message)

    def on_harness_continue(self, reason: str) -> None:
        self._stream.on_harness_continue(reason)

    def on_finish(self) -> None:
        self._stream.on_finish()

    def on_turn_end(self, writes: list[str]) -> None:
        self._stream.on_turn_end(writes)

    def add_usage(self, meta: dict[str, int]) -> None:
        self._stream.add_usage(meta)

    def on_todos_update(self, todos: list[dict]) -> None:
        self._stream.on_todos_update(todos)

    def on_phase_change(self, state: str, step: int, hint: str) -> None:
        self._stream.on_phase_change(state, step, hint)

    # ----------------------------------------------------- thinking fold (true fold/unfold)
    def show_last_thinking(self) -> bool:
        """Expand the most recent thinking (compat with /think command)."""
        if self._last_collapsible is None:
            self._add_text("最近一轮没有思考内容。", style="dim")
            return False
        self._last_collapsible.collapsed = False  # expand
        return True

    # ----------------------------------------------------- helpers: add content to history

    def _add_text(self, text: str, style: str = "") -> None:
        """Push a text line to the render queue."""
        self._stream.queue_static(text, style)

    def _add_text_main(self, text: str, style: str = "") -> None:
        try:
            history = self.query_one("#history", VerticalScroll)
            history.mount(Static(Text(text, style=style) if style else Text(text)))
            history.call_after_refresh(history.scroll_end, animate=False)
        except Exception:  # noqa: BLE001 — pane may not be mounted yet
            _log.debug("history mount failed", exc_info=True)

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
        except Exception:  # noqa: BLE001 — pane may not be mounted yet
            _log.debug("history mount failed", exc_info=True)

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
        except Exception:  # noqa: BLE001 — pane may not be mounted yet
            _log.debug("history mount failed", exc_info=True)


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

    # Input dispatch + session lifecycle live in TuiRuntime (tui_runtime.py) —
    # extracted from this function's former closures (S3 decomposition): pure
    # runtime wiring, unit-testable without booting Textual.
    from pathlib import Path as _Pc

    from coderio.cli.custom_commands import discover_custom_commands
    from coderio.cli.tui_runtime import TuiRuntime
    from coderio.config.loader import _find_project_dir

    custom_commands = discover_custom_commands(
        project_dir=_find_project_dir(_Pc.cwd()) / ".coderio" / "commands",
        user_dir=_Pc.home() / ".coderio" / "commands",
    )

    runtime = TuiRuntime(
        store=store,
        active=active,
        tools=tools,
        creds_path=creds_path,
        custom_commands=custom_commands,
    )
    tui = CoderioTUI(
        on_input=runtime.handle_input,
        show_tool_output=cfg.cli.show_tool_output,
        banner=banner,
        extra_completions=[f"/{n} " for n in sorted(custom_commands)],
    )
    runtime.bind(tui, cfg=cfg, model=model, gate=gate, session=session)
    tui.run()
