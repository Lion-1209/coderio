"""ChatStreamController — the StreamHandler implementation behind the TUI.

P2-3 (2026-08-28 audit): the 12 StreamHandler callbacks, the agent-thread
streaming state (buffer / thinking rounds / usage / interrupt flag) and the
render-queue drain schedule used to live ON CoderioTUI, mixing agent-thread
protocol logic with Textual layout code (~600 lines inside the App class).
They live here now: the controller OWNS the protocol + the render queue;
CoderioTUI owns widgets and user events.

Threading contract (unchanged by the extraction):
  - Callbacks run on the agent's BACKGROUND thread. They never touch the
    Textual DOM and never call call_from_thread (its callbacks are not
    reliably delivered in a real terminal). Background-thread data reaches
    main-thread widgets through TWO documented channels:
      1. render_q + drain_ui(): callbacks push render instructions onto the
         thread-safe deque; the main-thread timer (CoderioTUI.on_mount →
         set_interval, 60ms) drains it and invokes the TUI's render methods.
         This carries all CONTENT (text, panels, thinking, todos).
      2. Plain-attribute writes, GIL-safe from any thread: the controller
         reads/writes a few documented attributes on ``_ui`` (``_status_bar``,
         ``_todo_widget``, ``_live_think_body``, ``show_tool_output``) and
         StatusBar's set_phase/set_turn_tokens/set_task_phase write plain
         attributes picked up by the next layout pass. This carries live
         STATUS only (phase text, token counts) — never content.
    Any new cross-thread surface must be added to this list, not invented
    ad hoc.
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from typing import Any

from rich.markdown import Markdown
from rich.panel import Panel
from rich.text import Text

from coderio.tools.taxonomy import TASK as TASK_TOOL
from coderio.tools.taxonomy import WRITE_TOOLS

_log = logging.getLogger(__name__)


class ChatStreamController:
    """StreamHandler protocol + agent-thread state + render-queue schedule.

    The TUI forwards its StreamHandler duck-type to an instance of this class
    (see CoderioTUI's protocol section), so the engine keeps calling
    ``stream.on_token(...)`` on the TUI object exactly as before.
    """

    # Tools that modify files — shown with a prominent yellow line so the user
    # always knows what changed, even in auto mode (where there's no confirmation).
    # From the taxonomy registry (single source of truth, audit A2).
    _WRITE_TOOLS: frozenset[str] = WRITE_TOOLS

    def __init__(self, ui) -> None:
        self._ui = ui
        # StreamHandler state (agent thread).
        self.buffer = ""
        self.usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
        self._round_thinking = ""
        self._round_think_start = 0.0
        # Agent-thread-local flag: has a think_start already been queued for the
        # CURRENT round? `_live_think_body` is set by the MAIN thread 60ms later
        # (next drain tick), so reading it from the agent thread is a race —
        # within one drain window many on_thinking chunks arrive before the main
        # thread has had a chance to mount the Collapsible, and each one would
        # otherwise queue a SEPARATE think_start, fragmenting one continuous
        # thinking stream into N tiny Collapsibles ("The" / "The user is" / ...).
        # This flag is owned entirely by the agent thread (set in on_thinking,
        # cleared in _flush_round_thinking / on_finish), so there's no race.
        self._live_think_chars = 0  # chars shown so far (to append only the delta)
        self._round_think_started: bool = False
        self._live_output_last_flush: float = 0.0  # throttle: only flush >=80ms apart
        # Interrupt support: set by request_interrupt() when the user presses
        # Esc during a turn. The engine polls is_interrupted() between stream
        # chunks and raises InterruptedError at the next safe boundary.
        self._interrupted: bool = False
        # Inline confirmation state (cross-thread): when _confirm_event is
        # non-None, the AGENT thread is blocked inside request_confirmation()
        # waiting for the user to allow/deny a write. The main thread resolves
        # it via resolve_confirmation() (wired to the ConfirmMenu keys).
        self._confirm_event: threading.Event | None = None
        self._confirm_result: bool | str = False
        self._confirm_custom_mode = False  # True when user clicked "其他"
        # RENDER QUEUE: the agent's background thread pushes render instructions
        # here (thread-safe deque append/popleft). A main-thread set_interval
        # timer drains the queue via drain_ui() and executes the instructions.
        self.render_q: collections.deque = collections.deque()

    # ----------------------------------------------------- cross-thread confirmation
    def request_confirmation(self, tool_name: str, args: dict, detail: str | None = None) -> bool | str:
        """Ask the user to allow/deny/custom-respond to a write operation.

        Called from the AGENT's background thread (PermissionMiddleware). Shows
        an inline confirmation row with three options: ✓ 允许 / ✗ 拒绝 / ✎ 其他.
        - 允许 → True (execute the tool)
        - 拒绝 → False (block, "Permission denied")
        - 其他 → user types free text → str (block, but feed user's instruction
          to the model as a tool result so it can adjust)

        ``detail`` (P3-1) is an optional pre-rendered diff preview for
        file-write tools, shown between the prompt and the options.

        The "其他" mode hides the buttons and turns #msg into a custom-reply
        input. The user types their instruction and presses Enter to submit.
        """
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        if len(args_str) > 120:
            args_str = args_str[:120] + "…"
        self._confirm_event = threading.Event()
        self._confirm_result = False
        self._confirm_custom_mode = False
        ui = self._ui

        def _show():
            try:
                ui._show_confirm_menu(tool_name, args_str, detail=detail)
            except Exception:  # noqa: BLE001 — confirm UI is best-effort
                _log.debug("confirm menu show failed", exc_info=True)

        def _hide():
            try:
                ui._hide_confirm_menu()
            except Exception:  # noqa: BLE001
                _log.debug("confirm menu hide failed", exc_info=True)

        ui.call_from_thread(_show)
        # Sliced wait (2026-09-02 audit finding 6): a full 120s block left the
        # agent thread un-interruptible — Esc only set a flag nobody polled,
        # so the TUI froze on the prompt until the user answered or timed out.
        # Wake every 0.5s to check the interrupt flag; an interrupt resolves
        # the prompt as DENY (the tool call is refused) and the very next
        # stream-chunk boundary raises InterruptedError to end the turn.
        deadline = time.monotonic() + 120
        interrupted = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            if self._confirm_event.wait(timeout=min(remaining, 0.5)):
                break
            if self._interrupted:
                interrupted = True
                break
        ui.call_from_thread(_hide)
        self._confirm_event = None
        self._confirm_custom_mode = False
        if interrupted:
            return False
        return self._confirm_result

    def resolve_confirmation(self, result: bool | str) -> None:
        """MAIN THREAD: resolve the pending confirmation and wake the agent."""
        self._confirm_result = result
        if self._confirm_event is not None:
            self._confirm_event.set()

    @property
    def confirm_custom_mode(self) -> bool:
        """True while the input bar is in custom-reply mode ('其他' chosen)."""
        return self._confirm_custom_mode

    def enter_custom_mode(self) -> None:
        """MAIN THREAD: switch to custom-reply mode ('其他' chosen)."""
        self._confirm_custom_mode = True

    # ----------------------------------------------------- StreamHandler protocol
    # ALL callbacks run on the agent's BACKGROUND thread. They ONLY push render
    # instructions to self.render_q — no call_from_thread here.
    def on_step_start(self, step: int = 1) -> None:
        self._flush_round_thinking()
        # Reset turn token counter at the start of each turn (step 1 = new turn).
        if step == 1:
            self.usage = {"input_tokens": 0, "output_tokens": 0}
            if self._ui._status_bar:
                self._ui._status_bar.set_turn_tokens(0)
        self._ui._set_phase("thinking", step=step)

    def is_interrupted(self) -> bool:
        """Check if the user requested an interrupt (agent thread calls this).

        The engine polls this between stream chunks. When True it raises
        InterruptedError at the chunk boundary; the worker's error path shows
        the '⚠ 已中断' panel.
        """
        return self._interrupted

    def request_interrupt(self) -> None:
        """UI thread: the user pressed Esc / clicked the stop button."""
        self._interrupted = True

    def on_token(self, text: str) -> None:
        self._flush_round_thinking()
        bar = self._ui._status_bar
        if bar is None or bar.phase != "responding":
            self._ui._set_phase("responding")
        # Accumulate in buffer (agent thread). Push a "text" render instruction
        # with the FULL buffer so the main thread can update the live widget.
        # Throttle: only push at most once per ~60ms to avoid flooding the queue.
        self.buffer += text
        now = time.monotonic()
        if self._live_output_last_flush == 0.0 or (now - self._live_output_last_flush) >= 0.06:
            self._live_output_last_flush = now
            self.render_q.append(("text", self.buffer))

    def on_thinking(self, text: str) -> None:
        if not self._round_thinking:
            self._round_think_start = time.monotonic()
        self._round_thinking += text
        now = time.monotonic()
        # Decide think_start vs think_update using an AGENT-THREAD-LOCAL flag,
        # NOT `_live_think_body`. The latter is set by the main thread only after
        # the next drain tick (60ms away), so reading it here is a race: within
        # one drain window many on_thinking chunks arrive, each one would
        # re-enter the "first chunk" branch and queue another think_start,
        # fragmenting one continuous thinking stream into many tiny Collapsibles.
        if not self._round_think_started:
            # First chunk of this round: queue think_start with the FULL text so
            # far, and mark the round as started. The main thread will mount ONE
            # Collapsible; subsequent chunks queue think_update against that same
            # widget.
            self._round_think_started = True
            self._live_think_chars = len(self._round_thinking)
            self.render_q.append(("think_start", self._round_thinking))
            self._live_output_last_flush = now  # reuse throttle timer for thinking
        else:
            delta_len = len(self._round_thinking) - self._live_think_chars
            if delta_len > 0 and (now - self._live_output_last_flush) >= 0.06:
                self._live_think_chars = len(self._round_thinking)
                self._live_output_last_flush = now
                self.render_q.append(("think_update", self._round_thinking))

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
        self._ui._set_phase(
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
        if name == TASK_TOOL:
            subagent = args.get("subagent_type", "general-purpose")
            desc = args.get("description", "") or args.get("instructions", "")
            desc_short = desc.split("\n")[0][:80] if desc else ""
            self.render_q.append(("static", f"🔄 委派子 agent [{subagent}]：{desc_short}…（执行中，请稍候）", "cyan"))
            return
        args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
        if len(args_str) > 100:
            args_str = args_str[:100] + "…"
        self.render_q.append(("static", f"⏺ {name}({args_str})", "green"))

    def on_tool_end(self, name: str, result: str) -> None:
        self._ui._set_phase("thinking")
        # Write tools get a prominent yellow line so the user sees what was
        # modified — matching the "always show file changes" UX of claude code /
        # zcode. Other tools keep the existing dim/grey output.
        if name in self._WRITE_TOOLS and not result.startswith(("Error", "Permission denied")):
            self.render_q.append(
                ("static", f"  📝 {result.splitlines()[0] if result.splitlines() else name}", "yellow bold")
            )
            return
        if name == "_empty_response":
            # Empty-response exhaustion is a hard interruption, not a normal
            # tool result — show it as a red panel (like on_harness_warn) so
            # it's visible instead of buried in dim grey text.
            self._flush_round_thinking()
            self._flush_buffer()
            self.render_q.append(
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
        if not self._ui.show_tool_output:
            first = result.splitlines()[0][:60] if result.splitlines() else ""
            self.render_q.append(("static", f"  → {first}{'…' if len(result) > 60 else ''}", "dim"))
            return
        # Labeled, CHAR-capped preview. The old code truncated by LINE count,
        # so a tool returning one huge line (list_dir's 57-entry Python repr,
        # web_fetch HTML) soft-wrapped into a ~17-row unlabeled wall mid-
        # transcript (2026-08-27 live TUI audit). Collapse ALL whitespace and
        # cap by characters instead — one compact, attributable line.
        flat = " ".join(result.split())
        if len(flat) > 160:
            flat = flat[:160] + "…"
        self.render_q.append(("static", f"  ↳ {name}: {flat}" if flat else f"  ↳ {name}: (空)", "dim"))

    def on_harness_warn(self, message: str) -> None:
        """Escalation release: the harness allowed a sketchy answer through.

        IMPORTANT: do NOT call _flush_buffer() here. on_harness_warn is always
        followed by on_finish, which renders self.buffer as the final blue
        'coderio' Panel. Flushing here would instead render it as a cyan
        '中间输出' Panel AND clear the buffer, so on_finish would have nothing
        left to show — the model's real final answer would appear as misleading
        intermediate output (the exact bug we fixed). Just clear the live
        streaming widget so on_finish can mount the final Panel cleanly."""
        self._flush_round_thinking()
        self.render_q.append(("clear_live",))
        self.render_q.append(
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
        self.render_q.append(
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
        self.reset_stream_state()
        # Reset the todo widget so the next turn mounts a fresh one.
        self._ui._todo_widget = None
        self.render_q.append(("finalize", buf, think_text, secs, had_live))
        if self._ui._status_bar:
            self._ui._status_bar.set_phase("idle")
            self._ui._status_bar.set_turn_tokens(0)

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
        self.render_q.append(
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
        if self._ui._status_bar:
            total = self.usage["input_tokens"] + self.usage["output_tokens"]
            self._ui._status_bar.set_turn_tokens(total)

    def on_todos_update(self, todos: list[dict]) -> None:
        """Push a todo list update to the render queue (agent background thread).

        Called when deepagents' write_todos tool fires. The whole list is
        replaced each call. The main-thread drain renders it as a Markdown
        checklist in the output area (Claude Code style).
        """
        self.render_q.append(("todo_update", todos))

    def on_phase_change(self, state: str, step: int, hint: str) -> None:
        """Task-level phase change (explore/plan/implement/verify/...).

        Called from Harness._track_phase on the agent's background thread. Just
        forwards to StatusBar.set_task_phase (plain attribute write, GIL-safe);
        the heartbeat repaints within ~100ms. 'complete' clears the tag.
        """
        if self._ui._status_bar:
            self._ui._status_bar.set_task_phase("" if state == "complete" else state)

    # ----------------------------------------------------- agent-thread helpers
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
        self._ui._live_think_body = None
        self._live_think_chars = 0
        self._round_think_started = False
        self.render_q.append(("think_fold", text, secs, had_live))

    def _flush_buffer(self) -> None:
        """Push the accumulated output to the render queue, replacing the live
        streaming RichLog with a Markdown Panel.

        Marked as an INTERMEDIATE panel (distinct title + dim cyan border) so the
        user can visually tell this is mid-process output, NOT the final answer.
        The final answer is mounted with title "coderio" and a blue border. Without
        this distinction, a model that emits text then continues with more tool
        calls looks like it "finished, then restarted"."""
        # Remove the live streaming widget first (if present).
        self.render_q.append(("clear_live",))
        if self.buffer.strip():
            self.render_q.append(
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

    def reset_stream_state(self) -> None:
        """Clear per-turn streaming accumulators (agent thread).

        Used by on_finish AND by the worker's interrupt/error paths (on_finish
        is never called when the engine raises, so the worker resets explicitly
        to keep the TUI from sticking in 'thinking' phase).
        """
        self._round_thinking = ""
        self._round_think_start = 0.0
        self._ui._live_think_body = None
        self._live_think_chars = 0
        self._round_think_started = False
        self.buffer = ""
        self._live_output_last_flush = 0.0

    def begin_turn(self) -> None:
        """Agent-thread worker start: clear a stale interrupt flag."""
        self._interrupted = False

    # ----------------------------------------------------- queue helpers (any thread)
    def queue_static(self, text: str, style: str = "") -> None:
        self.render_q.append(("static", text, style))

    def queue_panel(self, renderable) -> None:
        self.render_q.append(("panel", renderable))

    def queue_exit(self) -> None:
        self.render_q.append(("exit",))

    # ----------------------------------------------------- MAIN THREAD: drain
    # Dispatch table: action name -> (TUI render method, scroll category).
    # Built once at class definition — a flat table lookup instead of a
    # 9-branch if/elif chain (cyclomatic complexity ~18 in the pre-P2-3
    # version). Each category drives the post-drain scroll strategy:
    #   - "streaming": lightweight live update → single deferred scroll.
    #   - "final": new widget mounted → multi-stage delayed scroll (layout
    #     passes need time to settle on large Panels).
    #   - "none": no scroll trigger (clear_live, exit).
    _ACTION_TABLE: dict[str, tuple[str, str]] = {
        "text": ("_render_live_output", "streaming"),
        "finalize": ("_render_finalize", "final"),
        "think_start": ("_render_think_start", "streaming"),
        "think_update": ("_render_think_update", "streaming"),
        "think_fold": ("_render_think_fold", "final"),
        "static": ("_render_static", "final"),
        "panel": ("_add_static_main", "final"),
        "clear_live": ("_clear_live_output", "none"),
        "exit": ("_exit_app", "none"),
        "todo_update": ("_render_todos", "final"),
    }

    def drain_ui(self) -> tuple[bool, bool]:
        """MAIN THREAD (CoderioTUI's set_interval timer): pop all queued render
        instructions and execute them on the TUI. Returns (did_streaming,
        did_final) so the caller can pick the scroll strategy.

        A handler may return its own scroll category ("streaming"/"final"/
        "none") to override the table default — _render_todos does this so an
        empty list / in-place update doesn't schedule the multi-stage final
        scroll (matching the pre-P2-3 dynamic behavior)."""
        did_streaming = False
        did_final = False
        ui = self._ui
        while self.render_q:
            action, *args = self.render_q.popleft()
            entry = self._ACTION_TABLE.get(action)
            if entry is None:
                continue
            method_name, category = entry
            try:
                ret = getattr(ui, method_name)(*args)
            except Exception:  # noqa: BLE001 — a render glitch must never kill the drain loop
                _log.debug("TUI render action %r failed", action, exc_info=True)
                continue
            if ret in ("streaming", "final", "none"):
                category = ret
            if category == "streaming":
                did_streaming = True
            elif category == "final":
                did_final = True
        return did_streaming, did_final
