"""TUI widgets — extracted from tui.py for modularity.

Contains the popup slash-command menu (CommandMenu), the permission-confirmation
menu (ConfirmMenu), and the live status bar (StatusBar) with its animated
spinner + phase/timer display. These are self-contained widgets with no
top-level coupling to the rest of coderio.

StatusBar owns a background heartbeat thread (~80ms) that drives both the
spinner animation and the elapsed-timer refresh via call_from_thread.
"""

from __future__ import annotations

import threading
import time

from rich.console import RenderableType
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Input, ListItem, ListView, Static

# Human-readable labels for AgentState task-phase values (shown in StatusBar).
# Mirrors _PHASE_LABELS in cli/stream.py — duplicated rather than imported to
# keep this module decoupled from the Rich CLI stream module.
_TASK_PHASE_LABELS: dict[str, str] = {
    "explore": "探索",
    "plan": "规划",
    "implement": "实现",
    "verify": "验证",
    "complete": "完成",
}


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
        /* Lives INSIDE #input-bar (above StatusBar). When visible it expands
           the input-bar upward, pushing into the history pane's area — but
           since #history is height:1fr it shrinks to accommodate, so there's
           no overlap (unlike the old dock:bottom approach where the menu
           floated over and hid the StatusBar's first char + lost its own
           bottom border). height is fixed so the ListView gets a real
           viewport (height:auto collapses to ~4 rows in practice). */
        display: none;          /* hidden until input starts with "/" */
        height: 10;
        background: $surface;
        border: round $accent;
        padding: 0;
        margin: 0;
    }
    CommandMenu.-visible { display: block; }
    CommandMenu ListView { background: $surface; }
    CommandMenu ListItem { padding: 0 1; }
    CommandMenu ListItem > Widget :hover { background: $boost; }
    """

    def __init__(self, completions: list[str], descriptions: dict[str, str] | None = None) -> None:
        super().__init__()
        self._all = completions
        # completion → one-line description, shown as a dim second column in
        # each row (2026-08-27 live TUI audit: the menu listed bare names and
        # gave no hint what a command does before selecting it).
        self._descs = descriptions or {}
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

    @staticmethod
    def _bare(candidate: str) -> str:
        """The bare command of a completion candidate: '/mode plan' → '/mode'."""
        return candidate.split(" ", 1)[0]

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
            desc = self._descs.get(c, "")
            # Subcommand/argument forms ("/mode plan") reuse the parent's
            # description — hide it to avoid 5 identical rows of the same text.
            if c != self._bare(c) and self._descs.get(self._bare(c)) == desc:
                label = f"{c}"
            elif desc:
                label = f"{c}  [dim]{desc}[/dim]"
            else:
                label = c
            lv.append(ListItem(Static(label, markup=True), name=c))
        self.add_class("-visible")
        # auto-select the first (top) match so Enter is immediately meaningful
        try:
            lv.index = 0
        except Exception:
            pass

    def visible(self) -> bool:
        return self.has_class("-visible")

    def move(self, delta: int) -> None:
        """Move the selection by delta (+1 down, -1 up); wraps around.

        After moving, proactively scrolls so the highlight stays at least two
        rows inside the visible viewport (not flush against the edge). Textual's
        default scroll_to_widget only reacts once the highlighted item is FULLY
        off-screen, which makes the menu feel unresponsive — the user presses
        down several times before the view catches up. We compute the target
        scroll_y directly to trigger earlier.

        Robustness note: we do NOT trust lv.size.height blindly. In a real
        terminal the CommandMenu (dock:bottom, max-height:12) overlaps the
        history pane, and the *effective* visible row count can be smaller
        than what the ListView reports (border/padding/overlap). So we clamp
        the assumed viewport height to a conservative cap and keep a 2-row
        margin on both sides — even if the reported height is slightly off,
        the highlight stays in the safe zone.
        """
        if not self.visible():
            return
        lv = self.query_one("#cmd-list", ListView)
        if not lv.children:
            return
        n = len(lv.children)
        idx = lv.index or 0
        new_idx = (idx + delta) % n
        lv.index = new_idx

        try:
            # Effective viewport height for scroll math. The ListView reports
            # its computed height, but in a real terminal the menu's visible
            # area can be smaller (overlap with history border, conpty quirks).
            # Cap to a conservative value and keep a 2-row margin on each side
            # so the highlight never sits on the very edge.
            vp_h = lv.size.height
            if vp_h <= 0:
                vp_h = 8  # fallback if layout hasn't settled yet
            margin = 2  # rows of breathing room above AND below the highlight
            row = new_idx
            if delta > 0:
                # Moving DOWN: keep the highlight within the bottom margin.
                # Target scroll_y so row sits at most (vp_h - margin - 1) rows
                # below the viewport top.
                target = max(0, row - (vp_h - margin - 1))
            else:
                # Moving UP: keep the highlight within the top margin.
                # Target scroll_y so row sits at least `margin` rows below top.
                target = max(0, row - margin)
            # Clamp to valid range and apply if it actually changes the view.
            target = max(0, min(lv.max_scroll_y, target))
            if target != lv.scroll_y:
                lv.scroll_y = target
            # Belt-and-suspenders: also ask Textual to ensure visibility. This
            # is a no-op when already visible, but catches any edge case the
            # manual computation missed (e.g. vp_h reporting a stale value).
            lv.scroll_to_widget(lv.children[new_idx], animate=False, top=False)
        except Exception:
            try:
                lv.scroll_to_widget(lv.children[new_idx], animate=False)
            except Exception:
                pass

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


class ConfirmMenu(Vertical):
    """Permission-confirmation menu (zcode/codex-style vertical selection).

    Replaces the old three-button #confirm-row (whose Button borders inflated
    the layout and left a black gap). This is a vertical ListView floating
    above the input box: ↑↓ moves the highlight, Enter confirms the choice,
    Esc cancels (= deny). The user sees all options at once and picks with
    the keyboard — same interaction model as the slash-command CommandMenu.

    Lifecycle: the agent's background thread calls show() (via call_from_thread);
    the main thread's on_key drives move()/accept(); accept() returns the chosen
    option name ("allow"/"deny"/"custom") so on_key can dispatch to
    _resolve_confirmation / _enter_custom_mode. hide() is called when the agent
    thread resumes.
    """

    DEFAULT_CSS = """
    ConfirmMenu {
        /* Hidden by default. When visible it sits between #status-row and #msg,
           expanding the input-bar upward (history shrinks to fit). height:auto
           adapts: 5 rows bare (prompt + 3 options), taller with a diff preview
           (P3-1) — bounded by max-height so a huge diff scrolls instead of
           filling the screen. */
        display: none;
        height: auto;
        max-height: 30;
        background: $surface;
        border: round $accent;
        padding: 0;
        margin: 0;
    }
    ConfirmMenu.-visible { display: block; }
    ConfirmMenu ListView { background: $surface; height: 3; }
    ConfirmMenu ListItem { padding: 0 1; }
    /* Prompt line at the top showing the tool + args. */
    ConfirmMenu #confirm-prompt { color: $text; padding: 0 1; height: 1; }
    /* Diff preview block (P3-1): hidden unless show(detail=...) has content. */
    ConfirmMenu #confirm-diff {
        display: none;
        height: auto;
        max-height: 16;
        padding: 0 1;
        background: $surface-darken-1;
        overflow-y: auto;
    }
    ConfirmMenu.-has-diff #confirm-diff { display: block; }
    """

    # Choice constants — accept() returns one of these.
    ALLOW = "allow"
    DENY = "deny"
    CUSTOM = "custom"

    def compose(self) -> ComposeResult:
        yield Static("⚠", id="confirm-prompt")
        yield Static("", id="confirm-diff")
        yield ListView(id="confirm-list")

    def visible(self) -> bool:
        return self.has_class("-visible")

    def show(self, tool_name: str, args_str: str, detail: str | None = None) -> None:
        """Populate options and reveal the menu. MAIN THREAD (call_from_thread).

        ``detail`` (P3-1): optional unified-diff preview for file-write tools,
        rendered with Rich's diff syntax highlighting above the options. The
        menu grows to fit (max-height bounds it)."""
        prompt = self.query_one("#confirm-prompt", Static)
        prompt.update(f"⚠ {tool_name}({args_str})")
        diff_w = self.query_one("#confirm-diff", Static)
        if detail:
            from rich.syntax import Syntax

            diff_w.update(Syntax(detail, "diff", theme="ansi-dark", word_wrap=True))
            self.add_class("-has-diff")
        else:
            diff_w.update("")
            self.remove_class("-has-diff")
        lv = self.query_one("#confirm-list", ListView)
        lv.clear()
        lv.append(ListItem(Static("✅ 允许执行"), name=self.ALLOW))
        lv.append(ListItem(Static("❌ 拒绝"), name=self.DENY))
        lv.append(ListItem(Static("✎ 自定义回复"), name=self.CUSTOM))
        self.add_class("-visible")
        try:
            lv.index = 0  # default-select the first (allow)
        except Exception:
            pass

    def hide(self) -> None:
        self.remove_class("-visible")
        self.remove_class("-has-diff")
        try:
            self.query_one("#confirm-list", ListView).clear()
        except Exception:
            pass

    def move(self, delta: int) -> None:
        """Move the selection by delta (+1 down, -1 up); wraps around."""
        if not self.visible():
            return
        lv = self.query_one("#confirm-list", ListView)
        if not lv.children:
            return
        n = len(lv.children)
        idx = lv.index or 0
        lv.index = (idx + delta) % n

    def accept(self) -> str | None:
        """Return the selected option's name, or None if nothing valid.

        Does NOT write to any Input — the caller (on_key) decides what to do
        with the returned choice.
        """
        if not self.visible():
            return None
        lv = self.query_one("#confirm-list", ListView)
        if not lv.children or lv.index is None:
            return None
        return lv.children[lv.index].name


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
        # Task-level phase (explore/plan/implement/verify/complete), orthogonal
        # to self.phase (which tracks model micro-activity: thinking/responding/tool).
        # Derived from harness ground truth via AgentStateTracker. Empty = unknown.
        self.task_phase: str = ""
        # Turn-level token consumption (input + output). Reset on step_start,
        # accumulated via add_usage, cleared on finish. 0 = not shown (idle).
        self.turn_tokens: int = 0
        self._app = None
        self._spin_frame = 0  # cycles through _SPINNER each heartbeat while active

    def on_mount(self) -> None:
        self._app = self.app
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

    def set_phase(
        self,
        phase: str,
        tool_name: str = "",
        step: int = 0,
        tool_index: int = 0,
        tool_total: int = 0,
    ) -> None:
        """Update the displayed phase (safe to call from ANY thread).

        Only mutates plain attributes (GIL-safe). Does NOT call refresh() — that
        is widget-state mutation requiring the main thread. The background
        heartbeat picks up the new phase within ~100ms and repaints.
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

    def set_task_phase(self, task_phase: str) -> None:
        """Update the task-level phase tag (explore/plan/implement/verify/...).

        Safe from any thread (plain attribute write, GIL-safe). Repainted by the
        heartbeat. Pass "" to clear.
        """
        self.task_phase = task_phase

    def set_turn_tokens(self, tokens: int) -> None:
        """Update the turn-level token count shown in the status bar.

        Safe from any thread (plain attribute write). Repainted by the heartbeat.
        Pass 0 to hide (idle state).
        """
        self.turn_tokens = tokens

    def render(self) -> RenderableType:
        # Build a phase label that shows WHERE in the task the agent is, so the
        # user can distinguish "still working, step 3" from "frozen". The step
        # number + tool index give concrete progress, not just a vague spinner.
        step_tag = f"步骤{self.step}" if self.step else ""
        if self.phase == "tool" and self.tool_total > 1:
            tool_tag = f"{self.tool_name}({self.tool_index + 1}/{self.tool_total})"
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
        if self.phase == "idle":
            return Text(labels["idle"], style="dim")
        elapsed = time.monotonic() - self.phase_start if self.phase_start else 0.0
        label = labels.get(self.phase, self.phase)
        # The spinner ANIMATES: each heartbeat advances _spin_frame.
        spin = self._SPINNER[self._spin_frame]
        # Build the Text by APPENDING separate spans. The braille spinner and the
        # CJK text are in different segments so Textual computes cell widths
        # independently (mixing them in one f-string miscalculates width and can
        # eat the adjacent CJK char). Separate spans + overflow='ellipsis' + no_wrap.
        parts = []
        if step_tag:
            parts.append(step_tag)
        # Task-level phase tag (e.g. [实现]) derived from harness ground truth.
        # Shown before the micro-activity label so the user sees both axes:
        # "步骤3 · [实现] 思考中 · 12.4s".
        if self.task_phase:
            task_label = _TASK_PHASE_LABELS.get(self.task_phase, self.task_phase)
            parts.append(f"[{task_label}]")
        parts.append(label)
        parts.append(f"{elapsed:.1f}s")
        # Turn-level token consumption (only while a turn is active).
        if self.turn_tokens > 0:
            parts.append(f"{self.turn_tokens} tok")
        body = " · ".join(parts)
        t = Text(no_wrap=True, overflow="ellipsis")
        t.append(spin + " ", style="bold cyan")
        t.append(body)
        return t
